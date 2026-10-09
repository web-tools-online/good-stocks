"""Export the database to the static website (JSON + CSV next to the HTML)."""

from __future__ import annotations

import csv
import datetime as dt
import json
import logging
import os
import shutil
from pathlib import Path

from . import config, db

log = logging.getLogger(__name__)

# (json key, sql expression, rounding) - order defines the compact row layout.
FIELDS = [
    ("s", "l.symbol", None),
    ("n", "l.name", None),
    ("m", "l.market", None),
    ("x", "COALESCE(l.exchange_name, l.exchange)", None),
    ("c", "l.country", None),
    ("sec", "l.sector", None),
    ("ind", "l.industry", None),
    ("cur", "l.currency", None),
    ("fcur", "COALESCE(l.financial_currency, l.currency)", None),
    ("p", "m.price", 4),
    ("mc", "m.market_cap_usd", 0),
    ("pe", "m.pe_ttm", 2),
    ("dy", "m.dividend_yield", 4),
    ("g1", "m.rev_growth_1y", 4),
    ("g1b", "m.rev_growth_1y_basis", None),
    ("g5", "m.rev_growth_5y", 4),
    ("g5y", "m.rev_growth_5y_years", None),
    ("e5", "m.earn_growth_5y", 4),
    ("e5y", "m.earn_growth_5y_years", None),
    ("roe", "m.roe", 4),
    ("de", "m.debt_to_equity", 3),
    ("fcf", "m.fcf_ttm", 0),
    ("fcfu", "m.fcf_ttm_usd", 0),
    ("peg", "m.peg_5y", 2),
    ("asof", "m.as_of", None),
]

CSV_COLUMNS = [
    ("Ticker", "s"), ("Company", "n"), ("Market", "m"), ("Exchange", "x"), ("Country", "c"),
    ("Sector", "sec"), ("Industry", "ind"), ("Currency", "cur"), ("Price", "p"),
    ("Market Cap (USD)", "mc"), ("P/E (TTM)", "pe"), ("Dividend Yield", "dy"),
    ("Revenue Growth (1Y, TTM)", "g1"), ("Earnings Growth (5Y)", "e5"), ("Years of earnings history", "e5y"),
    ("Revenue Growth (5Y)", "g5"), ("Years of revenue history", "g5y"), ("ROE", "roe"),
    ("Debt to Equity", "de"), ("Free Cash Flow (TTM)", "fcf"), ("Financial Currency", "fcur"),
    ("Free Cash Flow (TTM, USD)", "fcfu"), ("PEG (5Y expected)", "peg"), ("Data as of", "asof"),
]


def _round(value, digits):
    if value is None or digits is None or not isinstance(value, float):
        return value
    if digits == 0:
        return int(round(value))
    return round(value, digits)


def query_rows(con, max_stale_days: int = config.MAX_STALE_DAYS) -> list[list]:
    cutoff = (dt.date.today() - dt.timedelta(days=max_stale_days)).isoformat()
    sql = (f"SELECT {', '.join(expr for _, expr, _ in FIELDS)} FROM listings l JOIN metrics m USING (symbol) "
           "WHERE l.status = 'active' AND m.as_of >= ? ORDER BY m.market_cap_usd IS NULL, m.market_cap_usd DESC")
    rows = []
    for r in con.execute(sql, (cutoff,)):
        rows.append([_round(v, digits) for v, (_, _, digits) in zip(r, FIELDS)])
    return rows


def _db_url() -> str | None:
    repo = os.environ.get("GITHUB_REPOSITORY")
    return f"https://github.com/{repo}/releases/download/data/stocks.db" if repo else None


def run_export(db_path: str | None, site_dir: str, out_dir: str, summary_path: str | None = None) -> dict:
    out = Path(out_dir)
    if out.exists():
        shutil.rmtree(out)
    shutil.copytree(site_dir, out)
    data_dir = out / "data"
    data_dir.mkdir(exist_ok=True)

    rows, fx, generated, last_run = [], {}, None, None
    if db_path and Path(db_path).exists():
        con = db.connect(db_path)
        rows = query_rows(con)
        generated = db.get_meta(con, "last_update")
        fx = {r[0]: round(r[1], 8) for r in con.execute(
            "SELECT currency, usd_per_unit FROM fx_rates f WHERE date = "
            "(SELECT MAX(date) FROM fx_rates WHERE currency = f.currency)")}
        run = con.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 1").fetchone()
        last_run = dict(run) if run else None
        con.close()

    keys = [k for k, _, _ in FIELDS]
    payload = {"generated": generated, "db_url": _db_url(), "fx": fx, "fields": keys, "rows": rows}
    (data_dir / "stocks.json").write_text(json.dumps(payload, separators=(",", ":"), ensure_ascii=False))

    idx = {k: i for i, k in enumerate(keys)}
    with open(data_dir / "stocks.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow([h for h, _ in CSV_COLUMNS])
        for r in rows:
            w.writerow(["" if r[idx[k]] is None else r[idx[k]] for _, k in CSV_COLUMNS])

    summary = {
        "generated": generated,
        "stocks": len(rows),
        "by_market": {m: sum(1 for r in rows if r[idx["m"]] == m) for m in config.MARKETS},
        "last_run": last_run,
    }
    (data_dir / "summary.json").write_text(json.dumps(summary, indent=2))
    if summary_path:
        Path(summary_path).parent.mkdir(parents=True, exist_ok=True)
        Path(summary_path).write_text(json.dumps(summary, indent=2) + "\n")
    log.info("exported %d stocks to %s", len(rows), out)
    return summary
