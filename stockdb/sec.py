"""Long annual revenue / net income history for US filers from SEC EDGAR XBRL frames.

Yahoo only exposes the last four fiscal years, which is not enough for a 5-year growth
figure. SEC "frames" return one value per company for a concept and calendar year, so
~60 small requests cover every US filer for the last eight years.

SEC values are only used for older years and only when they agree with the years Yahoo
also reports, which protects against picking the wrong XBRL concept for a company.
"""

from __future__ import annotations

import datetime as dt
import logging
import os
import time

from . import config

log = logging.getLogger(__name__)

DEFAULT_USER_AGENT = "good-stocks screener (https://github.com/web-tools-online/good-stocks)"


def _session():
    import requests

    s = requests.Session()
    s.headers["User-Agent"] = os.environ.get("SEC_USER_AGENT") or DEFAULT_USER_AGENT
    s.headers["Accept-Encoding"] = "gzip, deflate"
    return s


def _get_json(session, url: str):
    for attempt in range(4):
        try:
            resp = session.get(url, timeout=60)
            if resp.status_code == 404:
                return None
            if resp.status_code == 200:
                return resp.json()
            log.warning("SEC %s -> HTTP %s", url, resp.status_code)
        except Exception as exc:
            log.warning("SEC %s failed: %s", url, exc)
        time.sleep(2 * (attempt + 1))
    return None


def fetch_ticker_ciks(session) -> dict[str, int]:
    data = _get_json(session, config.SEC_TICKERS_URL) or {}
    return {str(v["ticker"]).upper(): int(v["cik_str"]) for v in data.values()}


def parse_frame(payload: dict | None) -> dict[int, tuple[str, float]]:
    """``{cik: (period_end, value)}`` from a frames response."""
    out = {}
    for row in (payload or {}).get("data") or []:
        try:
            out[int(row["cik"])] = (row["end"], float(row["val"]))
        except (KeyError, TypeError, ValueError):
            continue
    return out


def combine_frames(frames_by_tag: list[dict[int, dict[int, tuple[str, float]]]]) -> dict[int, dict[str, float]]:
    """Merge per-tag frames (in priority order) into ``{cik: {period_end: value}}``.

    ``frames_by_tag[i]`` is ``{year: {cik: (end, value)}}`` for the i-th tag; for each
    company and year the highest-priority tag that has a value wins.
    """
    out: dict[int, dict[str, float]] = {}
    years = sorted({y for frames in frames_by_tag for y in frames})
    for year in years:
        taken: set[int] = set()
        for frames in frames_by_tag:
            for cik, (end, value) in frames.get(year, {}).items():
                if cik in taken:
                    continue
                taken.add(cik)
                out.setdefault(cik, {})[end] = value
    return out


def fetch_concept_history(session, tags: list[str], years: list[int]) -> dict[int, dict[str, float]]:
    frames_by_tag = []
    for tag in tags:
        frames = {}
        for year in years:
            frames[year] = parse_frame(_get_json(session, config.SEC_FRAMES_URL.format(tag=tag, year=year)))
            time.sleep(0.2)  # SEC fair-access policy: max 10 requests/second
        log.info("SEC %s: %d companies", tag, len({c for f in frames.values() for c in f}))
        frames_by_tag.append(frames)
    return combine_frames(frames_by_tag)


def fetch_sec_history(symbols: list[str]) -> dict[str, dict[str, dict[str, float]]]:
    """Return ``{symbol: {"revenue": {end: value}, "net_income": {end: value}}}``."""
    session = _session()
    ciks = fetch_ticker_ciks(session)
    if not ciks:
        log.warning("SEC ticker list unavailable - skipping SEC history")
        return {}
    this_year = dt.date.today().year
    years = list(range(this_year - config.SEC_YEARS_BACK, this_year + 1))
    revenue = fetch_concept_history(session, config.SEC_REVENUE_TAGS, years)
    net_income = fetch_concept_history(session, config.SEC_NET_INCOME_TAGS, years)
    out = {}
    for symbol in symbols:
        cik = ciks.get(symbol.upper())
        if cik is None:
            continue
        hist = {}
        if cik in revenue:
            hist["revenue"] = revenue[cik]
        if cik in net_income:
            hist["net_income"] = net_income[cik]
        if hist:
            out[symbol] = hist
    return out


def _parse_date(s: str) -> dt.date:
    return dt.date.fromisoformat(s[:10])


def validated_extension(yahoo: dict[str, float], sec: dict[str, float],
                        max_gap_days: int = 20) -> dict[str, float]:
    """SEC annual values for fiscal years Yahoo does not cover.

    Requires at least one fiscal year present in both sources (matched by period end
    within ``max_gap_days``); every overlapping year must agree within 3 %. Returns
    ``{}`` when the sources cannot be reconciled.
    """
    if not yahoo or not sec:
        return {}
    y_dates = [(_parse_date(d), v) for d, v in yahoo.items()]
    overlaps = 0
    extra = {}
    for end, value in sec.items():
        d = _parse_date(end)
        match = [yv for yd, yv in y_dates if abs((yd - d).days) <= max_gap_days]
        if match:
            yv = match[0]
            if yv == 0 or abs(value - yv) / abs(yv) > 0.03:
                return {}
            overlaps += 1
        elif d < min(yd for yd, _ in y_dates):
            extra[end] = value
    return extra if overlaps else {}
