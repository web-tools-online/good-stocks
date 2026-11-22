import datetime as dt

import pytest

from stockdb import metrics
from stockdb.metrics import growth_4y, revenue_growth_1y, statements_from_rows

D = dt.date


def annual(values, last_year=2025, month=12, day=31):
    n = len(values)
    return [(D(last_year - n + 1 + i, month, day), v) for i, v in enumerate(values)]


def test_growth_4y_latest_vs_oldest_of_four_years():
    assert growth_4y(annual([100, 110, 121, 150])) == pytest.approx(0.5)


def test_growth_4y_uses_the_year_three_before_latest():
    # more stored years than four: still FY0 vs FY-3
    assert growth_4y(annual([50, 100, 110, 121, 150])) == pytest.approx(0.5)


def test_growth_4y_needs_four_years():
    assert growth_4y(annual([100, 110, 120])) is None


def test_growth_4y_non_positive_base():
    assert growth_4y(annual([-5, 2, 3, 10])) is None


def test_growth_4y_negative_latest():
    assert growth_4y(annual([100, 50, 20, -50])) == pytest.approx(-1.5)


def test_growth_4y_stale():
    assert growth_4y(annual([100, 110, 120, 130], last_year=2020), today=D(2026, 10, 1)) is None


def test_growth_4y_shifted_fiscal_year():
    series = [(D(2022, 7, 1), 120), (D(2023, 6, 30), 130), (D(2024, 6, 28), 150), (D(2025, 6, 27), 180)]
    assert growth_4y(series) == pytest.approx(0.5)


def test_revenue_growth_from_trailing_values():
    hist = {("T", "revenue"): [(D(2024, 6, 30), 100.0), (D(2025, 6, 30), 112.0)]}
    g, basis = revenue_growth_1y(hist)
    assert basis == "ttm" and g == pytest.approx(0.12)


def test_revenue_growth_from_eight_quarters():
    q = [(D(2023, 9, 30), 10), (D(2023, 12, 31), 10), (D(2024, 3, 31), 10), (D(2024, 6, 30), 10),
         (D(2024, 9, 30), 11), (D(2024, 12, 31), 11), (D(2025, 3, 31), 12), (D(2025, 6, 30), 12)]
    g, basis = revenue_growth_1y({("Q", "revenue"): q, ("T", "revenue"): [(D(2025, 6, 30), 46)]})
    assert basis == "ttm" and g == pytest.approx(0.15)


def test_revenue_growth_ttm_equals_fiscal_year():
    hist = {
        ("T", "revenue"): [(D(2025, 12, 31), 120.0)],
        ("A", "revenue"): annual([90, 100, 120]),
        ("Q", "revenue"): [(D(2025, 3, 31), 28), (D(2025, 6, 30), 30), (D(2025, 9, 30), 30), (D(2025, 12, 31), 32)],
    }
    g, basis = revenue_growth_1y(hist)
    assert basis == "fy" and g == pytest.approx(0.2)


def test_revenue_growth_mrq_fallback():
    q = [(D(2024, 6, 30), 20), (D(2024, 9, 30), 21), (D(2024, 12, 31), 22), (D(2025, 3, 31), 23), (D(2025, 6, 30), 25)]
    hist = {("Q", "revenue"): q, ("T", "revenue"): [(D(2025, 6, 30), 91)], ("A", "revenue"): annual([80, 85])}
    g, basis = revenue_growth_1y(hist)
    assert basis == "mrq" and g == pytest.approx(0.25)


def test_revenue_growth_yahoo_fallback():
    g, basis = revenue_growth_1y({}, yahoo_quarterly_growth=0.07)
    assert (g, basis) == (0.07, "yahoo_q")


