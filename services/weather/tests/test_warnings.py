"""기상특보 통보문(t6) 파싱. 문자열은 2026-10-05~07 실제 응답(stnId=108)에서 가져왔다."""

from __future__ import annotations

import pytest

from conftest import KEY_ERROR_XML, FakeKma, kst, warning_response
from hestia_weather.kma import KmaClient, KmaError, parse_t6

# tmSeq 17 (2026-10-06 04:00)
T6_SEA_WIND_COLD = (
    "o 풍랑주의보 : 남해동부바깥먼바다, 제주도앞바다(제주도서부앞바다), 제주도남쪽바깥먼바다, "
    "제주도남동쪽안쪽먼바다, 제주도남서쪽안쪽먼바다 o 한파주의보 : 강원도(태백, 평창산지)"
)
# tmSeq 16 — 구역 안에 괄호와 쉼표가 겹친다
T6_NESTED = (
    "o 강풍주의보 : 서해5도(백령도.대청도), 충청남도(보령도서), 전라남도(흑산도.홍도), "
    "전북자치도(부안위도면, 군산옥도면(어청도 제외), 군산어청도) o 풍랑주의보 : 서해남부전해상, "
    "서해중부앞바다(충남북부앞바다, 충남남부앞바다) o 한파주의보 : 강원도(태백, 평창산지)"
)
# tmSeq 21 (2026-10-07 04:00) — 특보 없음. '없 음' 사이에 공백이 있다
T6_NONE = "o 없 음"


def test_parse_entries():
    ws = parse_t6(T6_SEA_WIND_COLD)
    assert [(w.name, w.type, w.level) for w in ws] == [
        ("풍랑주의보", "HIGH_SEAS", "ADVISORY"),
        ("한파주의보", "COLD_WAVE", "ADVISORY"),
    ]
    assert ws[1].areas == "강원도(태백, 평창산지)"


def test_parse_nested_parentheses():
    ws = parse_t6(T6_NESTED)
    assert [w.name for w in ws] == ["강풍주의보", "풍랑주의보", "한파주의보"]
    assert ws[0].areas.endswith("군산어청도)")


@pytest.mark.parametrize("t6", [T6_NONE, "o 없음", "", "  "])
def test_parse_none(t6):
    assert parse_t6(t6) == []


@pytest.mark.parametrize("name, type_, level", [
    ("폭염주의보", "HEAT", "ADVISORY"),
    ("폭염경보", "HEAT", "WARNING"),
    ("폭염중대경보", "HEAT", "SEVERE_WARNING"),
    ("호우경보", "HEAVY_RAIN", "WARNING"),
    ("폭풍해일주의보", "STORM_SURGE", "ADVISORY"),
    ("이상한특보", "OTHER", "OTHER"),
])
def test_type_and_level(name, type_, level):
    (w,) = parse_t6(f"o {name} : 서울")
    assert (w.type, w.level) == (type_, level)


# ==================================================================== 조회


def client(fake: FakeKma) -> KmaClient:
    return KmaClient("key", 60, 127, fetch=fake)


def test_latest_message_and_home_area_only():
    fake = FakeKma(warning=warning_response(
        {"tmFc": "202607220500", "tmSeq": "3", "t6": "o 폭염주의보 : 서울, 경기도(수원)"},
        {"tmFc": "202607221100", "tmSeq": "4",
         "t6": "o 폭염경보 : 서울, 대구 o 호우주의보 : 강원도(태백)"},
    ))
    w = client(fake).warnings(kst(2026, 7, 22, 11, 30), ["서울"])

    assert [x.to_payload() for x in w.items] == [{"name": "폭염경보", "type": "HEAT", "level": "WARNING"}]
    assert w.issued_at == int(kst(2026, 7, 22, 11, 0))


def test_same_tmfc_uses_higher_seq():
    fake = FakeKma(warning=warning_response(
        {"tmFc": "202607221100", "tmSeq": "9", "t6": "o 폭염경보 : 서울"},
        {"tmFc": "202607221100", "tmSeq": "10", "t6": "o 없 음"},
    ))
    assert client(fake).warnings(kst(2026, 7, 22, 11, 30), ["서울"]).items == ()


def test_other_regions_only_is_empty():
    fake = FakeKma(warning=warning_response({"tmFc": "202610060400", "tmSeq": "17", "t6": T6_SEA_WIND_COLD}))
    assert client(fake).warnings(kst(2026, 10, 6, 5, 0), ["서울"]).items == ()


def test_no_recent_message_is_empty():
    w = client(FakeKma()).warnings(kst(2026, 10, 10, 12, 0), ["서울"])
    assert (w.items, w.issued_at) == ((), None)


def test_request_stays_within_six_days():
    fake = FakeKma()
    client(fake).warnings(kst(2026, 10, 10, 0, 30), ["서울"])
    p = fake.params()
    assert (p["stnId"], p["fromTmFc"], p["toTmFc"]) == ("108", "20261005", "20261010")


def test_error_is_raised():
    fake = FakeKma(warning=KEY_ERROR_XML)
    with pytest.raises(KmaError):
        client(fake).warnings(kst(2026, 10, 10, 12, 0), ["서울"])
