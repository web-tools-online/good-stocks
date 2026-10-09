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

-- Latest metrics, one row per listing. Money values are in the financial currency
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
    rev_growth_5y_years  INTEGER,           -- years of history actually used (5 = exact)
    earn_growth_5y       REAL,
    earn_growth_5y_years INTEGER,
    roe                  REAL,
    debt_to_equity       REAL,
    fcf_ttm              REAL,
    fcf_ttm_usd          REAL,
    peg_5y               REAL,
    latest_fy_end        TEXT
);

-- Latest raw key statistics from Yahoo (kept so metrics can be recomputed).
CREATE TABLE IF NOT EXISTS yahoo_stats (
    symbol      TEXT PRIMARY KEY REFERENCES listings(symbol),
    fetched_at  TEXT NOT NULL,
    stats_json  TEXT NOT NULL
);

-- Financial statement history, accumulated week after week (Yahoo only exposes the
-- last 4 fiscal years / 5 quarters, so the database grows a longer history over time).
CREATE TABLE IF NOT EXISTS financials (
    symbol    TEXT NOT NULL,
    period    TEXT NOT NULL,   -- A = annual, Q = quarterly, T = trailing twelve months
    end_date  TEXT NOT NULL,
    item      TEXT NOT NULL,   -- revenue, net_income, fcf, equity, total_debt, ...
    value     REAL NOT NULL,
    source    TEXT NOT NULL,   -- yahoo | sec
    updated   TEXT NOT NULL,
    PRIMARY KEY (symbol, period, end_date, item)
);

-- Weekly snapshot of the key metrics.
CREATE TABLE IF NOT EXISTS history (
    symbol          TEXT NOT NULL,
    week            TEXT NOT NULL,   -- Monday of the ISO week, YYYY-MM-DD
    price           REAL,
    market_cap_usd  REAL,
    rev_growth_1y   REAL,
    rev_growth_5y   REAL,
    earn_growth_5y  REAL,
    roe             REAL,
    debt_to_equity  REAL,
    fcf_ttm_usd     REAL,
    peg_5y          REAL,
    PRIMARY KEY (symbol, week)
);

CREATE TABLE IF NOT EXISTS fx_rates (
    currency      TEXT NOT NULL,
    date          TEXT NOT NULL,
    usd_per_unit  REAL NOT NULL,
    PRIMARY KEY (currency, date)
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
CREATE INDEX IF NOT EXISTS idx_history_week ON history(week);
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
