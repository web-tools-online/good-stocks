"""Minimal Yahoo Finance client.

The HTTP session, cookie and crumb handling come from yfinance (``YfData``), which
keeps up with Yahoo's frequent changes. On top of it this module asks for exactly the
data the screener needs in two requests per ticker:

* ``quoteSummary`` - profile (name, sector, country), price, market cap, ROE, D/E ...
* ``fundamentals-timeseries`` - annual / quarterly / trailing statement items + PEG.

The ``parse_*`` functions are pure and unit tested against recorded response shapes.
"""

from __future__ import annotations

import datetime as dt
import logging
import random
import threading
import time

log = logging.getLogger(__name__)

QUOTE_SUMMARY_URL = "https://query2.finance.yahoo.com/v10/finance/quoteSummary/{symbol}"
TIMESERIES_URL = "https://query2.finance.yahoo.com/ws/fundamentals-timeseries/v1/finance/timeseries/{symbol}"
QUOTE_SUMMARY_MODULES = "price,summaryDetail,financialData,defaultKeyStatistics,assetProfile,quoteType"

# Yahoo timeseries key -> canonical item name stored in the database.
TIMESERIES_ITEMS = {
    "TotalRevenue": "revenue",
    "NetIncome": "net_income",
    "NetIncomeCommonStockholders": "net_income_common",
    "DilutedEPS": "eps_diluted",
    "FreeCashFlow": "fcf",
    "OperatingCashFlow": "ocf",
    "CapitalExpenditure": "capex",
    "StockholdersEquity": "equity",
    "TotalDebt": "total_debt",
}
TIMESERIES_REQUEST = {
    # prefix -> (period code stored in the DB, keys)
    "annual": ("A", ["TotalRevenue", "NetIncome", "NetIncomeCommonStockholders", "DilutedEPS",
                     "FreeCashFlow", "OperatingCashFlow", "CapitalExpenditure",
                     "StockholdersEquity", "TotalDebt"]),
    "quarterly": ("Q", ["TotalRevenue", "NetIncome", "StockholdersEquity", "TotalDebt"]),
    "trailing": ("T", ["TotalRevenue", "NetIncome", "NetIncomeCommonStockholders",
                       "FreeCashFlow", "OperatingCashFlow", "CapitalExpenditure"]),
}
PEG_KEY = "trailingPegRatio"


class NotFoundError(Exception):
    """Yahoo does not know the symbol (HTTP 404)."""


class FetchError(Exception):
    """Request failed after all retries."""


# --------------------------------------------------------------------------- parsing

def _num(value):
    """Return a float for Yahoo values that may be raw numbers, {"raw": x} or {}."""
    if isinstance(value, dict):
        value = value.get("raw")
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        if value != value or value in (float("inf"), float("-inf")):
            return None
        return float(value)
    return None


def _str(value):
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def parse_quote_summary(payload: dict) -> tuple[dict, dict]:
    """Return ``(profile, stats)`` from a quoteSummary response."""
    result = ((payload or {}).get("quoteSummary") or {}).get("result") or []
    if not result:
        raise NotFoundError("empty quoteSummary result")
    r = result[0]
    price = r.get("price") or {}
    detail = r.get("summaryDetail") or {}
    fin = r.get("financialData") or {}
    keystats = r.get("defaultKeyStatistics") or {}
    asset = r.get("assetProfile") or {}
    qtype = r.get("quoteType") or {}

    profile = {
        "name": _str(price.get("longName")) or _str(qtype.get("longName"))
                or _str(price.get("shortName")) or _str(qtype.get("shortName")),
        "sector": _str(asset.get("sector")),
        "industry": _str(asset.get("industry")),
        "country": _str(asset.get("country")),
        "website": _str(asset.get("website")),
        "exchange": _str(price.get("exchange")) or _str(qtype.get("exchange")),
        "exchange_name": _str(price.get("exchangeName")),
        "currency": _str(price.get("currency")) or _str(detail.get("currency")),
        "financial_currency": _str(fin.get("financialCurrency")),
        "quote_type": _str(price.get("quoteType")) or _str(qtype.get("quoteType")),
    }
    stats = {
        "price": _num(price.get("regularMarketPrice")) or _num(fin.get("currentPrice")),
        "market_cap": _num(price.get("marketCap")) or _num(detail.get("marketCap")),
        "pe_ttm": _num(detail.get("trailingPE")),
        "dividend_yield": _num(detail.get("dividendYield")),
        "avg_volume": _num(detail.get("averageVolume")),
        "roe": _num(fin.get("returnOnEquity")),
        "debt_to_equity_pct": _num(fin.get("debtToEquity")),
        "fcf_levered": _num(fin.get("freeCashflow")),
        "revenue_growth_q": _num(fin.get("revenueGrowth")),
        "earnings_growth_q": _num(fin.get("earningsGrowth")),
        "peg_ratio": _num(keystats.get("pegRatio")),
        "shares_outstanding": _num(keystats.get("sharesOutstanding")) or _num(keystats.get("impliedSharesOutstanding")),
        "market_time": _num(price.get("regularMarketTime")),
    }
    return profile, stats