def test_compute_prefers_yahoo_ratios_and_computes_fcf():
    hist = statements_from_rows([
        ("A", "revenue", "2021-12-31", 100), ("A", "revenue", "2022-12-31", 110),
        ("A", "revenue", "2023-12-31", 120), ("A", "revenue", "2024-12-31", 130),
        ("A", "revenue", "2025-12-31", 150), ("A", "revenue", "2020-12-31", 75),
        ("A", "net_income", "2022-12-31", 10), ("A", "net_income", "2025-12-31", 20),
        ("T", "ocf", "2026-06-30", 50), ("T", "capex", "2026-06-30", -20),
        ("T", "net_income", "2026-06-30", 22),
        ("Q", "equity", "2026-06-30", 100), ("Q", "total_debt", "2026-06-30", 40),
    ])
    m = metrics.compute(hist, {"roe": 0.3, "debt_to_equity_pct": 26.0, "peg_5y": 1.4}, today=D(2026, 9, 1))
    assert m["rev_growth_4y"] == pytest.approx(150 / 110 - 1)  # FY2025 vs FY2022
    assert m["earn_growth_4y"] == pytest.approx(1.0)
    assert m["fcf_ttm"] == 30
    assert m["fcf_yield"] is None  # no market cap given
    assert m["roe"] == 0.3
    assert m["debt_to_equity"] == pytest.approx(0.26)
    assert m["peg_5y"] == 1.4
    assert m["latest_fy_end"] == "2025-12-31"


def test_compute_falls_back_to_statements():
    hist = statements_from_rows([
        ("T", "net_income", "2026-06-30", 25), ("Q", "equity", "2026-06-30", 100),
        ("Q", "total_debt", "2026-06-30", 50), ("T", "fcf", "2026-06-30", -5),
    ])
    m = metrics.compute(hist, {"peg_ratio": 0.9}, today=D(2026, 9, 1))
    assert m["roe"] == pytest.approx(0.25)
    assert m["debt_to_equity"] == pytest.approx(0.5)
    assert m["fcf_ttm"] == -5
    assert m["peg_5y"] == 0.9
    assert m["rev_growth_4y"] is None


# --- dividend yield / P/E checks, using the real Yahoo figures that motivated them -----

TODAY = D(2026, 10, 9)


def _paid(ttm=None, annual=None, end="2026-06-30"):
    rows = []
    if ttm is not None:
        rows.append(("T", "dividends_paid_common", end, -ttm))
    if annual is not None:
        rows.append(("A", "dividends_paid", "2025-12-31", -annual))
    return statements_from_rows(rows)


def _ts(day):
    import datetime as _dt
    return _dt.datetime(day.year, day.month, day.day, tzinfo=_dt.timezone.utc).timestamp()


def test_dividend_above_share_price_is_hidden():
    # GMEX: a $453.60 "dividend" on a $1.94 share, no dividends in the cash-flow statement
    stats = {"dividend_yield": 232.61539, "trailing_dividend_yield": 170.53, "last_split_date": _ts(D(2026, 9, 28))}
    assert metrics.dividend_yield(stats, {}, 1459904, TODAY) == (None, "invalid")
    # CYNCA.ST: a spin-off recorded as a 135 SEK dividend on a 43.82 SEK share
    assert metrics.dividend_yield({"dividend_yield": 3.2067, "trailing_dividend_yield": 0.0}, {}, 1.65e9, TODAY) \
        == (None, "invalid")


def test_high_yield_not_confirmed_by_cash_uses_dividends_paid():
    # LARK.ST: Yahoo 2.00 SEK/share (90.5 %), but 3.96M SEK paid on 19.8M shares = 0.20 SEK
    y, basis = metrics.dividend_yield({"dividend_yield": 0.905}, _paid(annual=3.96e6), 45146728, TODAY)
    assert basis == "cash" and y == pytest.approx(0.0877, abs=1e-4)


