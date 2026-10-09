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
    assert metrics.dividend_yield(stats, {}, 1e8, TODAY) == (None, "invalid")
    stats["last_split_date"] = _ts(D(2020, 8, 31))  # old split: nothing suspicious
    assert metrics.dividend_yield(stats, {}, 1e8, TODAY) == (0.4, "yahoo")


def test_stale_dividends_paid_are_ignored():
    old = statements_from_rows([("A", "dividends_paid", "2022-06-30", -5e6)])
    assert metrics.dividends_paid_ttm(old, TODAY) is None


def test_pe_of_loss_making_company_is_hidden():
    # GMEX: P/E 0.0007 from an EPS of $2,800 while the company lost $8.9M
    assert metrics.price_earnings({"pe_ttm": 0.000692877}, -8.873e6, 1459904) == (None, "loss")
    # also without a Yahoo P/E (CYNCA.ST), so PEG counts as failed for every loss-maker
    assert metrics.price_earnings({}, -3.44e8, 1.65e9) == (None, "loss")


def test_pe_far_off_market_cap_over_net_income_is_hidden():
    # CHSN: P/E 0.0036 vs market cap / net income = 13.1 (reverse split 1:100)
    assert metrics.price_earnings({"pe_ttm": 0.0036471232}, 1.875e5, 2461734) == (None, "conflict")
    # below 1 and more than 2x off
    assert metrics.price_earnings({"pe_ttm": 0.815}, 1e6, 7.35e6) == (None, "conflict")


def test_ordinary_pe_differences_are_kept():
    # holding structures, share classes etc. give differences up to several times
    assert metrics.price_earnings({"pe_ttm": 60.4}, 1e8, 1.12e9) == (60.4, "yahoo")
    assert metrics.price_earnings({"pe_ttm": 0.81}, 1.038e9, 7.7e8) == (0.81, "yahoo")  # LX: confirmed
    assert metrics.price_earnings({"pe_ttm": 39.04}, None, 4.968e12) == (39.04, "yahoo")  # nothing to check
    assert metrics.price_earnings({}, 1e9, 1e10) == (None, None)  # no Yahoo P/E: none


def test_compute_drops_peg_with_unusable_pe():
    st = statements_from_rows([("T", "net_income", "2026-06-30", -5e6)])
    m = metrics.compute(st, {"pe_ttm": 12.0, "peg_5y": 0.8}, TODAY, market_cap_fin=1e8)
    assert m["pe_ttm"] is None and m["pe_basis"] == "loss" and m["peg_5y"] is None
    st = statements_from_rows([("T", "net_income", "2026-06-30", 1e7), ("T", "net_income_common", "2026-06-30", 9e6)])
    m = metrics.compute(st, {"pe_ttm": 12.0, "peg_5y": 0.8, "dividend_yield": 0.02}, TODAY, market_cap_fin=1e8)
    assert (m["pe_ttm"], m["pe_basis"], m["peg_5y"]) == (12.0, "yahoo", 0.8)
    assert (m["dividend_yield"], m["dividend_basis"]) == (0.02, "yahoo")
