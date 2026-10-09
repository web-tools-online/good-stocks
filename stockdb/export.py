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
           "WHERE l.status = 'active' AND m.as_of >= ? "
           "AND COALESCE(m.market_cap_usd, m.revenue_ttm, m.rev_growth_1y, m.roe, m.fcf_ttm) IS NOT NULL "
           "ORDER BY m.market_cap_usd IS NULL, m.market_cap_usd DESC")
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
        fx = {r[0]: round(r[1], 8) for r in con.execute("SELECT currency, usd_per_unit FROM fx_rates")}
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


def print_report(db_path: str, top: int = 15) -> None:
    """Human-readable sample: status counts and the largest companies of each market."""
    con = db.connect(db_path)
    print("Listings by status:")
    for r in con.execute("SELECT market, status, COUNT(*) FROM listings GROUP BY 1, 2 ORDER BY 1, 2"):
        print(f"  {r[0]:3} {r[1]:10} {r[2]:6}")
    print("Most common exclusion reasons:")
    for r in con.execute("SELECT status_reason, COUNT(*) FROM listings WHERE status != 'active' "
                         "GROUP BY 1 ORDER BY 2 DESC LIMIT 8"):
        print(f"  {r[1]:6}  {r[0]}")
    print("Metric coverage (non-null share of active listings with metrics):")
    cols = ["rev_growth_1y", "earn_growth_5y", "rev_growth_5y", "roe", "debt_to_equity", "fcf_ttm", "peg_5y",
            "market_cap_usd"]
    for market in config.MARKETS:
        n = con.execute("SELECT COUNT(*) FROM metrics JOIN listings USING (symbol) WHERE market = ?",
                        (market,)).fetchone()[0]
        if not n:
            continue
        parts = []
        for c in cols:
            k = con.execute(f"SELECT COUNT({c}) FROM metrics JOIN listings USING (symbol) WHERE market = ?",
                            (market,)).fetchone()[0]
            parts.append(f"{c}={100 * k // n}%")
        exact = con.execute("SELECT COUNT(*) FROM metrics JOIN listings USING (symbol) WHERE market = ? "
                            "AND rev_growth_5y_years = 5", (market,)).fetchone()[0]
        print(f"  {market} ({n}): {' '.join(parts)} exact5y={100 * exact // n}%")

    def pct(v):
        return "" if v is None else f"{v * 100:.1f}"

    def num(v, d=2):
        return "" if v is None else f"{v:.{d}f}"

    header = (f"{'symbol':10} {'name':26} {'country':14} {'mcap$bn':>8} {'g1%':>6} {'b':6} {'e5%':>7} {'y':>1} "
              f"{'g5%':>7} {'y':>1} {'roe%':>6} {'d/e':>5} {'fcf$bn':>7} {'peg':>5}")
    for market in config.MARKETS:
        rows = con.execute(
            "SELECT l.symbol, l.name, l.country, m.* FROM listings l JOIN metrics m USING (symbol) "
            "WHERE l.market = ? ORDER BY m.market_cap_usd IS NULL, m.market_cap_usd DESC LIMIT ?",
            (market, top)).fetchall()
        if not rows:
            continue
        print(f"\n{market} - largest companies")
        print(header)
        for r in rows:
            print(f"{r['symbol'][:10]:10} {(r['name'] or '')[:26]:26} {(r['country'] or '')[:14]:14} "
                  f"{num((r['market_cap_usd'] or 0) / 1e9, 1):>8} {pct(r['rev_growth_1y']):>6} "
                  f"{(r['rev_growth_1y_basis'] or ''):6} {pct(r['earn_growth_5y']):>7} {r['earn_growth_5y_years'] or '':>1} "
                  f"{pct(r['rev_growth_5y']):>7} {r['rev_growth_5y_years'] or '':>1} {pct(r['roe']):>6} "
                  f"{num(r['debt_to_equity']):>5} {num((r['fcf_ttm_usd'] or 0) / 1e9):>7} {num(r['peg_5y']):>5}")
    con.close()
