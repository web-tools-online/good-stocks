"""Print the raw Yahoo Finance fields behind a ticker's per-share numbers (debugging aid).

``python -m stockdb inspect GMEX CYNCA.ST`` shows the dividend, split, share-count and
EPS fields from quoteSummary next to the company totals from the financial statements,
so per-share values that are out of line with the price can be spotted.
"""

from __future__ import annotations

import datetime as dt
import json

from .yahoo import QUOTE_SUMMARY_URL, TIMESERIES_URL, YahooClient

SUMMARY_FIELDS = {
    "price": ["currency", "regularMarketPrice", "marketCap", "exchangeName", "quoteType"],
    "summaryDetail": ["dividendRate", "dividendYield", "trailingAnnualDividendRate",
                      "trailingAnnualDividendYield", "fiveYearAvgDividendYield", "exDividendDate",
                      "payoutRatio", "trailingPE", "marketCap", "currency"],
    "defaultKeyStatistics": ["sharesOutstanding", "impliedSharesOutstanding", "floatShares", "trailingEps",
                             "lastSplitFactor", "lastSplitDate", "lastDividendValue", "lastDividendDate"],
    "financialData": ["financialCurrency", "currentPrice"],
}
TIMESERIES_KEYS = [
    "trailingCashDividendsPaid", "trailingCommonStockDividendPaid", "trailingNetIncome",
    "trailingDilutedEPS", "trailingBasicEPS",
    "annualCashDividendsPaid", "annualCommonStockDividendPaid", "annualNetIncome",
    "annualDilutedEPS", "annualDilutedAverageShares", "annualOrdinarySharesNumber",
    "quarterlyOrdinarySharesNumber", "quarterlyDilutedAverageShares",
]


def _date(v):
    if isinstance(v, (int, float)) and v > 1e8:
        return dt.datetime.fromtimestamp(v, dt.timezone.utc).date().isoformat()
    return v


def inspect_symbol(client: YahooClient, symbol: str) -> dict:
    out: dict = {"symbol": symbol}
    try:
        data = client._get_json(QUOTE_SUMMARY_URL.format(symbol=symbol), {
            "modules": ",".join(SUMMARY_FIELDS), "formatted": "false", "symbol": symbol,
            "corsDomain": "finance.yahoo.com", "lang": "en-US", "region": "US"})
        result = ((data.get("quoteSummary") or {}).get("result") or [{}])[0]
        for module, fields in SUMMARY_FIELDS.items():
            mod = result.get(module) or {}
            for f in fields:
                v = mod.get(f)
                if isinstance(v, dict):
                    v = v.get("raw")
                if v not in (None, {}):
                    out[f"{module}.{f}"] = _date(v) if "Date" in f else v
    except Exception as exc:
        out["quoteSummary_error"] = str(exc)
    try:
        now = dt.datetime.now(dt.timezone.utc)
        data = client._get_json(TIMESERIES_URL.format(symbol=symbol), {
            "symbol": symbol, "type": ",".join(TIMESERIES_KEYS),
            "period1": int(dt.datetime(2016, 12, 31, tzinfo=dt.timezone.utc).timestamp()),
            "period2": int(now.timestamp()) + 86400})
        for res in (data.get("timeseries") or {}).get("result") or []:
            key = ((res.get("meta") or {}).get("type") or [None])[0]
            points = [p for p in (res.get(key) or []) if p]
            if points:
                out[key] = {p.get("asOfDate"): (p.get("reportedValue") or {}).get("raw") for p in points}
                cur = points[-1].get("currencyCode")
                if cur:
                    out[f"{key}.currency"] = cur
    except Exception as exc:
        out["timeseries_error"] = str(exc)
    return out


def run_inspect(symbols: list[str]) -> list[dict]:
    client = YahooClient(rate=2.0)
    results = []
    for symbol in symbols:
        info = inspect_symbol(client, symbol)
        results.append(info)
        print(json.dumps(info, indent=1, sort_keys=True), flush=True)
    return results
