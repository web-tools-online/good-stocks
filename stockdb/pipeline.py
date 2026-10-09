"""The three steps of the weekly update: universe -> fetch (sharded) -> build."""

from __future__ import annotations

import datetime as dt
import glob
import gzip
import json
import logging
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from . import config, db, fx, metrics, sec
from .universe import build_universe

log = logging.getLogger(__name__)


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0)


def _iso(ts: dt.datetime) -> str:
    return ts.strftime("%Y-%m-%dT%H:%M:%SZ")


# --------------------------------------------------------------------------- universe

def run_universe(db_path: str, out_path: str, shards: int, limit: int = 0) -> list[int]:
    """Build the universe, decide what to fetch and split it into shards."""
    from .yahoo import YahooClient

    con = db.connect(db_path)
    client = YahooClient(rate=2.0)
    candidates = build_universe(client, limit=limit)

    now = _now()
    recheck_before = _iso(now - dt.timedelta(days=config.EXCLUSION_RECHECK_DAYS))
    known = {r["symbol"]: dict(r) for r in con.execute(
        "SELECT l.symbol, l.status, l.status_changed, m.market_cap_usd "
        "FROM listings l LEFT JOIN metrics m USING (symbol)")}

    to_fetch, skipped = [], 0
    for c in candidates:
        k = known.get(c["symbol"])
        if k and k["status"] in ("excluded", "duplicate") and (k["status_changed"] or "") > recheck_before:
            c["skip"] = True
            skipped += 1
        else:
            c["skip"] = False
            c["_mcap"] = (k or {}).get("market_cap_usd") or 0
            to_fetch.append(c)

    # Biggest companies first, so that a shard running out of time loses only small ones.
    to_fetch.sort(key=lambda c: -c.pop("_mcap"))
    for i, c in enumerate(to_fetch):
        c["shard"] = i % shards

    by_market = {m: sum(1 for c in candidates if c["market"] == m) for m in config.MARKETS}
    payload = {
        "generated": _iso(now),
        "limit": limit,
        "shards": shards,
        "market_counts": by_market,
        "listings": candidates,
    }
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_path).write_text(json.dumps(payload, separators=(",", ":")))
    log.info("universe: %d candidates %s, %d to fetch, %d skipped (excluded/duplicate)",
             len(candidates), by_market, len(to_fetch), skipped)
    return list(range(shards))


# --------------------------------------------------------------------------- fetch

def fetch_one(client, listing: dict) -> dict:
    from .yahoo import NotFoundError

    rec = {"symbol": listing["symbol"], "market": listing["market"], "fetched_at": _iso(_now())}
    try:
        profile, stats = client.quote_summary(listing["symbol"])
    except NotFoundError as exc:
        rec.update(status="not_found", error=str(exc)[:300])
        return rec
    except Exception as exc:
        rec.update(status="error", error=f"{type(exc).__name__}: {exc}"[:300])
        return rec
    rec.update(status="ok", profile=profile, stats=stats, series=[])
    if _is_eligible(listing["market"], profile):
        try:
            series, peg = client.timeseries(listing["symbol"])
            rec["series"] = series
            rec["stats"]["peg_5y"] = peg
        except Exception as exc:  # keep the profile and key statistics
            rec["error"] = f"timeseries: {type(exc).__name__}: {exc}"[:300]
    return rec


def listing_status(market: str, profile: dict) -> tuple[str, str | None]:
    """``("active", None)`` or ``("excluded", reason)`` for a fetched profile."""
    if (profile.get("quote_type") or "EQUITY") != "EQUITY":
        return "excluded", f"not a stock ({profile.get('quote_type')})"
    if market == config.MARKET_EU:
        country = profile.get("country")
        if country and country not in config.EU_COUNTRIES:
            return "excluded", f"company based outside the EU ({country})"
        reporting = profile.get("financial_currency") or profile.get("currency")
        if not country and reporting and reporting not in config.EU_CURRENCIES:
            return "excluded", f"company probably based outside the EU (reports in {reporting})"
    return "active", None


def _is_eligible(market: str, profile: dict) -> bool:
    return listing_status(market, profile)[0] == "active"


