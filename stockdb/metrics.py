"""Compute the screener metrics from statement history and Yahoo key statistics.

All functions are pure. ``history`` maps ``(period, item)`` to a list of
``(end_date, value)`` tuples sorted by date, where period is ``A`` (annual),
``Q`` (quarterly) or ``T`` (trailing twelve months).
"""

from __future__ import annotations

import datetime as dt

Series = list[tuple[dt.date, float]]

# Rev. growth basis codes (shown as a tooltip on the website).
BASIS_TTM = "ttm"            # TTM vs TTM one year earlier
BASIS_FY = "fy"              # latest fiscal year vs previous fiscal year (TTM == FY)
BASIS_MRQ = "mrq"            # most recent quarter vs same quarter a year earlier
BASIS_YAHOO = "yahoo_q"      # Yahoo's quarterly YoY revenue growth
BASIS_FY_OLD = "fy_old"      # fiscal years, latest TTM not available


def _years_before(d: dt.date, years: int) -> dt.date:
    try:
        return d.replace(year=d.year - years)
    except ValueError:  # 29 February
        return d.replace(year=d.year - years, day=28)


def _find(series: Series, target: dt.date, tolerance_days: int):
    best = None
    for d, v in series:
        gap = abs((d - target).days)
        if gap <= tolerance_days and (best is None or gap < best[0]):
            best = (gap, v)
    return None if best is None else best[1]


def _ratio_growth(new: float, old: float):
    if old is None or new is None or old <= 0:
        return None
    return (new - old) / old


def growth_5y(annual: Series, target_years: int = 5, min_years: int = 3,
              today: dt.date | None = None, max_age_days: int = 640):
    """Cumulative growth over ``target_years`` fiscal years.

    Uses the latest fiscal year and the year ``target_years`` earlier. When that much
    history is not available yet, the longest span of at least ``min_years`` is used
    and its compound annual rate is extended to ``target_years``.

    Returns ``(growth, years_used)`` or ``(None, None)``.
    """
    if not annual:
        return None, None
    end_date, latest = annual[-1]
    if today and (today - end_date).days > max_age_days:
        return None, None
    for years in range(target_years, min_years - 1, -1):
        base = _find(annual, _years_before(end_date, years), 75)
        if base is None:
            continue
        g = _ratio_growth(latest, base)
        if g is None:
            return None, None
        if years < target_years and latest > 0:
            g = (latest / base) ** (target_years / years) - 1
        return g, years
    return None, None


def _consecutive_quarters(quarterly: Series, n: int):
    """Last ``n`` quarterly values if they are consecutive (about 3 months apart)."""
    if len(quarterly) < n:
        return None
    tail = quarterly[-n:]
    for (d1, _), (d2, _) in zip(tail, tail[1:]):
        if not 70 <= (d2 - d1).days <= 110:
            return None
    return [v for _, v in tail]


def revenue_growth_1y(history: dict, yahoo_quarterly_growth=None, today: dt.date | None = None):
    """Revenue growth over the last year, TTM vs the previous TTM when possible.

    Returns ``(growth, basis)``.
    """
    trailing = history.get(("T", "revenue"), [])
    quarterly = history.get(("Q", "revenue"), [])
    annual = history.get(("A", "revenue"), [])

    if trailing:
        d0, v0 = trailing[-1]
        prev = _find(trailing[:-1], _years_before(d0, 1), 20)
        if prev is not None and (g := _ratio_growth(v0, prev)) is not None:
            return g, BASIS_TTM

    q8 = _consecutive_quarters(quarterly, 8)
    if q8 and (g := _ratio_growth(sum(q8[4:]), sum(q8[:4]))) is not None:
        return g, BASIS_TTM

    if trailing and annual:
        d0, _ = trailing[-1]
        fy_end, fy_value = annual[-1]
        if abs((d0 - fy_end).days) <= 20:
            prev = _find(annual[:-1], _years_before(fy_end, 1), 45)
            if (g := _ratio_growth(fy_value, prev)) is not None:
                return g, BASIS_FY

    if quarterly:
        d0, v0 = quarterly[-1]
        prev = _find(quarterly[:-1], _years_before(d0, 1), 20)
        if (g := _ratio_growth(v0, prev)) is not None:
            return g, BASIS_MRQ

    if yahoo_quarterly_growth is not None:
        return yahoo_quarterly_growth, BASIS_YAHOO

    if len(annual) >= 2:
        fy_end, fy_value = annual[-1]
        if today is None or (today - fy_end).days <= 640:
            prev = _find(annual[:-1], _years_before(fy_end, 1), 45)
            if (g := _ratio_growth(fy_value, prev)) is not None:
                return g, BASIS_FY_OLD
    return None, None


