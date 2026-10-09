import datetime as dt

import pytest

from stockdb import metrics
from stockdb.metrics import growth_5y, revenue_growth_1y, history_from_rows

D = dt.date


def annual(values, last_year=2025, month=12, day=31):
    n = len(values)
    return [(D(last_year - n + 1 + i, month, day), v) for i, v in enumerate(values)]


def test_growth_5y_exact():
    g, years = growth_5y(annual([100, 110, 120, 130, 140, 200]))
    assert years == 5
    assert g == pytest.approx(1.0)


def test_growth_5y_extrapolated_from_3_years():
    # 4 fiscal years = 3-year span; 33.1% over 3 years = 10%/yr -> 61.05% over 5 years
    g, years = growth_5y(annual([100, 110, 121, 133.1]))
    assert years == 3
    assert g == pytest.approx(1.1 ** 5 - 1, rel=1e-6)


def test_growth_5y_needs_three_years():
    assert growth_5y(annual([100, 110, 120])) == (None, None)


def test_growth_5y_non_positive_base():
    assert growth_5y(annual([-5, 1, 2, 3, 4, 10])) == (None, None)


def test_growth_5y_negative_latest_not_extrapolated():
    g, years = growth_5y(annual([100, 50, 20, -50]))
    assert years == 3
    assert g == pytest.approx(-1.5)


def test_growth_5y_stale():
    assert growth_5y(annual([100, 110, 120, 130], last_year=2020), today=D(2026, 10, 1)) == (None, None)


def test_growth_5y_shifted_fiscal_year():
    series = [(D(2020, 6, 30), 100), (D(2021, 7, 2), 110), (D(2022, 7, 1), 120), (D(2023, 6, 30), 130),
              (D(2024, 6, 28), 150), (D(2025, 6, 27), 180)]
    g, years = growth_5y(series)
    assert years == 5 and g == pytest.approx(0.8)


def test_revenue_growth_from_trailing_history():
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
    hist = history_from_rows([
        ("A", "revenue", "2021-12-31", 100), ("A", "revenue", "2022-12-31", 110),
        ("A", "revenue", "2023-12-31", 120), ("A", "revenue", "2024-12-31", 130),
        ("A", "revenue", "2025-12-31", 150), ("A", "revenue", "2020-12-31", 75),
        ("A", "net_income", "2022-12-31", 10), ("A", "net_income", "2025-12-31", 20),
        ("T", "ocf", "2026-06-30", 50), ("T", "capex", "2026-06-30", -20),
        ("T", "net_income", "2026-06-30", 22),
        ("Q", "equity", "2026-06-30", 100), ("Q", "total_debt", "2026-06-30", 40),
    ])
    m = metrics.compute(hist, {"roe": 0.3, "debt_to_equity_pct": 26.0, "peg_5y": 1.4}, today=D(2026, 9, 1))
    assert m["rev_growth_5y"] == pytest.approx(1.0) and m["rev_growth_5y_years"] == 5
    assert m["earn_growth_5y_years"] == 3 and m["earn_growth_5y"] == pytest.approx(2 ** (5 / 3) - 1)
    assert m["fcf_ttm"] == 30
    assert m["roe"] == 0.3
    assert m["debt_to_equity"] == pytest.approx(0.26)
    assert m["peg_5y"] == 1.4
    assert m["latest_fy_end"] == "2025-12-31"


def test_compute_falls_back_to_statements():
    hist = history_from_rows([
        ("T", "net_income", "2026-06-30", 25), ("Q", "equity", "2026-06-30", 100),
        ("Q", "total_debt", "2026-06-30", 50), ("T", "fcf", "2026-06-30", -5),
    ])
    m = metrics.compute(hist, {"peg_ratio": 0.9}, today=D(2026, 9, 1))
    assert m["roe"] == pytest.approx(0.25)
    assert m["debt_to_equity"] == pytest.approx(0.5)
    assert m["fcf_ttm"] == -5
    assert m["peg_5y"] == 0.9
    assert m["rev_growth_5y"] is None