def run_fetch(universe_path: str, shard: int, out_path: str, workers: int = 4,
              rate: float = 4.0, max_minutes: float = 300) -> dict:
    from .yahoo import YahooClient

    universe = json.loads(Path(universe_path).read_text())
    todo = [c for c in universe["listings"] if not c.get("skip") and c.get("shard") == shard]
    log.info("shard %d: %d listings to fetch", shard, len(todo))

    client = YahooClient(rate=rate)
    deadline = time.monotonic() + max_minutes * 60
    lock = threading.Lock()
    counts = {"ok": 0, "not_found": 0, "error": 0}
    state = {"consecutive_errors": 0, "pauses": 0, "abort": False, "pause_until": 0.0}
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)

    def work(listing):
        while time.monotonic() < state["pause_until"]:
            time.sleep(5)
        if state["abort"] or time.monotonic() > deadline:
            return None
        return fetch_one(client, listing)

    with gzip.open(out_path, "wt", encoding="utf-8") as out, ThreadPoolExecutor(workers) as pool:
        futures = [pool.submit(work, c) for c in todo]
        for n, fut in enumerate(as_completed(futures), 1):
            rec = fut.result()
            if rec is None:
                continue
            with lock:
                out.write(json.dumps(rec, separators=(",", ":")) + "\n")
                counts[rec["status"]] += 1
                if rec["status"] == "error":
                    state["consecutive_errors"] += 1
                else:
                    state["consecutive_errors"] = 0
                    state["pauses"] = 0
                if state["consecutive_errors"] >= 25:
                    state["pauses"] += 1
                    state["consecutive_errors"] = 0
                    if state["pauses"] > 3:
                        log.error("too many consecutive failures - giving up on this shard")
                        state["abort"] = True
                    else:
                        log.warning("25 failures in a row (last: %s) - pausing 5 minutes", rec.get("error"))
                        state["pause_until"] = time.monotonic() + 300
            if n % 100 == 0:
                log.info("shard %d: %d/%d done %s", shard, n, len(todo), counts)
    skipped = len(todo) - sum(counts.values())
    log.info("shard %d finished: %s, not attempted: %d", shard, counts, skipped)
    return counts


# --------------------------------------------------------------------------- build

def _load_raw(patterns: list[str]) -> list[dict]:
    records = {}
    for pattern in patterns:
        for path in sorted(glob.glob(pattern)):
            try:
                with gzip.open(path, "rt", encoding="utf-8") as fh:
                    for line in fh:
                        line = line.strip()
                        if line:
                            rec = json.loads(line)
                            records[rec["symbol"]] = rec
            except (EOFError, OSError, json.JSONDecodeError) as exc:
                # A shard killed mid-write leaves a truncated file; keep what was read.
                log.warning("%s is incomplete (%s) - using the records read so far", path, exc)
    return list(records.values())


def _monday(d: dt.date) -> str:
    return (d - dt.timedelta(days=d.weekday())).isoformat()


def normalize_name(name: str | None) -> str:
    name = (name or "").lower()
    name = re.sub(r"[^a-z0-9 ]+", " ", name)
    return re.sub(r"\s+", " ", name).strip()


def run_build(db_path: str, universe_path: str, raw_patterns: list[str], use_sec: bool = True) -> dict:
    con = db.connect(db_path)
    universe = json.loads(Path(universe_path).read_text())
    now = _now()
    now_s, today = _iso(now), now.date()
    smoke = bool(universe.get("limit"))

    # 1. Listings seen in the universe ------------------------------------------------
    candidates = universe["listings"]
    for c in candidates:
        con.execute(
            "INSERT INTO listings (symbol, market, name, exchange, exchange_name, first_seen, last_seen) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT (symbol) DO UPDATE SET "
            "market = excluded.market, last_seen = excluded.last_seen, "
            "name = COALESCE(listings.name, excluded.name), "
            "exchange = COALESCE(listings.exchange, excluded.exchange), "
            "exchange_name = COALESCE(listings.exchange_name, excluded.exchange_name)",
            (c["symbol"], c["market"], c.get("name"), c.get("exchange"), c.get("exchange_name"), now_s, now_s))

    # Listings that disappeared from a (healthy) market universe are delisted.
    if not smoke:
        seen = {c["symbol"] for c in candidates}
        for market, count in universe.get("market_counts", {}).items():
            active = con.execute("SELECT symbol FROM listings WHERE market = ? AND status = 'active'",
                                 (market,)).fetchall()
            if count < 0.5 * len(active):
                log.warning("%s universe looks incomplete (%d vs %d active) - not delisting", market, count, len(active))
                continue
            for (symbol,) in active:
                if symbol not in seen:
                    con.execute("UPDATE listings SET status = 'delisted', status_reason = ?, status_changed = ? "
                                "WHERE symbol = ?", ("no longer listed", now_s, symbol))

    # 2. Raw fetch results ------------------------------------------------------------
    records = _load_raw(raw_patterns)
    counts = {"ok": 0, "not_found": 0, "error": 0}
    for rec in records:
        counts[rec["status"]] = counts.get(rec["status"], 0) + 1
        _apply_record(con, rec, now_s)
    log.info("applied %d fetch results: %s", len(records), counts)

    # 3. FX rates ---------------------------------------------------------------------
    rates = _update_fx(con, today)

    # 4. Longer history for US companies from SEC EDGAR ------------------------------
    if use_sec:
        try:
            _apply_sec(con, now_s)
        except Exception as exc:  # optional data source
            log.exception("SEC history failed: %s", exc)

    # 5. Metrics, weekly history, EU de-duplication -----------------------------------
    fetched_now = {r["symbol"] for r in records if r["status"] == "ok"}
    n_metrics = _recompute_metrics(con, rates, today, fetched_now)
    _dedupe_eu(con, rates, now_s)

    con.execute("INSERT INTO runs (finished, universe_size, fetched_ok, fetched_failed, notes) VALUES (?, ?, ?, ?, ?)",
                (now_s, len(candidates), counts.get("ok", 0),
                 counts.get("error", 0) + counts.get("not_found", 0), "smoke test" if smoke else None))
    db.set_meta(con, "last_update", now_s)
    con.commit()
    con.execute("VACUUM")
    con.close()
    summary = {"records": len(records), **counts, "metrics": n_metrics}
    log.info("build finished: %s", summary)
    return summary


