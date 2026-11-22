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
# shares, partial data, currency effects, one-off gains in net income). So the totals are
# only used to confirm values that are implausible in the first place - a dividend yield
# of 15 %+ or a P/E below 1 - never to second-guess ordinary ones.

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
    """Cash paid to (common) shareholders in the latest reported 12 months (trailing, else
    the latest fiscal year), as a positive number; 0 if the statements show none paid,
    None if they do not say."""
    for key in (("T", "dividends_paid_common"), ("T", "dividends_paid"),
                ("A", "dividends_paid_common"), ("A", "dividends_paid")):
        _, value = _latest(statements.get(key, []), today, max_age_days)
        if value is not None:
            return abs(value)
    return None


def dividend_yield(stats: dict, statements: dict, market_cap_fin, today: dt.date | None = None):
    """Yahoo's dividend yield with implausible values checked.

    Returns ``(yield, basis)``:

    * ``yahoo``       - Yahoo's yield (forward, else trailing); values below 15 % are
                        taken as they are, higher ones when the dividends paid confirm them,
    * ``cash``        - dividends paid / market cap, when Yahoo's 15 %+ yield is more than
                        twice what the company actually paid,
    * ``invalid``     - (None) a dividend larger than the share price,
    * ``unconfirmed`` - (None) a 15 %+ yield while the statements show nothing paid, or
                        right after a stock split with nothing to check it against.
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
    if paid is not None and market_cap_fin and market_cap_fin > 0:
        cash = paid / market_cap_fin
        if cash >= y / 2:
            return y, "yahoo"  # confirmed; or paid more before a cut - Yahoo's forward yield is current
        if cash > 0:
            return cash, "cash"
        return None, "unconfirmed"  # nothing paid at all
    if _recent_split(stats, today):
        return None, "unconfirmed"
    return y, "yahoo"


def price_earnings(stats: dict, net_income_ttm, market_cap_fin):
    """Yahoo's P/E with impossible values removed.

    A P/E below 1 (price worth less than one year of earnings) almost always comes from
    a broken EPS, e.g. right after a reverse split (GMEX 0.0007, CHSN 0.004). It is kept
    only when market cap / net income confirms it; P/Es of 1 or more are never touched,
    because net income can legitimately differ from Yahoo's EPS (one-off gains,
    discontinued operations). Returns ``(pe, basis)`` with basis ``yahoo`` or
    ``(None, "invalid")``.
    """
    pe = stats.get("pe_ttm")
    if pe is None or pe <= 0:
        return None, None
    if pe >= 1:
        return pe, "yahoo"
    if net_income_ttm and net_income_ttm > 0 and market_cap_fin and market_cap_fin > 0 \
            and _agree(pe, market_cap_fin / net_income_ttm):
        return pe, "yahoo"
    return None, "invalid"


# --------------------------------------------------------------------------- value & quality

def _balance(statements: dict, item: str, today: dt.date | None):
    """Latest balance-sheet value: most recent quarter, else the latest fiscal year."""
    _, v = _latest(statements.get(("Q", item), []), today, 400)
    if v is None:
        _, v = _latest(statements.get(("A", item), []), today, 640)
    return v


def acquirers_multiple(statements: dict, market_cap_fin, today: dt.date | None = None):
    """Tobias Carlisle's acquirer's multiple: enterprise value / operating income (TTM).

    EV = market cap + total debt + preferred equity + minority interest - cash and
    short-term investments (latest balance sheet). Returns ``(multiple, ev, operating
    income)``. The multiple is None when operating income is not positive, when EV is not
    positive (mostly banks and insurers, where neither number means much), or when the
    company reports no balance sheet. Lower is cheaper.
    """
    _, op = _latest(statements.get(("T", "operating_income"), []), today, 500)
    if op is None:
        _, op = _latest(statements.get(("T", "ebit"), []), today, 500)
    if op is None:
        _, op = _latest(statements.get(("A", "operating_income"), []) or statements.get(("A", "ebit"), []),
                        today, 640)
    if not market_cap_fin or market_cap_fin <= 0:
        return None, None, op
    if _balance(statements, "equity", today) is None and _balance(statements, "total_assets", today) is None:
        return None, None, op  # no balance sheet: missing debt / cash cannot be read as zero
    cash = _balance(statements, "cash", today)
    if cash is None:
        cash = _balance(statements, "cash_only", today)
    # Yahoo leaves out items that are zero (a company without debt has no TotalDebt).
    ev = market_cap_fin + sum(_balance(statements, item, today) or 0.0
                              for item in ("total_debt", "preferred_equity", "minority_interest")) - (cash or 0.0)
    if op is None or op <= 0 or ev <= 0:
        return None, ev, op
    return ev / op, ev, op


F_SCORE_TESTS = ("roa", "cfo", "droa", "accrual", "dlever", "dliquid", "eq_offer", "dmargin", "dturn")


def piotroski_f_score(statements: dict, today: dt.date | None = None, max_age_days: int = 640):
    """Piotroski F-score (0-9) from the two latest fiscal years.

    One point each for: positive ROA, positive operating cash flow, higher ROA, operating
    cash flow above net income, lower long-term debt / total assets, higher current
    ratio, no more shares outstanding, higher gross margin, higher asset turnover.
    ROA and asset turnover use total assets at the start of the year when available.

    Returns ``(score, detail)`` where detail is a 9-character string of 1/0 in the order
    of ``F_SCORE_TESTS``; ``(None, None)`` unless all nine tests can be evaluated (banks
    and insurers report no current assets or gross profit).
    """
    def series(item):
        return statements.get(("A", item), [])

    assets = series("total_assets")
    if not assets:
        return None, None
    y1, ta1 = assets[-1]
    if today and (today - y1).days > max_age_days:
        return None, None
    y0, y_1 = _years_before(y1, 1), _years_before(y1, 2)

    def at(item, d, default=None):
        v = _find(series(item), d, 45)
        return default if v is None else v

    def gross(d):
        gp = at("gross_profit", d)
        if gp is None and at("revenue", d) is not None and at("cost_of_revenue", d) is not None:
            gp = at("revenue", d) - at("cost_of_revenue", d)
        return gp

    def shares(d):
        return at("shares", d) if series("shares") else at("diluted_shares", d)

    ta0, ta_1 = at("total_assets", y0), at("total_assets", y_1)
    ni1 = at("net_income", y1) if series("net_income") else at("net_income_common", y1)
    ni0 = at("net_income", y0) if series("net_income") else at("net_income_common", y0)
    values = [ta0, ni1, ni0, at("ocf", y1), at("revenue", y1), at("revenue", y0), gross(y1), gross(y0),
              at("current_assets", y1), at("current_assets", y0), at("current_liabilities", y1),
              at("current_liabilities", y0), shares(y1), shares(y0)]
    if any(v is None for v in values) or ta1 <= 0 or ta0 <= 0:
        return None, None
    ocf1, rev1, rev0 = at("ocf", y1), at("revenue", y1), at("revenue", y0)
    if rev1 <= 0 or rev0 <= 0 or at("current_liabilities", y1) <= 0 or at("current_liabilities", y0) <= 0:
        return None, None
    begin1 = ta0
    begin0 = ta_1 if ta_1 and ta_1 > 0 else ta0
    roa1, roa0 = ni1 / begin1, ni0 / begin0
    # A company without long-term debt has no LongTermDebt item.
    lever1, lever0 = at("long_term_debt", y1, 0.0) / ta1, at("long_term_debt", y0, 0.0) / ta0
    tests = [
        roa1 > 0,
        ocf1 > 0,
        roa1 > roa0,
        ocf1 / begin1 > roa1,
        lever1 <= lever0 if lever0 else lever1 == 0,
        at("current_assets", y1) / at("current_liabilities", y1) > at("current_assets", y0) / at("current_liabilities", y0),
        shares(y1) <= shares(y0),
        gross(y1) / rev1 > gross(y0) / rev0,
        rev1 / begin1 > rev0 / begin0,
    ]
    return sum(tests), "".join("1" if t else "0" for t in tests)


def regression_channel(closes: list[float], length: int = 2500, min_bars: int = 500) -> dict | None:
    """Linear regression channel over the last ``length`` daily closes.

    The same calculation as TradingView's linear regression / standard deviation channel:
    a straight line fitted to the closing prices (least squares) and the standard deviation
    of the prices around that line (``n - 1`` in the denominator). Returns

    * ``z``     - where the last close lies, in standard deviations from the line
                  (-1.5 = halfway between the -1 and -2 lines)
    * ``mid``   - the line's value on the last day (trading currency)
    * ``sd``    - one standard deviation (trading currency)
    * ``trend`` - the line's slope per year (252 trading days) relative to ``mid``
    * ``bars``  - number of closes used

    ``None`` with fewer than ``min_bars`` closes.
    """
    ys = [y for y in (closes or []) if y is not None][-length:]
    n = len(ys)
    if n < max(min_bars, 3):
        return None
    x_mean = (n - 1) / 2
    y_mean = sum(ys) / n
    sxx = sum((x - x_mean) ** 2 for x in range(n))
    sxy = sum((x - x_mean) * (y - y_mean) for x, y in enumerate(ys))
    slope = sxy / sxx
    intercept = y_mean - slope * x_mean
    sq = sum((y - (intercept + slope * x)) ** 2 for x, y in enumerate(ys))
    sd = (sq / (n - 1)) ** 0.5
    mid = intercept + slope * (n - 1)
    if sd <= 0:
        return None
    return {
        "z": (ys[-1] - mid) / sd,
        "mid": mid,
        "sd": sd,
        "trend": slope * 252 / mid if mid > 0 else None,
        "bars": n,
    }


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
    # FCF yield = free cash flow / market cap: a price check that needs no forecasts.
    out["fcf_yield"] = fcf / market_cap_fin if fcf is not None and market_cap_fin and market_cap_fin > 0 else None

    # P/E and dividend yield, checked against company totals (see above).
    _, ni_common = _latest(statements.get(("T", "net_income_common"), []), today, 500)
    out["pe_ttm"], out["pe_basis"] = price_earnings(stats, ni_common if ni_common is not None else ni_ttm,
                                                    market_cap_fin)
    out["dividend_yield"], out["dividend_basis"] = dividend_yield(stats, statements, market_cap_fin, today)

    # Yahoo's PEG is built on its own P/E: drop it together with a broken P/E.
    peg = stats.get("peg_5y") if stats.get("peg_5y") is not None else stats.get("peg_ratio")
    out["peg_5y"] = None if out["pe_basis"] == "invalid" else peg

    out["acquirers_multiple"], out["enterprise_value"], out["operating_income_ttm"] = acquirers_multiple(
        statements, market_cap_fin, today)
    out["f_score"], out["f_score_detail"] = piotroski_f_score(statements, today)

    channel = stats.get("channel") or {}
    out["channel_z"] = channel.get("z")
    out["channel_mid"] = channel.get("mid")
    out["channel_sd"] = channel.get("sd")
    out["channel_trend"] = channel.get("trend")
    out["channel_bars"] = channel.get("bars")

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
