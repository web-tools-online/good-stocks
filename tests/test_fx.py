import pytest

from stockdb import fx


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
