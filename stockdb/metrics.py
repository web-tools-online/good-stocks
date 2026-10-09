"""Compute the screener metrics from the current Yahoo Finance data.

All functions are pure. ``statements`` holds the statement figures Yahoo returns with the
current fundamentals (the last 4 fiscal years and ~5 quarters). It maps
``(period, item)`` to a list of ``(end_date, value)`` tuples sorted by date, where
period is ``A`` (annual), ``Q`` (quarterly) or ``T`` (trailing twelve months).
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


def growth_4y(annual: Series, today: dt.date | None = None, max_age_days: int = 640):
    """Change between the oldest and the latest of the last four fiscal years.

    Yahoo Finance reports the four most recent fiscal years (e.g. FY2022-FY2025), so this
    is the latest fiscal year compared with the one three years before it.
    """
    if not annual:
        return None
    end_date, latest = annual[-1]
    if today and (today - end_date).days > max_age_days:
        return None
    return _ratio_growth(latest, _find(annual, _years_before(end_date, 3), 75))


def _consecutive_quarters(quarterly: Series, n: int):
    """Last ``n`` quarterly values if they are consecutive (about 3 months apart)."""
    if len(quarterly) < n:
        return None
    tail = quarterly[-n:]
    for (d1, _), (d2, _) in zip(tail, tail[1:]):
        if not 70 <= (d2 - d1).days <= 110:
            return None
    return [v for _, v in tail]


def revenue_growth_1y(statements: dict, yahoo_quarterly_growth=None, today: dt.date | None = None):
    """Revenue growth over the last year, TTM vs the previous TTM when possible.

    Returns ``(growth, basis)``.
    """
    trailing = statements.get(("T", "revenue"), [])
    quarterly = statements.get(("Q", "revenue"), [])
    annual = statements.get(("A", "revenue"), [])

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


# --------------------------------------------------------------------------- validation
#
# Yahoo's per-share fields (dividend per share, EPS) are sometimes out of line with the
# share price, typically after a reverse split, or when a spin-off is recorded as a cash
# dividend (e.g. GMEX: a $453.60 "dividend" on a $1.94 stock and EPS of $2,800 despite a
# net loss). Company totals from the financial statements do not have that problem.
#
# Checked against 62 ordinary dividend payers, Yahoo's per-share values were right in
# the normal range and the statement totals were the ones off (scrip dividends paid in
# shares, partial data, currency effects). So the totals are only used to catch values
# that are implausible in the first place, never to second-guess ordinary ones.

HIGH_DIVIDEND_YIELD = 0.15  # above this a yield must be confirmed by dividends actually paid


def _agree(a: float, b: float, factor: float = 2.0) -> bool:
    lo, hi = min(a, b), max(a, b)
    return hi <= factor * lo


def _recent_split(stats: dict, today: dt.date | None, days: int = 365) -> bool:
    ts = stats.get("last_split_date")
    if not ts or today is None:
        return False
    try:
        split = dt.datetime.fromtimestamp(ts, dt.timezone.utc).date()
    except (OverflowError, OSError, ValueError):
        return False
    return 0 <= (today - split).days <= days


def dividends_paid_ttm(statements: dict, today: dt.date | None, max_age_days: int = 550):
    """Cash paid to (common) shareholders over the last 12 months, as a positive number."""
    for key in (("T", "dividends_paid_common"), ("T", "dividends_paid"),
                ("A", "dividends_paid_common"), ("A", "dividends_paid")):
        _, value = _latest(statements.get(key, []), today, max_age_days)
        if value is not None:
            return abs(value)
    return None


def dividend_yield(stats: dict, statements: dict, market_cap_fin, today: dt.date | None = None):
    """Yahoo's dividend yield with implausible values checked.

    Returns ``(yield, basis)``:

    * ``yahoo``   - Yahoo's (forward) yield; values below 15 % are taken as they are,
    * ``cash``    - dividends actually paid in the last 12 months / market cap, used when
                    a yield of 15 % or more is not confirmed by them,
    * ``invalid`` - (None) a dividend above the share price, or a high yield right
                    after a stock split that cannot be checked.
    """
    y = stats.get("dividend_yield")
    if y is None:
        y = stats.get("trailing_dividend_yield")
    if y is None or y < 0:
        return None, None
    if y >= 1:
        return None, "invalid"  # dividend per share above the share price
    if y < HIGH_DIVIDEND_YIELD:
        return y, "yahoo"

    paid = dividends_paid_ttm(statements, today)
    cash = paid / market_cap_fin if paid and market_cap_fin and market_cap_fin > 0 else None
    if cash is not None and 0 < cash < 1:
        return (y, "yahoo") if _agree(y, cash) else (cash, "cash")
    if _recent_split(stats, today):
        return None, "invalid"
    return y, "yahoo"


def price_earnings(stats: dict, net_income_ttm, market_cap_fin):
    """Yahoo's P/E with impossible values removed.

    Returns ``(pe, basis)``: ``yahoo`` when kept, ``(None, "loss")`` when the company
    made a loss over the last 12 months (no meaningful P/E), ``(None, "conflict")`` when
    Yahoo's P/E is wildly out of line with market cap / net income.
    """
    if net_income_ttm is not None and net_income_ttm <= 0:
        return None, "loss"
    pe = stats.get("pe_ttm")
    if pe is None or pe <= 0:
        return None, None
    if net_income_ttm is None or not market_cap_fin or market_cap_fin <= 0:
        return pe, "yahoo"
    implied = market_cap_fin / net_income_ttm
    ratio = max(pe, implied) / min(pe, implied)
    if ratio > 10 or (ratio > 2 and min(pe, implied) < 1):
        return None, "conflict"
    return pe, "yahoo"


def compute(statements: dict, stats: dict, today: dt.date | None = None, market_cap_fin=None) -> dict:
    """Return the metrics stored in the ``metrics`` table (financial-currency units).

    ``market_cap_fin`` is the market capitalisation converted to the financial currency;
    it is needed to check Yahoo's per-share P/E and dividend yield against company totals.
    """
    stats = stats or {}
    out: dict = {}

    out["rev_growth_1y"], out["rev_growth_1y_basis"] = revenue_growth_1y(
        statements, stats.get("revenue_growth_q"), today)

    out["rev_growth_4y"] = growth_4y(statements.get(("A", "revenue"), []), today=today)

    income = statements.get(("A", "net_income")) or statements.get(("A", "net_income_common"), [])
    out["earn_growth_4y"] = growth_4y(income, today=today)

    # TTM figures
    _, revenue_ttm = _latest(statements.get(("T", "revenue"), []), today, 500)
    _, ni_ttm = _latest(statements.get(("T", "net_income"), []) or statements.get(("T", "net_income_common"), []),
                        today, 500)
    out["revenue_ttm"], out["net_income_ttm"] = revenue_ttm, ni_ttm

    # Balance sheet: most recent quarter, falling back to the latest fiscal year.
    _, equity = _latest(statements.get(("Q", "equity"), []), today, 400)
    if equity is None:
        _, equity = _latest(statements.get(("A", "equity"), []), today, 640)
    _, debt = _latest(statements.get(("Q", "total_debt"), []), today, 400)
    if debt is None:
        _, debt = _latest(statements.get(("A", "total_debt"), []), today, 640)

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
    _, fcf = _latest(statements.get(("T", "fcf"), []), today, 500)
    if fcf is None:
        _, ocf = _latest(statements.get(("T", "ocf"), []), today, 500)
        _, capex = _latest(statements.get(("T", "capex"), []), today, 500)
        if ocf is not None and capex is not None:
            fcf = ocf - abs(capex)
    if fcf is None:
        fcf = stats.get("fcf_levered")
    out["fcf_ttm"] = fcf

    # P/E and dividend yield, checked against company totals (see above).
    _, ni_common = _latest(statements.get(("T", "net_income_common"), []), today, 500)
    out["pe_ttm"], out["pe_basis"] = price_earnings(stats, ni_common if ni_common is not None else ni_ttm,
                                                    market_cap_fin)
    out["dividend_yield"], out["dividend_basis"] = dividend_yield(stats, statements, market_cap_fin, today)

    # Yahoo's PEG is built on its own P/E: drop it when that P/E was wrong or meaningless.
    peg = stats.get("peg_5y") if stats.get("peg_5y") is not None else stats.get("peg_ratio")
    out["peg_5y"] = peg if out["pe_basis"] in ("yahoo", None) else None

    annual_rev = statements.get(("A", "revenue"), [])
    out["latest_fy_end"] = annual_rev[-1][0].isoformat() if annual_rev else None
    return out


def statements_from_rows(rows) -> dict:
    """Build the ``statements`` mapping from ``(period, item, end_date, value)`` rows."""
    hist: dict = {}
    for period, item, end, value in rows:
        if value is None:
            continue
        d = end if isinstance(end, dt.date) else dt.date.fromisoformat(str(end)[:10])
        hist.setdefault((period, item), {})[d] = float(value)
    return {k: sorted(v.items()) for k, v in hist.items()}
