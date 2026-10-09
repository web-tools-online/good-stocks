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

from . import config, db, fx, metrics
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
            series, peg, currency = client.timeseries(listing["symbol"])
            rec["series"] = series
            rec["stats"]["peg_5y"] = peg
            if currency and not profile.get("financial_currency"):
                profile["financial_currency"] = currency
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


def normalize_name(name: str | None) -> str:
    name = (name or "").lower()
    name = re.sub(r"[^a-z0-9 ]+", " ", name)
    return re.sub(r"\s+", " ", name).strip()


def run_build(db_path: str, universe_path: str, raw_patterns: list[str]) -> dict:
    """Store the current snapshot: listings and their latest metrics (no history is kept)."""
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

    # 2. Current exchange rates ---------------------------------------------------------
    records = _load_raw(raw_patterns)
    currencies = {r[0] for r in con.execute(
        "SELECT currency FROM listings UNION SELECT financial_currency FROM listings") if r[0]}
    for rec in records:
        profile = rec.get("profile") or {}
        currencies.update(c for c in (profile.get("currency"), profile.get("financial_currency")) if c)
    rates = _update_fx(con, currencies)

    # 3. Fetch results -> listing profile ----------------------------------------------
    counts = {"ok": 0, "not_found": 0, "error": 0}
    active = []
    for rec in records:
        counts[rec["status"]] = counts.get(rec["status"], 0) + 1
        if _apply_record(con, rec, now_s):
            active.append(rec)
    log.info("applied %d fetch results: %s", len(records), counts)

    # 4. Current metrics ------------------------------------------------------------------
    n_metrics = sum(_store_metrics(con, rec, rates, today) for rec in active)

    # Listings that failed this week keep last week's metrics (hidden after MAX_STALE_DAYS).
    con.execute("DELETE FROM metrics WHERE symbol NOT IN (SELECT symbol FROM listings WHERE status = 'active')")
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


def _apply_record(con, rec: dict, now_s: str) -> bool:
    """Update the listing from one fetch result; True if it is an active stock."""
    symbol, status = rec["symbol"], rec["status"]
    if status != "ok":
        con.execute(
            "UPDATE listings SET fail_count = fail_count + 1, last_fetch_error = ? WHERE symbol = ?",
            (rec.get("error"), symbol))
        if status == "not_found":
            con.execute(
                "UPDATE listings SET status = 'delisted', status_reason = 'not found on Yahoo Finance', "
                "status_changed = ? WHERE symbol = ? AND fail_count >= 3", (now_s, symbol))
        return False

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
    return new_status == "active"


def _store_metrics(con, rec: dict, rates: dict, today: dt.date) -> int:
    """Compute and store the current metrics of one active listing."""
    symbol = rec["symbol"]
    profile = rec.get("profile") or {}
    stats = rec.get("stats") or {}
    rows = [(period, item, end, value) for period, item, end, value in rec.get("series") or []]
    m = metrics.compute(metrics.statements_from_rows(rows), stats, today)
    trade_rate = fx.usd_rate(rates, profile.get("currency"))
    fin_rate = fx.usd_rate(rates, profile.get("financial_currency") or profile.get("currency"))
    mcap = stats.get("market_cap")
    if mcap is None and stats.get("price") and stats.get("shares_outstanding"):
        mcap = stats["price"] * stats["shares_outstanding"]
    db.upsert(con, "metrics", {
        "symbol": symbol,
        "as_of": rec["fetched_at"][:10],
        "price": stats.get("price"),
        "market_cap": mcap,
        "market_cap_usd": mcap * trade_rate if mcap is not None and trade_rate else None,
        "pe_ttm": stats.get("pe_ttm"),
        "dividend_yield": stats.get("dividend_yield"),
        "avg_volume": stats.get("avg_volume"),
        **m,
        "fcf_ttm_usd": m["fcf_ttm"] * fin_rate if m["fcf_ttm"] is not None and fin_rate else None,
    })
    return 1


def _update_fx(con, currencies: set[str]) -> dict[str, float]:
    """Current USD rates (ECB, Yahoo for the rest); falls back to the last stored rates."""
    _date, rates = fx.fetch_ecb()
    missing = {fx.MINOR_UNITS.get(c, (c,))[0] for c in currencies} - set(rates)
    if missing:
        try:
            from .yahoo import YahooClient

            rates.update({k: v for k, v in fx.fetch_yahoo(YahooClient(rate=2.0), missing).items() if k not in rates})
        except Exception as exc:
            log.warning("Yahoo FX fallback failed: %s", exc)
    for currency, rate in con.execute("SELECT currency, usd_per_unit FROM fx_rates"):
        rates.setdefault(currency, rate)
    for currency, rate in rates.items():
        db.upsert(con, "fx_rates", {"currency": currency, "usd_per_unit": rate}, key="currency")
    log.info("FX rates for %d currencies", len(rates))
    return rates


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
            removed += 1
    log.info("EU de-duplication: %d secondary listings hidden", removed)
