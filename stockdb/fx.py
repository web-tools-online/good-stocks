"""Exchange rates to USD (ECB reference rates, Yahoo as a fallback)."""

from __future__ import annotations

import logging
import re

from . import config

log = logging.getLogger(__name__)

# Quote currencies expressed in minor units.
MINOR_UNITS = {"GBp": ("GBP", 100.0), "GBX": ("GBP", 100.0), "ZAc": ("ZAR", 100.0), "ILA": ("ILS", 100.0)}


def parse_ecb_xml(xml: str) -> tuple[str | None, dict[str, float]]:
    """Return ``(date, {currency: usd_per_unit})`` from the ECB daily XML."""
    date = None
    m = re.search(r"time=['\"](\d{4}-\d{2}-\d{2})['\"]", xml)
    if m:
        date = m.group(1)
    per_eur = {c: float(r) for c, r in re.findall(r"currency=['\"]([A-Z]{3})['\"]\s+rate=['\"]([\d.]+)['\"]", xml)}
    if "USD" not in per_eur:
        return date, {}
    usd_per_eur = per_eur["USD"]
    rates = {c: usd_per_eur / r for c, r in per_eur.items() if r > 0}
    rates["EUR"] = usd_per_eur
    rates["USD"] = 1.0
    return date, rates


def fetch_ecb() -> tuple[str | None, dict[str, float]]:
    import requests

    try:
        resp = requests.get(config.ECB_RATES_URL, timeout=30)
        resp.raise_for_status()
        return parse_ecb_xml(resp.text)
    except Exception as exc:
        log.warning("ECB rates unavailable: %s", exc)
        return None, {}


def fetch_yahoo(client, currencies) -> dict[str, float]:
    symbols = [f"{c}USD=X" for c in sorted(currencies) if c and c != "USD" and len(c) == 3]
    rates = {}
    for i in range(0, len(symbols), 50):
        try:
            data = client._get_json("https://query1.finance.yahoo.com/v7/finance/quote",
                                    {"symbols": ",".join(symbols[i:i + 50])})
        except Exception as exc:
            log.warning("Yahoo FX quotes unavailable: %s", exc)
            continue
        for q in (data.get("quoteResponse") or {}).get("result") or []:
            price = q.get("regularMarketPrice")
            if isinstance(price, (int, float)) and price > 0:
                rates[q["symbol"][:3]] = float(price)
    return rates


def usd_rate(rates: dict[str, float], currency: str | None):
    """USD per one unit of ``currency`` (handles GBp and other minor units)."""
    if not currency:
        return None
    if currency in MINOR_UNITS:
        major, divisor = MINOR_UNITS[currency]
        rate = rates.get(major)
        return None if rate is None else rate / divisor
    return rates.get(currency.upper())
