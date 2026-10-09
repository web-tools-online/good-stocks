import pytest

from stockdb import fx, sec


def test_parse_and_combine_frames():
    revenues_2020 = sec.parse_frame({"data": [{"cik": 1, "end": "2020-12-31", "val": 100},
                                              {"cik": 2, "end": "2020-06-30", "val": 50}]})
    contract_2020 = sec.parse_frame({"data": [{"cik": 1, "end": "2020-12-31", "val": 90},
                                              {"cik": 3, "end": "2020-12-31", "val": 7}]})
    contract_2021 = sec.parse_frame({"data": [{"cik": 1, "end": "2021-12-31", "val": 120}]})
    combined = sec.combine_frames([{2020: revenues_2020}, {2020: contract_2020, 2021: contract_2021}])
    assert combined[1] == {"2020-12-31": 100.0, "2021-12-31": 120.0}  # Revenues wins in 2020
    assert combined[2] == {"2020-06-30": 50.0}
    assert combined[3] == {"2020-12-31": 7.0}


def test_validated_extension():
    yahoo = {"2022-12-31": 120.0, "2023-12-31": 130.0, "2024-12-31": 140.0, "2025-12-31": 150.0}
    sec_values = {"2019-12-31": 90.0, "2020-12-31": 100.0, "2021-12-31": 110.0, "2022-12-31": 120.5,
                  "2025-12-31": 150.0}
    assert sec.validated_extension(yahoo, sec_values) == {"2019-12-31": 90.0, "2020-12-31": 100.0,
                                                          "2021-12-31": 110.0}


def test_validated_extension_rejects_mismatch():
    yahoo = {"2024-12-31": 140.0, "2025-12-31": 150.0}
    assert sec.validated_extension(yahoo, {"2020-12-31": 100.0, "2025-12-31": 120.0}) == {}
    # no overlapping year -> cannot validate
    assert sec.validated_extension(yahoo, {"2020-12-31": 100.0}) == {}


ECB = """<?xml version="1.0" encoding="UTF-8"?>
<gesmes:Envelope xmlns:gesmes="http://www.gesmes.org/xml/2002-08-01" xmlns="http://www.ecb.int/vocabulary/2002-08-01/eurofxref">
<Cube><Cube time='2026-10-09'>
<Cube currency='USD' rate='1.1000'/><Cube currency='CZK' rate='24.500'/><Cube currency='GBP' rate='0.8500'/>
</Cube></Cube></gesmes:Envelope>"""


def test_parse_ecb():
    date, rates = fx.parse_ecb_xml(ECB)
    assert date == "2026-10-09"
    assert rates["USD"] == 1.0
    assert rates["EUR"] == pytest.approx(1.1)
    assert rates["CZK"] == pytest.approx(1.1 / 24.5)
    assert fx.usd_rate(rates, "GBp") == pytest.approx(1.1 / 0.85 / 100)
    assert fx.usd_rate(rates, None) is None