def _apply_record(con, rec: dict, now_s: str) -> None:
    symbol, status = rec["symbol"], rec["status"]
    if status != "ok":
        con.execute(
            "UPDATE listings SET fail_count = fail_count + 1, last_fetch_error = ? WHERE symbol = ?",
            (rec.get("error"), symbol))
        if status == "not_found":
            con.execute(
                "UPDATE listings SET status = 'delisted', status_reason = 'not found on Yahoo Finance', "
                "status_changed = ? WHERE symbol = ? AND fail_count >= 3", (now_s, symbol))
        return

    profile = rec.get("profile") or {}
    current = con.execute("SELECT status FROM listings WHERE symbol = ?", (symbol,)).fetchone()
    new_status, reason = listing_status(rec["market"], profile)
    changed = current is None or current["status"] != new_status

    fields = {k: profile.get(k) for k in ("sector", "industry", "country", "website", "currency",
                                          "financial_currency", "quote_type")}
    for k in ("name", "exchange", "exchange_name"):
        if profile.get(k):
            fields[k] = profile[k]
    fields.update(status=new_status, status_reason=reason, last_fetch_ok=now_s,
                  last_fetch_error=rec.get("error"), fail_count=0)
    if changed or new_status != "active":
        fields["status_changed"] = now_s  # also restarts the re-check timer of excluded listings
    sets = ", ".join(f"{k} = ?" for k in fields)
    con.execute(f"UPDATE listings SET {sets} WHERE symbol = ?", [*fields.values(), symbol])

    if new_status != "active":
        return
    for period, item, end, value in rec.get("series") or []:
        con.execute(
            "INSERT INTO financials (symbol, period, end_date, item, value, source, updated) "
            "VALUES (?, ?, ?, ?, ?, 'yahoo', ?) ON CONFLICT (symbol, period, end_date, item) DO UPDATE SET "
            "value = excluded.value, source = 'yahoo', updated = excluded.updated",
            (symbol, period, end[:10], item, value, now_s))
    db.upsert(con, "yahoo_stats", {"symbol": symbol, "fetched_at": rec["fetched_at"],
                                   "stats_json": json.dumps(rec.get("stats") or {})})


def _update_fx(con, today: dt.date) -> dict[str, float]:
    date, rates = fx.fetch_ecb()
    currencies = {r[0] for r in con.execute(
        "SELECT currency FROM listings UNION SELECT financial_currency FROM listings") if r[0]}
    missing = {fx.MINOR_UNITS.get(c, (c,))[0] for c in currencies} - set(rates)
    if missing:
        try:
            from .yahoo import YahooClient

            rates.update({k: v for k, v in fx.fetch_yahoo(YahooClient(rate=2.0), missing).items() if k not in rates})
        except Exception as exc:
            log.warning("Yahoo FX fallback failed: %s", exc)
    for currency, rate in rates.items():
        db.upsert(con, "fx_rates", {"currency": currency, "date": date or today.isoformat(), "usd_per_unit": rate},
                  key=("currency", "date"))
    # Fill gaps with the most recent stored rate.
    for currency, rate in con.execute(
            "SELECT currency, usd_per_unit FROM fx_rates f WHERE date = "
            "(SELECT MAX(date) FROM fx_rates WHERE currency = f.currency)"):
        rates.setdefault(currency, rate)
    log.info("FX rates for %d currencies", len(rates))
    return rates


