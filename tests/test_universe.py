from stockdb.universe import parse_nasdaq_directory, _sample_per_exchange

NASDAQ = """Symbol|Security Name|Market Category|Test Issue|Financial Status|Round Lot Size|ETF|NextShares
AAPL|Apple Inc. - Common Stock|Q|N|N|100|N|N
AAPLW|Some Corp - Warrant|Q|N|N|100|N|N
QQQ|Invesco QQQ Trust, Series 1|G|N|N|100|Y|N
ZXZZT|NASDAQ TEST STOCK|G|Y|N|100|N|N
ASML|ASML Holding N.V. - New York Registry Shares|Q|N|N|100|N|N
ABCDU|ABCD Acquisition Corp - Units|G|N|N|100|N|N
File Creation Time: 1009202618:01|||||||
"""

OTHER = """ACT Symbol|Security Name|Exchange|CQS Symbol|ETF|Round Lot Size|Test Issue|NASDAQ Symbol
BRK.B|Berkshire Hathaway Inc. Class B|N|BRK.B|N|100|N|BRK.B
ABR$D|Arbor Realty Trust 6.375% Series D Cumulative Redeemable Preferred Stock|N|ABRpD|N|100|N|ABR-D
SPY|SPDR S&P 500 ETF Trust|P|SPY|Y|100|N|SPY
GE|GE Aerospace Common Stock|N|GE|N|100|N|GE
XFLT|XAI Octagon Floating Rate & Alternative Income Trust Fund|N|XFLT|N|100|N|XFLT
File Creation Time: 1009202618:01|||||||
"""


def test_nasdaq_listed():
    rows = parse_nasdaq_directory(NASDAQ, "nasdaq")
    assert [r["symbol"] for r in rows] == ["AAPL", "ASML"]
    assert rows[0]["name"] == "Apple Inc."
    assert rows[0]["market"] == "US"


def test_other_listed():
    rows = parse_nasdaq_directory(OTHER, "other")
    assert [r["symbol"] for r in rows] == ["BRK-B", "GE"]
    assert rows[0]["exchange_name"] == "NYSE"


def test_sample_per_exchange():
    rows = [{"symbol": f"{x}{i}", "exchange": x} for x in "ABC" for i in range(5)]
    sample = _sample_per_exchange(rows, 4)
    assert [r["symbol"] for r in sample] == ["A0", "B0", "C0", "A1"]
