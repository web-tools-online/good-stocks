"""Build the list of tickers to fetch: all US, EU and Czech listed stocks."""

from __future__ import annotations

import logging
import re
import time

from . import config

log = logging.getLogger(__name__)

# Securities in the Nasdaq Trader directory that are not ordinary shares.
_EXCLUDE_NAME = re.compile(
    r"\b(warrants?|rights?|units?|preferred|preference|notes?|debentures?|bonds?|"
    r"etf|etn|exchange[- ]traded|fund|trust preferred|subordinated|due \d{4})\b|%",
    re.IGNORECASE,
)
_SYMBOL_OK = re.compile(r"^[A-Z0-9]+(\.[A-Z])?$")
# Bonds and certificates show up on some exchanges under their ISIN, e.g. CZ0003527690.PR
_ISIN_LIKE = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$")


def to_yahoo_symbol(symbol: str) -> str:
    """Nasdaq Trader uses ``BRK.B``; Yahoo uses ``BRK-B``."""
    return symbol.replace(".", "-")


def parse_nasdaq_directory(text: str, kind: str) -> list[dict]:
    """Parse ``nasdaqlisted.txt`` (kind="nasdaq") or ``otherlisted.txt`` (kind="other")."""
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        return []
    header = [h.strip() for h in lines[0].split("|")]
    out = []
    exchange_names = {"A": "NYSE American", "N": "NYSE", "P": "NYSE Arca", "Z": "Cboe BZX", "V": "IEX"}
    for line in lines[1:]:
        if line.startswith("File Creation Time"):
            continue
        row = dict(zip(header, (c.strip() for c in line.split("|"))))
        symbol = row.get("Symbol") if kind == "nasdaq" else row.get("ACT Symbol")
        name = row.get("Security Name", "")
        if not symbol or row.get("ETF") == "Y" or row.get("Test Issue") == "Y":
            continue
        if not _SYMBOL_OK.match(symbol) or _EXCLUDE_NAME.search(name):
            continue
        if kind == "nasdaq":
            exchange = "NASDAQ"
        else:
            exchange = exchange_names.get(row.get("Exchange", ""), row.get("Exchange", ""))
        out.append({
            "symbol": to_yahoo_symbol(symbol),
            "market": config.MARKET_US,
            "name": _clean_security_name(name),
            "exchange_name": exchange,
        })
    return out


def _clean_security_name(name: str) -> str:
    # "Apple Inc. - Common Stock" -> "Apple Inc."
    return re.split(r"\s+-\s+", name, maxsplit=1)[0].strip()


def _http_get_text(url: str) -> str:
    from curl_cffi import requests

    last = None
    for attempt in range(4):
        try:
            resp = requests.get(url, impersonate="chrome", timeout=60)
            if resp.status_code == 200:
                return resp.text
            last = f"HTTP {resp.status_code}"
        except Exception as exc:
            last = str(exc)
        time.sleep(5 * (attempt + 1))
    raise RuntimeError(f"GET {url} failed: {last}")


def us_listings() -> list[dict]:
    rows = parse_nasdaq_directory(_http_get_text(config.NASDAQ_LISTED_URL), "nasdaq")
    rows += parse_nasdaq_directory(_http_get_text(config.OTHER_LISTED_URL), "other")
    seen, out = set(), []
    for r in rows:
        if r["symbol"] not in seen:
            seen.add(r["symbol"])
            out.append(r)
    return out


def screener_listings(client, market: str, exchanges: dict[str, list[str]],
                      max_age_days: int = 30) -> list[dict]:
    """All equities on the given Yahoo exchanges that traded in the last ``max_age_days``."""
    cutoff = time.time() - max_age_days * 86400
    out, seen = [], set()
    for region, codes in exchanges.items():
        for code in codes:
            try:
                quotes = client.screen_exchange(code, region)
            except Exception as exc:  # one exchange failing must not kill the run
                log.error("screener for %s failed: %s", code, exc)
                continue
            kept = 0
            for q in quotes:
                symbol = q.get("symbol")
                if not symbol or symbol in seen or q.get("quoteType", "EQUITY") != "EQUITY":
                    continue
                if _ISIN_LIKE.match(symbol.split(".")[0]):
                    continue
                traded = q.get("regularMarketTime")
                if isinstance(traded, (int, float)) and traded < cutoff:
                    continue
                seen.add(symbol)
                kept += 1
                out.append({
                    "symbol": symbol,
                    "market": market,
                    "name": q.get("longName") or q.get("shortName"),
                    "exchange": code,
                    "exchange_name": q.get("fullExchangeName"),
                })
            log.info("%s %s: %d quotes, %d kept", market, code, len(quotes), kept)
    return out


def build_universe(client, markets=config.MARKETS, limit: int = 0) -> list[dict]:
    """Return ``[{symbol, market, name, exchange, exchange_name}]`` for all markets.

    ``limit`` > 0 keeps only the first N listings per market (used for smoke tests).
    """
    universe: list[dict] = []
    if config.MARKET_US in markets:
        us = us_listings()
        log.info("US: %d listings", len(us))
        universe += us[:limit] if limit else us
    if config.MARKET_EU in markets:
        eu = screener_listings(client, config.MARKET_EU, config.EU_EXCHANGES)
        log.info("EU: %d listings", len(eu))
        universe += _sample_per_exchange(eu, limit) if limit else eu
    if config.MARKET_CZ in markets:
        cz = screener_listings(client, config.MARKET_CZ, config.CZ_EXCHANGES)
        have = {r["symbol"] for r in cz}
        cz += [{"symbol": s, "market": config.MARKET_CZ, "name": None, "exchange": "PRA",
                "exchange_name": "Prague"} for s in config.CZ_SEED_SYMBOLS if s not in have]
        log.info("CZ: %d listings", len(cz))
        universe += cz[:limit] if limit else cz
    return universe


def _sample_per_exchange(rows: list[dict], limit: int) -> list[dict]:
    """Take listings round-robin across exchanges so a small sample covers all of them."""
    by_exchange: dict[str, list[dict]] = {}
    for r in rows:
        by_exchange.setdefault(r.get("exchange") or "", []).append(r)
    out: list[dict] = []
    while len(out) < limit and any(by_exchange.values()):
        for rs in by_exchange.values():
            if rs and len(out) < limit:
                out.append(rs.pop(0))
    return out