def _apply_sec(con, now_s: str) -> None:
    symbols = [r[0] for r in con.execute(
        "SELECT symbol FROM listings WHERE market = 'US' AND status = 'active'")]
    if not symbols:
        return
    history = sec.fetch_sec_history(symbols)
    added = 0
    for symbol, items in history.items():
        for item, sec_values in items.items():
            yahoo = {r[0]: r[1] for r in con.execute(
                "SELECT end_date, value FROM financials WHERE symbol = ? AND period = 'A' AND item = ? "
                "AND source = 'yahoo'", (symbol, item))}
            extra = sec.validated_extension(yahoo, sec_values)
            con.execute("DELETE FROM financials WHERE symbol = ? AND period = 'A' AND item = ? AND source = 'sec'",
                        (symbol, item))
            for end, value in extra.items():
                con.execute(
                    "INSERT OR IGNORE INTO financials (symbol, period, end_date, item, value, source, updated) "
                    "VALUES (?, 'A', ?, ?, ?, 'sec', ?)", (symbol, end, item, value, now_s))
                added += 1
    log.info("SEC: %d companies matched, %d older annual values added", len(history), added)


def _recompute_metrics(con, rates: dict, today: dt.date, fetched_now: set[str]) -> int:
    listings = {r["symbol"]: dict(r) for r in con.execute(
        "SELECT l.symbol, l.currency, l.financial_currency, s.fetched_at, s.stats_json "
        "FROM listings l JOIN yahoo_stats s USING (symbol) WHERE l.status = 'active'")}
    week = _monday(today)
    n = 0

    def flush(symbol, rows):
        nonlocal n
        info = listings.get(symbol)
        if info is None:
            return
        stats = json.loads(info["stats_json"])
        m = metrics.compute(metrics.history_from_rows(rows), stats, today)
        trade_rate = fx.usd_rate(rates, info["currency"])
        fin_rate = fx.usd_rate(rates, info["financial_currency"] or info["currency"])
        mcap = stats.get("market_cap")
        if mcap is None and stats.get("price") and stats.get("shares_outstanding"):
            mcap = stats["price"] * stats["shares_outstanding"]
        row = {
            "symbol": symbol,
            "as_of": info["fetched_at"][:10],
            "price": stats.get("price"),
            "market_cap": mcap,
            "market_cap_usd": mcap * trade_rate if mcap is not None and trade_rate else None,
            "pe_ttm": stats.get("pe_ttm"),
            "dividend_yield": stats.get("dividend_yield"),
            "avg_volume": stats.get("avg_volume"),
            **m,
            "fcf_ttm_usd": m["fcf_ttm"] * fin_rate if m["fcf_ttm"] is not None and fin_rate else None,
        }
        db.upsert(con, "metrics", row)
        if symbol in fetched_now:
            db.upsert(con, "history", {
                "symbol": symbol, "week": week, "price": row["price"], "market_cap_usd": row["market_cap_usd"],
                **{k: row[k] for k in ("rev_growth_1y", "rev_growth_5y", "earn_growth_5y", "roe",
                                       "debt_to_equity", "fcf_ttm_usd", "peg_5y")}}, key=("symbol", "week"))
        n += 1

    current, rows = None, []
    done = set()
    for r in con.execute("SELECT symbol, period, item, end_date, value FROM financials ORDER BY symbol"):
        if r[0] != current:
            if current is not None:
                flush(current, rows)
                done.add(current)
            current, rows = r[0], []
        rows.append((r[1], r[2], r[3], r[4]))
    if current is not None:
        flush(current, rows)
        done.add(current)
    for symbol in listings.keys() - done:  # listings without any statements
        flush(symbol, [])
    # Metrics of listings that are no longer active are removed.
    con.execute("DELETE FROM metrics WHERE symbol NOT IN (SELECT symbol FROM listings WHERE status = 'active')")
    return n


def _dedupe_eu(con, rates: dict, now_s: str) -> None:
    """Keep one listing per company among EU exchanges (prefer the home exchange)."""
    rows = [dict(r) for r in con.execute(
        "SELECT l.symbol, l.name, l.country, l.exchange, l.currency, m.price, m.avg_volume "
        "FROM listings l JOIN metrics m USING (symbol) WHERE l.market = 'EU' AND l.status = 'active'")]
    groups: dict[str, list[dict]] = {}
    for r in rows:
        key = normalize_name(r["name"])
        if key:
            groups.setdefault(key, []).append(r)
    removed = 0
    for group in groups.values():
        if len(group) < 2:
            continue

        def score(r):
            home = config.EXCHANGE_COUNTRY.get(r["exchange"] or "") == r["country"]
            rate = fx.usd_rate(rates, r["currency"]) or 0
            traded = (r["price"] or 0) * (r["avg_volume"] or 0) * rate
            return (home, traded)

        group.sort(key=score, reverse=True)
        keep = group[0]["symbol"]
        for r in group[1:]:
            con.execute("UPDATE listings SET status = 'duplicate', status_reason = ?, status_changed = ? "
                        "WHERE symbol = ?", (f"same company as {keep}", now_s, r["symbol"]))
            con.execute("DELETE FROM metrics WHERE symbol = ?", (r["symbol"],))
            con.execute("DELETE FROM history WHERE symbol = ?", (r["symbol"],))
            removed += 1
    log.info("EU de-duplication: %d secondary listings hidden", removed)
