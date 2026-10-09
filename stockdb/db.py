"""SQLite database: schema and small helpers."""

from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS listings (
    symbol              TEXT PRIMARY KEY,   -- Yahoo Finance symbol, e.g. AAPL, SAP.DE, CEZ.PR
    market              TEXT NOT NULL,      -- US | EU | CZ
    name                TEXT,
    exchange            TEXT,               -- Yahoo exchange code
    exchange_name       TEXT,
    sector              TEXT,
    industry            TEXT,
    country             TEXT,
    website             TEXT,
    currency            TEXT,               -- trading currency
    financial_currency  TEXT,               -- currency of the financial statements
    quote_type          TEXT,
    status              TEXT NOT NULL DEFAULT 'active',  -- active | excluded | duplicate | delisted
    status_reason       TEXT,
    status_changed      TEXT,
    first_seen          TEXT,
    last_seen           TEXT,               -- last time the symbol was in the universe
    last_fetch_ok       TEXT,
    last_fetch_error    TEXT,
    fail_count          INTEGER NOT NULL DEFAULT 0
);

-- Current metrics, one row per listing (overwritten every week). Money values are in the financial currency
-- unless the column name ends with _usd. Growth rates and ROE are fractions (0.15 = 15 %).
CREATE TABLE IF NOT EXISTS metrics (
    symbol               TEXT PRIMARY KEY REFERENCES listings(symbol),
    as_of                TEXT NOT NULL,     -- date the data was fetched
    price                REAL,
    market_cap           REAL,              -- trading currency
    market_cap_usd       REAL,
    pe_ttm               REAL,
    dividend_yield       REAL,
    avg_volume           REAL,
    revenue_ttm          REAL,
    net_income_ttm       REAL,
    rev_growth_1y        REAL,
    rev_growth_1y_basis  TEXT,
    rev_growth_5y        REAL,
    rev_growth_5y_years  INTEGER,           -- fiscal years of data used (5 = exact, fewer = extended)
    earn_growth_5y       REAL,
    earn_growth_5y_years INTEGER,
    roe                  REAL,
    debt_to_equity       REAL,
    fcf_ttm              REAL,
    fcf_ttm_usd          REAL,
    peg_5y               REAL,
    latest_fy_end        TEXT
);

-- Annual revenue and net income, kept only for the 5-year growth rates (at most the
-- last ANNUAL_FIGURES_KEEP_YEARS fiscal years of active listings). Yahoo reports the
-- last 4 years; older years come from SEC EDGAR (US) or from earlier weekly runs.
CREATE TABLE IF NOT EXISTS annual_figures (
    symbol      TEXT NOT NULL,
    fy_end      TEXT NOT NULL,   -- fiscal year end date
    revenue     REAL,
    net_income  REAL,
    source      TEXT NOT NULL,   -- yahoo | sec
    PRIMARY KEY (symbol, fy_end)
);

-- Current exchange rates used for the *_usd columns.
CREATE TABLE IF NOT EXISTS fx_rates (
    currency      TEXT PRIMARY KEY,
    usd_per_unit  REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS runs (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    finished       TEXT NOT NULL,
    universe_size  INTEGER,
    fetched_ok     INTEGER,
    fetched_failed INTEGER,
    notes          TEXT
);

CREATE TABLE IF NOT EXISTS meta (
    key    TEXT PRIMARY KEY,
    value  TEXT
);

CREATE INDEX IF NOT EXISTS idx_listings_market ON listings(market, status);
"""


def connect(path: str | Path) -> sqlite3.Connection:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(path))
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = OFF")
    con.executescript(SCHEMA)
    return con


def upsert(con: sqlite3.Connection, table: str, row: dict, key: str | tuple = "symbol") -> None:
    """INSERT ... ON CONFLICT DO UPDATE for the given columns."""
    keys = (key,) if isinstance(key, str) else key
    cols = list(row)
    updates = [c for c in cols if c not in keys]
    sql = (f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)}) "
           f"ON CONFLICT ({', '.join(keys)}) DO ")
    sql += ("UPDATE SET " + ", ".join(f"{c} = excluded.{c}" for c in updates)) if updates else "NOTHING"
    con.execute(sql, [row[c] for c in cols])


def get_meta(con, key: str, default=None):
    row = con.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row[0] if row else default


def set_meta(con, key: str, value) -> None:
    upsert(con, "meta", {"key": key, "value": None if value is None else str(value)}, key="key")
