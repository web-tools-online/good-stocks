import sqlite3

from stockdb import db


def test_old_database_gets_new_columns(tmp_path):
    path = tmp_path / "old.db"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE metrics (symbol TEXT PRIMARY KEY, as_of TEXT NOT NULL, pe_ttm REAL, dividend_yield REAL)")
    con.execute("INSERT INTO metrics VALUES ('AAPL', '2026-10-09', 39.0, 0.0032)")
    con.commit()
    con.close()

    con = db.connect(path)
    cols = {r[1] for r in con.execute("PRAGMA table_info(metrics)")}
    assert {"pe_basis", "dividend_basis", "rev_growth_4y", "market_cap_usd"} <= cols
    assert tuple(con.execute("SELECT pe_ttm, dividend_yield FROM metrics").fetchone()) == (39.0, 0.0032)
    db.upsert(con, "metrics", {"symbol": "AAPL", "as_of": "2026-10-10", "pe_basis": "yahoo", "dividend_basis": "yahoo"})
    assert con.execute("SELECT pe_basis FROM metrics").fetchone()[0] == "yahoo"
