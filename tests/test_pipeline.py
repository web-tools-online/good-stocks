"""End-to-end: universe.json + raw shard files -> SQLite -> exported site (no network)."""

import gzip
import json
import sqlite3
from pathlib import Path

import pytest

from stockdb import export, fx, pipeline

ROOT = Path(__file__).resolve().parents[1]


def _series(revenue, net_income, last_year=2025):
    rows = []
    for i, (rv, ni) in enumerate(zip(revenue, net_income)):
        year = last_year - len(revenue) + 1 + i
        rows.append(["A", "revenue", f"{year}-12-31", rv])
        rows.append(["A", "net_income", f"{year}-12-31", ni])
    rows += [
        ["T", "revenue", f"{last_year + 1}-06-30", revenue[-1] * 1.05],
        ["T", "fcf", f"{last_year + 1}-06-30", net_income[-1] * 0.9],
        ["Q", "equity", f"{last_year + 1}-06-30", net_income[-1] * 4],
        ["Q", "total_debt", f"{last_year + 1}-06-30", net_income[-1]],
    ]
    return rows


def _record(symbol, market, name, country, currency="USD", quote_type="EQUITY", exchange=None,
            series=None, stats=None, status="ok"):
    if status != "ok":
        return {"symbol": symbol, "market": market, "fetched_at": "2026-10-10T04:00:00Z",
                "status": status, "error": "boom"}
    base_stats = {"price": 100.0, "market_cap": 5e10, "pe_ttm": 20.0, "avg_volume": 1e6, "roe": 0.25,
                  "debt_to_equity_pct": 30.0, "revenue_growth_q": 0.1, "peg_5y": 1.2}
    base_stats.update(stats or {})
    return {
        "symbol": symbol, "market": market, "fetched_at": "2026-10-10T04:00:00Z", "status": "ok",
        "profile": {"name": name, "sector": "Technology", "industry": "Software", "country": country,
                    "exchange": exchange, "exchange_name": exchange, "currency": currency,
                    "financial_currency": currency, "quote_type": quote_type},
        "stats": base_stats,
        "series": series if series is not None else _series([100, 110, 130, 150], [10, 12, 15, 20]),
    }


@pytest.fixture
def workdir(tmp_path, monkeypatch):
    monkeypatch.setattr(fx, "fetch_ecb", lambda: ("2026-10-09", {"USD": 1.0, "EUR": 1.1, "CZK": 0.045}))
    listings = [
        {"symbol": "GOOD", "market": "US", "name": "Good Corp", "exchange_name": "NASDAQ"},
        {"symbol": "ETFX", "market": "US", "name": "Some ETF", "exchange_name": "NYSE Arca"},
        {"symbol": "FAIL", "market": "US", "name": "Failing Inc", "exchange_name": "NYSE"},
        {"symbol": "SAP.DE", "market": "EU", "name": "SAP SE", "exchange": "GER"},
        {"symbol": "AIR.PA", "market": "EU", "name": "Airbus SE", "exchange": "PAR"},
        {"symbol": "AIR.DE", "market": "EU", "name": "Airbus SE", "exchange": "GER"},
        {"symbol": "APC.DE", "market": "EU", "name": "Apple Inc.", "exchange": "GER"},
        {"symbol": "CEZ.PR", "market": "CZ", "name": "CEZ", "exchange": "PRA"},
        {"symbol": "01P.DE", "market": "EU", "name": "Medpace Holdings Inc", "exchange": "GER"},
        {"symbol": "CTPNV.PR", "market": "CZ", "name": "CTP N.V.", "exchange": "PRA"},
        {"symbol": "EMPTY.PR", "market": "CZ", "name": "Empty shell", "exchange": "PRA"},
    ]
    for i, l in enumerate(listings):
        l.update(skip=False, shard=i % 2)
    universe = {"generated": "2026-10-10T03:00:00Z", "limit": 0, "shards": 2,
                "market_counts": {"US": 3, "EU": 5, "CZ": 3}, "listings": listings}
    (tmp_path / "universe.json").write_text(json.dumps(universe))
    records = [
        _record("GOOD", "US", "Good Corp", "United States", exchange="NMS"),
        _record("ETFX", "US", "Some ETF", None, quote_type="ETF", series=[]),
        _record("FAIL", "US", "Failing Inc", None, status="error"),
        _record("SAP.DE", "EU", "SAP SE", "Germany", currency="EUR", exchange="GER"),
        _record("AIR.PA", "EU", "Airbus SE", "France", currency="EUR", exchange="PAR"),
        _record("AIR.DE", "EU", "Airbus SE", "France", currency="EUR", exchange="GER",
                stats={"avg_volume": 5e7}),
        _record("APC.DE", "EU", "Apple Inc.", "United States", currency="EUR", exchange="GER", series=[]),
        _record("CEZ.PR", "CZ", "CEZ, a. s.", "Czech Republic", currency="CZK", exchange="PRA",
                stats={"market_cap": 6e12}),
        _record("01P.DE", "EU", "Medpace Holdings Inc", None, currency="USD", exchange="GER"),
        _record("CTPNV.PR", "CZ", "CTP N.V.", "Netherlands", currency="EUR", exchange="PRA",
                stats={"market_cap": None, "price": 18.0, "shares_outstanding": 4.5e8}),
        _record("EMPTY.PR", "CZ", "Empty shell", None, currency="CZK", exchange="PRA", series=[],
                stats={"market_cap": None, "roe": None, "debt_to_equity_pct": None, "revenue_growth_q": None,
                       "peg_5y": None}),
    ]
    raw = tmp_path / "raw"
    raw.mkdir()
    for shard in (0, 1):
        with gzip.open(raw / f"raw-{shard}.jsonl.gz", "wt") as fh:
            for r in records[shard::2]:
                fh.write(json.dumps(r) + "\n")
    return tmp_path


