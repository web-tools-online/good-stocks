from stockdb.yahoo import parse_quote_summary, parse_timeseries, NotFoundError
import pytest

QUOTE_SUMMARY = {
    "quoteSummary": {
        "result": [{
            "price": {"longName": "Apple Inc.", "shortName": "Apple", "currency": "USD", "exchange": "NMS",
                      "exchangeName": "NasdaqGS", "quoteType": "EQUITY", "regularMarketPrice": 227.5,
                      "marketCap": 3.4e12, "regularMarketTime": 1760000000},
            "summaryDetail": {"trailingPE": 34.6, "dividendYield": 0.0044, "averageVolume": 55000000,
                              "currency": "USD", "marketCap": 3.4e12},
            "financialData": {"returnOnEquity": 1.5081, "debtToEquity": 154.49, "freeCashflow": 9.4e10,
                              "revenueGrowth": 0.096, "earningsGrowth": 0.12, "financialCurrency": "USD",
                              "currentPrice": 227.5},
            "defaultKeyStatistics": {"pegRatio": {}},
            "assetProfile": {"sector": "Technology", "industry": "Consumer Electronics",
                             "country": "United States", "website": "https://www.apple.com"},
            "quoteType": {"quoteType": "EQUITY", "exchange": "NMS", "longName": "Apple Inc."},
        }],
        "error": None,
    }
}


def test_parse_quote_summary():
    profile, stats = parse_quote_summary(QUOTE_SUMMARY)
    assert profile["name"] == "Apple Inc."
    assert profile["sector"] == "Technology"
    assert profile["country"] == "United States"
    assert profile["financial_currency"] == "USD"
    assert profile["quote_type"] == "EQUITY"
    assert stats["roe"] == pytest.approx(1.5081)
    assert stats["debt_to_equity_pct"] == pytest.approx(154.49)
    assert stats["market_cap"] == 3.4e12
    assert stats["peg_ratio"] is None  # {} means missing


def test_parse_quote_summary_not_found():
    with pytest.raises(NotFoundError):
        parse_quote_summary({"quoteSummary": {"result": None, "error": {"code": "Not Found"}}})


TIMESERIES = {
    "timeseries": {
        "result": [
            {"meta": {"symbol": ["AAPL"], "type": ["annualTotalRevenue"]}, "timestamp": [1, 2],
             "annualTotalRevenue": [
                 {"asOfDate": "2023-09-30", "periodType": "12M", "currencyCode": "USD",
                  "reportedValue": {"raw": 383285000000.0, "fmt": "383.29B"}},
                 None,
                 {"asOfDate": "2024-09-30", "periodType": "12M", "currencyCode": "USD",
                  "reportedValue": {"raw": 391035000000.0, "fmt": "391.04B"}}]},
            {"meta": {"symbol": ["AAPL"], "type": ["trailingFreeCashFlow"]}, "timestamp": [3],
             "trailingFreeCashFlow": [{"asOfDate": "2025-06-30", "periodType": "TTM",
                                       "reportedValue": {"raw": 96184000000.0}}]},
            {"meta": {"symbol": ["AAPL"], "type": ["quarterlyStockholdersEquity"]}},
            {"meta": {"symbol": ["AAPL"], "type": ["trailingPegRatio"]}, "timestamp": [4, 5],
             "trailingPegRatio": [{"asOfDate": "2025-03-31", "reportedValue": {"raw": 2.4}},
                                  {"asOfDate": "2025-06-30", "reportedValue": {"raw": 2.1}}]},
            {"meta": {"symbol": ["AAPL"], "type": ["annualSomethingElse"]}, "annualSomethingElse": [
                {"asOfDate": "2024-09-30", "reportedValue": {"raw": 1}}]},
        ],
        "error": None,
    }
}


def test_parse_timeseries():
    rows, peg = parse_timeseries(TIMESERIES)
    assert ["A", "revenue", "2023-09-30", 383285000000.0] in rows
    assert ["A", "revenue", "2024-09-30", 391035000000.0] in rows
    assert ["T", "fcf", "2025-06-30", 96184000000.0] in rows
    assert len(rows) == 3
    assert peg == 2.1


def test_parse_timeseries_empty():
    assert parse_timeseries({}) == ([], None)
