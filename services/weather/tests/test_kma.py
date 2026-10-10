from __future__ import annotations

import pytest

from conftest import KEY_ERROR_XML, FakeKma, kst, ncst_response
from hestia_weather.grid import latlon_to_grid
from hestia_weather.kma import KmaClient, KmaError, normalize_key


def test_grid_matches_kma_table():
    assert latlon_to_grid(37.5665, 126.9780) == (60, 127)     # 서울시청
    assert latlon_to_grid(35.1796, 129.0756) == (98, 76)      # 부산시청


def test_latest_uses_this_hour():
    fake = FakeKma({"1400": ncst_response("20261010", "1400", T1H="31.2", REH="58")})
    obs = KmaClient("key", 60, 127, fetch=fake).latest(kst(2026, 10, 10, 14, 15))

    assert obs.observed_at == int(kst(2026, 10, 10, 14, 0))
    assert obs.temperature_c == 31.2
    assert obs.humidity_pct == 58
    assert obs.precip_type == "NONE"
    assert fake.params() == {
        "serviceKey": "key", "pageNo": "1", "numOfRows": "100", "dataType": "JSON",
        "base_date": "20261010", "base_time": "1400", "nx": "60", "ny": "127",
    }


def test_latest_falls_back_to_previous_hour_when_not_yet_published():
    fake = FakeKma({"1300": ncst_response("20261010", "1300")})
    obs = KmaClient("key", 60, 127, fetch=fake).latest(kst(2026, 10, 10, 14, 3))

    assert obs.observed_at == int(kst(2026, 10, 10, 13, 0))
    assert [fake.params(i)["base_time"] for i in range(2)] == ["1400", "1300"]


def test_previous_hour_crosses_midnight():
    fake = FakeKma({"2300": ncst_response("20261009", "2300")})
    obs = KmaClient("key", 60, 127, fetch=fake).latest(kst(2026, 10, 10, 0, 5))

    assert obs.observed_at == int(kst(2026, 10, 9, 23, 0))
    assert fake.params()["base_date"] == "20261009"


def test_no_data_for_both_hours_is_error():
    with pytest.raises(KmaError):
        KmaClient("key", 60, 127, fetch=FakeKma()).latest(kst(2026, 10, 10, 14, 3))


def test_key_error_xml_is_error():
    fake = FakeKma({"1400": KEY_ERROR_XML})
    with pytest.raises(KmaError, match="JSON 이 아닌"):
        KmaClient("key", 60, 127, fetch=fake).latest(kst(2026, 10, 10, 14, 15))


def test_missing_and_precip_values():
    fake = FakeKma({"1400": ncst_response("20261010", "1400", T1H="-998.9", RN1="2.5", PTY="1")})
    obs = KmaClient("key", 60, 127, fetch=fake).latest(kst(2026, 10, 10, 14, 15))

    assert obs.temperature_c is None
    assert obs.precip_1h_mm == 2.5
    assert obs.precip_type == "RAIN"


def test_encoding_key_is_not_double_encoded():
    assert normalize_key("abc%2Bdef%3D%3D") == "abc+def=="
    assert normalize_key(" abc+def== ") == "abc+def=="

    fake = FakeKma({"1400": ncst_response("20261010", "1400")})
    KmaClient("abc%2Bdef%3D%3D", 60, 127, fetch=fake).latest(kst(2026, 10, 10, 14, 15))
    assert fake.params()["serviceKey"] == "abc+def=="


def test_empty_key_rejected():
    with pytest.raises(ValueError):
        KmaClient("", 60, 127)