def _latest(series: Series, today: dt.date | None, max_age_days: int):
    if not series:
        return None, None
    d, v = series[-1]
    if today and (today - d).days > max_age_days:
        return None, None
    return d, v


def compute(history: dict, stats: dict, today: dt.date | None = None) -> dict:
    """Return the metrics stored in the ``metrics`` table (financial-currency units)."""
    stats = stats or {}
    out: dict = {}

    out["rev_growth_1y"], out["rev_growth_1y_basis"] = revenue_growth_1y(
        history, stats.get("revenue_growth_q"), today)

    out["rev_growth_5y"], out["rev_growth_5y_years"] = growth_5y(history.get(("A", "revenue"), []), today=today)

    income = history.get(("A", "net_income")) or history.get(("A", "net_income_common"), [])
    out["earn_growth_5y"], out["earn_growth_5y_years"] = growth_5y(income, today=today)

    # TTM figures
    _, revenue_ttm = _latest(history.get(("T", "revenue"), []), today, 500)
    _, ni_ttm = _latest(history.get(("T", "net_income"), []) or history.get(("T", "net_income_common"), []),
                        today, 500)
    out["revenue_ttm"], out["net_income_ttm"] = revenue_ttm, ni_ttm

    # Balance sheet: most recent quarter, falling back to the latest fiscal year.
    _, equity = _latest(history.get(("Q", "equity"), []), today, 400)
    if equity is None:
        _, equity = _latest(history.get(("A", "equity"), []), today, 640)
    _, debt = _latest(history.get(("Q", "total_debt"), []), today, 400)
    if debt is None:
        _, debt = _latest(history.get(("A", "total_debt"), []), today, 640)

    roe = stats.get("roe")
    if roe is None and ni_ttm is not None and equity and equity > 0:
        roe = ni_ttm / equity
    out["roe"] = roe

    de = stats.get("debt_to_equity_pct")
    if de is not None:
        de = de / 100.0
    elif debt is not None and equity and equity > 0:
        de = debt / equity
    out["debt_to_equity"] = de

    # Free cash flow (TTM) = operating cash flow - capital expenditure.
    _, fcf = _latest(history.get(("T", "fcf"), []), today, 500)
    if fcf is None:
        _, ocf = _latest(history.get(("T", "ocf"), []), today, 500)
        _, capex = _latest(history.get(("T", "capex"), []), today, 500)
        if ocf is not None and capex is not None:
            fcf = ocf - abs(capex)
    if fcf is None:
        fcf = stats.get("fcf_levered")
    out["fcf_ttm"] = fcf

    out["peg_5y"] = stats.get("peg_5y") if stats.get("peg_5y") is not None else stats.get("peg_ratio")

    annual_rev = history.get(("A", "revenue"), [])
    out["latest_fy_end"] = annual_rev[-1][0].isoformat() if annual_rev else None
    return out


def history_from_rows(rows) -> dict:
    """Build the ``history`` mapping from ``(period, item, end_date, value)`` rows."""
    hist: dict = {}
    for period, item, end, value in rows:
        if value is None:
            continue
        d = end if isinstance(end, dt.date) else dt.date.fromisoformat(str(end)[:10])
        hist.setdefault((period, item), {})[d] = float(value)
    return {k: sorted(v.items()) for k, v in hist.items()}