def test_high_yield_confirmed_by_cash_is_kept():
    # STX.WA really paid ~40 %: 180.5M PLN on a 452M PLN market cap
    assert metrics.dividend_yield({"dividend_yield": 0.4239}, _paid(ttm=1.805e8), 4.52e8, TODAY) == (0.4239, "yahoo")
    # NHTC: cash yield 79 % vs Yahoo 42.9 % - within a factor of two
    assert metrics.dividend_yield({"dividend_yield": 0.4287}, _paid(ttm=6.32e6), 8.0e6, TODAY) == (0.4287, "yahoo")


def test_ordinary_yields_are_never_second_guessed():
    # Sacyr pays scrip dividends (shares): cash paid is far below the yield, Yahoo is right
    assert metrics.dividend_yield({"dividend_yield": 0.0516}, _paid(ttm=1.0e7), 7.8e8, TODAY) == (0.0516, "yahoo")
    assert metrics.dividend_yield({"dividend_yield": 0.0032}, _paid(ttm=1.564e10), 4.968e12, TODAY) == (0.0032, "yahoo")
    assert metrics.dividend_yield({"dividend_yield": 0.0}, {}, 1e9, TODAY) == (0.0, "yahoo")
    assert metrics.dividend_yield({}, _paid(ttm=1e6), 1e8, TODAY) == (None, None)  # no Yahoo yield: none


def test_trailing_yield_used_when_forward_missing():
    assert metrics.dividend_yield({"trailing_dividend_yield": 0.03}, {}, 1e9, TODAY) == (0.03, "yahoo")


def test_high_yield_after_recent_split_without_cash_data_is_hidden():
    stats = {"dividend_yield": 0.4, "last_split_date": _ts(D(2026, 5, 7))}
    assert metrics.dividend_yield(stats, {}, 1e8, TODAY) == (None, "unconfirmed")
    stats["last_split_date"] = _ts(D(2020, 8, 31))  # old split: nothing suspicious
    assert metrics.dividend_yield(stats, {}, 1e8, TODAY) == (0.4, "yahoo")


def test_high_yield_with_nothing_paid_is_hidden():
    # the cash-flow statement explicitly reports 0 dividends paid
    assert metrics.dividend_yield({"dividend_yield": 0.3}, _paid(ttm=0.0), 1e8, TODAY) == (None, "unconfirmed")


def test_dividend_cut_keeps_yahoos_lower_forward_yield():
    # paid 40 % of the market cap last year, Yahoo's forward yield after a cut is 16 %
    assert metrics.dividend_yield({"dividend_yield": 0.16}, _paid(ttm=4e7), 1e8, TODAY) == (0.16, "yahoo")


def test_stale_dividends_paid_are_ignored():
    old = statements_from_rows([("A", "dividends_paid", "2022-06-30", -5e6)])
    assert metrics.dividends_paid_ttm(old, TODAY) is None
    zero = statements_from_rows([("T", "dividends_paid", "2026-06-30", 0.0)])
    assert metrics.dividends_paid_ttm(zero, TODAY) == 0


def test_implausible_pe_is_hidden():
    # GMEX: P/E 0.0007 from an EPS of $2,800 while the company lost $8.9M
    assert metrics.price_earnings({"pe_ttm": 0.000692877}, -8.873e6, 1459904) == (None, "invalid")
    # CHSN: P/E 0.0036 vs market cap / net income = 13.1 (reverse split 1:100)
    assert metrics.price_earnings({"pe_ttm": 0.0036471232}, 1.875e5, 2461734) == (None, "invalid")
    # AZUL, no usable net income to confirm it
    assert metrics.price_earnings({"pe_ttm": 1.2428857e-05}, None, 1.29e9) == (None, "invalid")


def test_pe_below_one_confirmed_by_statements_is_kept():
    # LX: P/E 0.81, market cap / net income 0.74
    assert metrics.price_earnings({"pe_ttm": 0.81}, 1.038e9, 7.7e8) == (0.81, "yahoo")


