# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests of the offline *vin* engine (searx/engines/vin.py)."""

from searx.engines import vin

# North American VINs: the check digit (9th character) is mandatory and the
# algorithm of ISO 3779 has to reproduce the character of the VIN.
NA_VINS = (
    "4JGBB8GB1BA700174",  # 2011 Mercedes-Benz ML 350 4MATIC (Tuscaloosa)
    "1HGCM82633A004352",  # 2003 Honda Accord
)

# European VIN: the 9th character is *not* a check digit.
EU_VIN = "WDC1569121J092219"  # Mercedes-Benz GLA 180 d (156.912)


def test_check_digit():
    for v in NA_VINS:
        assert vin.check_digit(v) == v[8], v


def test_vin_regex():
    assert vin.VIN_RE.search(EU_VIN).group(1) == EU_VIN
    assert vin.VIN_RE.search(f"проверь VIN {EU_VIN} пожалуйста").group(1) == EU_VIN
    # a VIN never contains the letters I, O or Q
    assert vin.VIN_RE.search("WDC1569121J09221I") is None
    assert vin.VIN_RE.search("too short") is None


def test_decode_european_vin():
    html = vin.decode(EU_VIN)
    assert "GLA 180 d" in html
    assert "156.912" in html
    assert "Sindelfingen" in html
    assert "092219" in html
    # the check digit of a European VIN does not match ISO 3779 ...
    assert "6" in html
    # ... which is normal and must be explained in the answer
    assert "не является признаком подделки" in html


def test_decode_north_american_vin():
    html = vin.decode("4JGBB8GB1BA700174")
    assert "W166" in html
    assert "ML 350 4MATIC" in html
    assert "Tuscaloosa" in html
    assert "2011" in html  # model year (position 10)


def test_european_vin_has_no_check_digit():
    # a European Mercedes VIN: the 9th character is part of the type number
    # (213.242 -> E 220 d), the ISO 3779 check digit does not match
    v = "WDD2132421A150839"
    assert vin.check_digit(v) != v[8]
    assert "не является признаком подделки" in vin.decode(v)


def test_search_returns_an_answer():
    res = vin.search(f"расшифруй {EU_VIN}", {})  # type: ignore
    assert len(res) == 1
    assert "GLA 180 d" in res[0].answer

    # a query without a VIN does not produce an answer
    assert len(vin.search("mercedes gla 180 d", {})) == 0  # type: ignore


def test_year_candidates():
    # digits 1-9 are 2001-2009 or 2031-2039, letters are 1980-2000 or 2010-2030
    assert vin.year_candidates("8") == [2008, 2038]
    assert vin.year_candidates("A") == [1980, 2010]
    # 0, I, O, Q, U and Z are not valid model year codes
    for bad in "0IOQUZ":
        assert vin.year_candidates(bad) == []


def test_invalid_model_year_is_reported():
    # NHTSA reports the same problem for this VIN (position 10 = "0")
    html = vin.decode("JTJCKBFZ802034519")
    assert "недопустимый символ" in html
    assert "2010" not in html


def test_lexus_wmi():
    html = vin.decode("JTJCKBFZ802034519")
    assert "Lexus" in html
    assert "Japan" in html or "Япония" in html