def parse_timeseries(payload: dict) -> tuple[list[list], float | None]:
    """Return ``(series, peg)``.

    ``series`` is a list of ``[period, item, end_date, value]`` rows where period is
    ``A`` (annual), ``Q`` (quarterly) or ``T`` (trailing twelve months).
    ``peg`` is the latest "PEG ratio (5yr expected)" value, if any.
    """
    results = ((payload or {}).get("timeseries") or {}).get("result") or []
    prefixes = {prefix: code for prefix, (code, _keys) in TIMESERIES_REQUEST.items()}
    rows: list[list] = []
    peg = None
    peg_date = ""
    for res in results:
        types = (res.get("meta") or {}).get("type") or []
        if not types:
            continue
        key = types[0]
        points = [p for p in (res.get(key) or []) if p]
        if key == PEG_KEY:
            for p in points:
                value = _num(p.get("reportedValue"))
                date = p.get("asOfDate") or ""
                if value is not None and date >= peg_date:
                    peg, peg_date = value, date
            continue
        for prefix, code in prefixes.items():
            if key.startswith(prefix) and key[len(prefix):] in TIMESERIES_ITEMS:
                item = TIMESERIES_ITEMS[key[len(prefix):]]
                for p in points:
                    value = _num(p.get("reportedValue"))
                    date = p.get("asOfDate")
                    if value is not None and date:
                        rows.append([code, item, date, value])
                break
    return rows, peg


# --------------------------------------------------------------------------- network

class _RateLimiter:
    """Allow at most ``rate`` requests per second across all threads."""

    def __init__(self, rate: float):
        self.interval = 1.0 / rate if rate > 0 else 0.0
        self.lock = threading.Lock()
        self.next_time = 0.0

    def wait(self):
        if not self.interval:
            return
        with self.lock:
            now = time.monotonic()
            wait = self.next_time - now
            self.next_time = max(now, self.next_time) + self.interval
        if wait > 0:
            time.sleep(wait)


class YahooClient:
    def __init__(self, rate: float = 4.0, attempts: int = 5):
        # Imported lazily so that the pure parsing code can be used without yfinance.
        from yfinance.data import YfData

        self._data = YfData()
        self._limiter = _RateLimiter(rate)
        self.attempts = attempts

    def _get_json(self, url: str, params: dict | None = None) -> dict:
        from yfinance.exceptions import YFRateLimitError

        last = None
        for attempt in range(self.attempts):
            self._limiter.wait()
            status = None
            try:
                resp = self._data.get(url, params=params, timeout=30)
                status = resp.status_code
                if status == 200:
                    return resp.json()
                if status == 404:
                    raise NotFoundError(url)
                last = f"HTTP {status}"
            except NotFoundError:
                raise
            except YFRateLimitError:
                status, last = 429, "HTTP 429 (rate limited)"
            except Exception as exc:  # network error, invalid JSON ...
                last = f"{type(exc).__name__}: {exc}"
            if status is not None and status not in (401, 403, 429) and status < 500:
                break
            delay = (60 if status == 429 else 4) * (2 ** attempt) / 2 + random.uniform(0, 2)
            log.debug("retry %s in %.0fs (%s)", url, delay, last)
            time.sleep(min(delay, 300))
        raise FetchError(last or "unknown error")

    def quote_summary(self, symbol: str) -> tuple[dict, dict]:
        params = {
            "modules": QUOTE_SUMMARY_MODULES,
            "formatted": "false",
            "corsDomain": "finance.yahoo.com",
            "symbol": symbol,
            "lang": "en-US",
            "region": "US",
        }
        return parse_quote_summary(self._get_json(QUOTE_SUMMARY_URL.format(symbol=symbol), params))

    def timeseries(self, symbol: str) -> tuple[list[list], float | None]:
        types = [prefix + key for prefix, (_code, keys) in TIMESERIES_REQUEST.items() for key in keys]
        types.append(PEG_KEY)
        now = dt.datetime.now(dt.timezone.utc)
        params = {
            "symbol": symbol,
            "type": ",".join(types),
            # Yahoo returns at most ~4 years / 5 quarters whatever the start date is.
            "period1": int(dt.datetime(2016, 12, 31, tzinfo=dt.timezone.utc).timestamp()),
            "period2": int(now.timestamp()) + 86400,
        }
        return parse_timeseries(self._get_json(TIMESERIES_URL.format(symbol=symbol), params))

    def screen_exchange(self, exchange: str, region: str | None = None) -> list[dict]:
        """Return all equity quotes listed on a Yahoo exchange code (e.g. ``PAR``)."""
        import yfinance as yf
        from yfinance import EquityQuery

        base = [EquityQuery("eq", ["exchange", exchange])]
        if region:
            base.append(EquityQuery("eq", ["region", region.lower()]))
        query = EquityQuery("and", base) if len(base) > 1 else base[0]
        quotes, total = self._screen_all(yf, query)
        if total and total > len(quotes) and total > 9000:
            # Yahoo caps deep pagination: split the exchange by sector instead.
            from yfinance.const import EQUITY_SCREENER_EQ_MAP

            seen = {q.get("symbol") for q in quotes}
            for sector in sorted(EQUITY_SCREENER_EQ_MAP["sector"]):
                sub, _ = self._screen_all(yf, EquityQuery("and", base + [EquityQuery("eq", ["sector", sector])]))
                for q in sub:
                    if q.get("symbol") not in seen:
                        seen.add(q.get("symbol"))
                        quotes.append(q)
        return quotes

    def _screen_all(self, yf, query) -> tuple[list[dict], int]:
        quotes: list[dict] = []
        total = None
        offset = 0
        while True:
            page = None
            for attempt in range(self.attempts):
                self._limiter.wait()
                try:
                    page = yf.screen(query, offset=offset, size=250, sortField="ticker", sortAsc=True)
                    break
                except Exception as exc:
                    log.warning("screener %s offset %d failed: %s", query.to_dict(), offset, exc)
                    time.sleep(10 * (attempt + 1))
            if page is None:
                raise FetchError(f"screener failed at offset {offset}")
            batch = page.get("quotes") or []
            total = page.get("total", total)
            quotes.extend(batch)
            offset += len(batch)
            if not batch or len(batch) < 250 or (total is not None and offset >= total):
                break
        return quotes, total or 0