def test_ordinary_pes_are_never_second_guessed():
    # TRS: one-off gain from selling a division makes net income exceed revenue
    assert metrics.price_earnings({"pe_ttm": 15.56}, 9.052e8, 1.379e9) == (15.56, "yahoo")
    # SONY: net loss only from discontinued operations (spin-off)
    assert metrics.price_earnings({"pe_ttm": 19.8}, -2.216e11, 2.0e13) == (19.8, "yahoo")
    # holding structures / share classes
    assert metrics.price_earnings({"pe_ttm": 60.4}, 1e8, 1.12e9) == (60.4, "yahoo")
    assert metrics.price_earnings({"pe_ttm": 39.04}, None, 4.968e12) == (39.04, "yahoo")
    assert metrics.price_earnings({}, 1e9, 1e10) == (None, None)
    assert metrics.price_earnings({}, -3.44e8, 1.65e9) == (None, None)


def test_compute_drops_peg_only_with_invalid_pe():
    st = statements_from_rows([("T", "net_income", "2026-06-30", -5e6)])
    m = metrics.compute(st, {"pe_ttm": 0.0007, "peg_5y": 0.8}, TODAY, market_cap_fin=1e8)
    assert (m["pe_ttm"], m["pe_basis"], m["peg_5y"]) == (None, "invalid", None)
    m = metrics.compute(st, {"pe_ttm": 12.0, "peg_5y": 0.8}, TODAY, market_cap_fin=1e8)
    assert (m["pe_ttm"], m["pe_basis"], m["peg_5y"]) == (12.0, "yahoo", 0.8)
    st = statements_from_rows([("T", "net_income", "2026-06-30", 1e7), ("T", "net_income_common", "2026-06-30", 9e6)])
    m = metrics.compute(st, {"pe_ttm": 12.0, "peg_5y": 0.8, "dividend_yield": 0.02}, TODAY, market_cap_fin=1e8)
    assert (m["pe_ttm"], m["pe_basis"], m["peg_5y"]) == (12.0, "yahoo", 0.8)
    assert (m["dividend_yield"], m["dividend_basis"]) == (0.02, "yahoo")


def test_fcf_yield_is_fcf_over_market_cap():
    st = statements_from_rows([("T", "fcf", "2026-06-30", 40)])
    assert metrics.compute(st, {}, TODAY, market_cap_fin=1000)["fcf_yield"] == pytest.approx(0.04)
    neg = statements_from_rows([("T", "fcf", "2026-06-30", -10)])
    assert metrics.compute(neg, {}, TODAY, market_cap_fin=1000)["fcf_yield"] == pytest.approx(-0.01)
    assert metrics.compute(st, {}, TODAY, market_cap_fin=0)["fcf_yield"] is None


def _fy(item, values, period="A"):
    return {(period, item): annual(values)}


def _f_score_statements(**override):
    st = {}
    base = {
        "total_assets": [1000, 1000, 1000], "net_income": [50, 50, 80], "ocf": [60, 60, 100],
        "revenue": [900, 900, 1000], "gross_profit": [300, 300, 350], "current_assets": [400, 400, 500],
        "current_liabilities": [300, 300, 300], "long_term_debt": [200, 200, 150], "shares": [100, 100, 99],
    }
    base.update(override)
    for item, values in base.items():
        st.update(_fy(item, values))
    return st


def test_f_score_all_tests_pass():
    assert metrics.piotroski_f_score(_f_score_statements()) == (9, "111111111")


def test_f_score_all_tests_fail():
    st = _f_score_statements(net_income=[50, 50, -10], ocf=[60, 60, -20], revenue=[900, 1000, 900],
                             gross_profit=[300, 350, 250], current_assets=[400, 500, 400],
                             long_term_debt=[200, 150, 300], shares=[100, 100, 110])
    assert metrics.piotroski_f_score(st) == (0, "000000000")