def test_build_and_export(workdir):
    db_path = workdir / "stocks.db"
    summary = pipeline.run_build(str(db_path), str(workdir / "universe.json"),
                                 [str(workdir / "raw" / "*.jsonl.gz")])
    assert summary["ok"] == 10 and summary["error"] == 1

    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    status = {r["symbol"]: (r["status"], r["status_reason"]) for r in con.execute("SELECT * FROM listings")}
    assert status["GOOD"][0] == "active"
    assert status["ETFX"][0] == "excluded"
    assert status["APC.DE"] == ("excluded", "company based outside the EU (United States)")
    assert status["AIR.PA"][0] == "active"  # home exchange wins over higher volume
    assert status["AIR.DE"] == ("duplicate", "same company as AIR.PA")
    assert status["FAIL"][0] == "active"
    assert status["01P.DE"] == ("excluded", "company probably based outside the EU (reports in USD)")

    m = {r["symbol"]: dict(r) for r in con.execute("SELECT * FROM metrics")}
    assert set(m) == {"GOOD", "SAP.DE", "AIR.PA", "CEZ.PR", "CTPNV.PR", "EMPTY.PR"}
    assert m["CTPNV.PR"]["market_cap_usd"] == pytest.approx(18.0 * 4.5e8 * 1.1)
    good = m["GOOD"]
    assert good["rev_growth_5y_years"] == 3
    assert good["rev_growth_5y"] == pytest.approx(1.5 ** (5 / 3) - 1)
    assert good["roe"] == 0.25 and good["debt_to_equity"] == pytest.approx(0.3)
    assert good["fcf_ttm"] == pytest.approx(18)
    assert m["CEZ.PR"]["market_cap_usd"] == pytest.approx(6e12 * 0.045)
    assert m["SAP.DE"]["fcf_ttm_usd"] == pytest.approx(18 * 1.1)
    tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert not tables & {"history", "financials"}  # current snapshot only
    con.close()

    out = workdir / "site_out"
    summary_path = workdir / "summary.json"
    res = export.run_export(str(db_path), str(ROOT / "site"), str(out), str(summary_path))
    assert res["by_market"] == {"US": 1, "EU": 2, "CZ": 2}  # EMPTY.PR has no data -> not exported
    data = json.loads((out / "data" / "stocks.json").read_text())
    rows = [dict(zip(data["fields"], r)) for r in data["rows"]]
    assert rows[0]["s"] == "CEZ.PR"  # sorted by market cap (USD)
    assert data["fx"]["CZK"] == 0.045
    assert (out / "index.html").exists() and (out / "app.js").exists()
    csv_lines = (out / "data" / "stocks.csv").read_text().splitlines()
    assert csv_lines[0].startswith("Ticker,Company,Market")
    assert len(csv_lines) == 6
    assert json.loads(summary_path.read_text())["stocks"] == 5


def test_report(workdir, capsys):
    db_path = workdir / "stocks.db"
    pipeline.run_build(str(db_path), str(workdir / "universe.json"), [str(workdir / "raw" / "*.jsonl.gz")])
    export.print_report(str(db_path))
    out = capsys.readouterr().out
    assert "US - largest companies" in out and "GOOD" in out
    assert "company based outside the EU" in out


def test_export_without_database(tmp_path):
    res = export.run_export(None, str(ROOT / "site"), str(tmp_path / "out"))
    assert res["stocks"] == 0
    data = json.loads((tmp_path / "out" / "data" / "stocks.json").read_text())
    assert data["rows"] == [] and data["generated"] is None


def test_delisting_and_incomplete_universe(workdir):
    db_path = workdir / "stocks.db"
    pipeline.run_build(str(db_path), str(workdir / "universe.json"), [str(workdir / "raw" / "*.jsonl.gz")])
    universe = json.loads((workdir / "universe.json").read_text())
    # US list now misses FAIL -> delisted; EU list is nearly empty -> treated as a broken source.
    universe["listings"] = [l for l in universe["listings"] if l["symbol"] not in ("FAIL", "SAP.DE", "AIR.PA")]
    universe["market_counts"] = {"US": 2, "EU": 0, "CZ": 1}
    (workdir / "universe2.json").write_text(json.dumps(universe))
    pipeline.run_build(str(db_path), str(workdir / "universe2.json"), [])
    con = sqlite3.connect(db_path)
    status = dict(con.execute("SELECT symbol, status FROM listings"))
    assert status["FAIL"] == "delisted"
    assert status["SAP.DE"] == "active" and status["AIR.PA"] == "active"