def test_f_score_without_long_term_debt_and_from_cost_of_revenue():
    st = _f_score_statements()
    del st[("A", "long_term_debt")], st[("A", "gross_profit")]
    st.update(_fy("cost_of_revenue", [600, 600, 650]))
    score, detail = metrics.piotroski_f_score(st)
    assert detail[4] == "1" and detail[7] == "1" and score == 9


def test_f_score_needs_all_nine_tests():
    st = _f_score_statements()
    del st[("A", "current_assets")]  # e.g. a bank
    assert metrics.piotroski_f_score(st) == (None, None)
    assert metrics.piotroski_f_score(_f_score_statements(), today=D(2028, 1, 1)) == (None, None)  # stale


def test_acquirers_multiple():
    st = {("T", "operating_income"): [(D(2025, 12, 31), 100.0)], ("Q", "total_debt"): [(D(2025, 12, 31), 300.0)],
          ("Q", "cash"): [(D(2025, 12, 31), 200.0)], ("Q", "minority_interest"): [(D(2025, 12, 31), 50.0)],
          ("Q", "equity"): [(D(2025, 12, 31), 500.0)]}
    am, ev, op = metrics.acquirers_multiple(st, 1000)
    assert (ev, op) == (1150, 100) and am == pytest.approx(11.5)
    # debt-free company: Yahoo has no TotalDebt item
    del st[("Q", "total_debt")]
    assert metrics.acquirers_multiple(st, 1000)[0] == pytest.approx(8.5)


def test_acquirers_multiple_not_meaningful():
    st = {("T", "operating_income"): [(D(2025, 12, 31), -5.0)], ("Q", "equity"): [(D(2025, 12, 31), 500.0)]}
    assert metrics.acquirers_multiple(st, 1000)[0] is None  # operating loss
    st[("T", "operating_income")] = [(D(2025, 12, 31), 50.0)]
    st[("Q", "cash")] = [(D(2025, 12, 31), 2000.0)]
    assert metrics.acquirers_multiple(st, 1000)[0] is None  # negative EV
    assert metrics.acquirers_multiple({("T", "operating_income"): [(D(2025, 12, 31), 50.0)]}, 1000)[0] is None


# --------------------------------------------------------------------------- regression channel

def test_regression_channel_matches_least_squares():
    import numpy as np

    rng = np.random.default_rng(1)
    closes = list(50 + 0.02 * np.arange(2600) + rng.normal(0, 3, 2600))
    ch = metrics.regression_channel(closes, length=2500)
    ys = np.array(closes[-2500:])
    xs = np.arange(2500)
    slope, intercept = np.polyfit(xs, ys, 1)
    mid = intercept + slope * 2499
    sd = np.std(ys - (intercept + slope * xs), ddof=1)
    assert ch["bars"] == 2500
    assert abs(ch["mid"] - mid) < 1e-6
    assert abs(ch["sd"] - sd) < 1e-6
    assert abs(ch["z"] - (ys[-1] - mid) / sd) < 1e-9
    assert abs(ch["trend"] - slope * 252 / mid) < 1e-9


def test_regression_channel_position():
    # a straight line with a +/-1 zigzag: sd ~ 1; the last close drops 2 below the line
    closes = [10 + 0.01 * i + (1 if i % 2 else -1) for i in range(999)] + [10 + 0.01 * 999 - 2]
    ch = metrics.regression_channel(closes)
    assert ch["z"] < -1.8
    assert ch["trend"] > 0


def test_regression_channel_needs_enough_history():
    assert metrics.regression_channel([10.0] * 400) is None
    assert metrics.regression_channel([]) is None
    assert metrics.regression_channel([10.0] * 600) is None  # flat: no deviation


def test_compute_takes_channel_from_stats():
    out = metrics.compute({}, {"channel": {"z": -1.2, "mid": 100.0, "sd": 5.0, "trend": 0.08, "bars": 2500}})
    assert out["channel_z"] == -1.2 and out["channel_bars"] == 2500
    assert metrics.compute({}, {})["channel_z"] is None
