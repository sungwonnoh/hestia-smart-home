from __future__ import annotations

from conftest import FakeKma, kst, ncst_response, warning_response
from hestia_weather.kma import KmaClient, KmaError
from hestia_weather.service import Location, WeatherService

SEOUL = Location("서울", 60, 127, warning_areas=("서울",))
SEOUL_NO_WARN = Location("서울", 60, 127)

HEAT = warning_response({"tmFc": "202610101400", "tmSeq": "1", "t6": "o 폭염경보 : 서울"})
NONE = warning_response({"tmFc": "202610101400", "tmSeq": "1", "t6": "o 없 음"})


def make(fake: FakeKma, location: Location = SEOUL_NO_WARN, **kw) -> WeatherService:
    return WeatherService(KmaClient("key", 60, 127, fetch=fake), location, **kw)


# ==================================================================== 실황


def test_first_poll_is_immediate_and_new():
    fake = FakeKma({"1400": ncst_response("20261010", "1400")})
    svc = make(fake)
    now = kst(2026, 10, 10, 14, 20)

    assert svc.due(now)
    assert svc.poll(now) is True
    # 이번 정시 자료를 받았으니 다음 정시 + 12분
    assert svc.next_poll == kst(2026, 10, 10, 15, 12)


def test_same_observation_is_not_new():
    fake = FakeKma({"1400": ncst_response("20261010", "1400")})
    svc = make(fake)
    svc.poll(kst(2026, 10, 10, 14, 20))

    assert svc.poll(kst(2026, 10, 10, 14, 40)) is False


def test_retries_until_this_hour_is_published():
    fake = FakeKma({"1300": ncst_response("20261010", "1300")})
    svc = make(fake, retry_sec=120)

    assert svc.poll(kst(2026, 10, 10, 14, 13)) is True
    assert svc.cache.observed_at == int(kst(2026, 10, 10, 13, 0))
    assert svc.next_poll == kst(2026, 10, 10, 14, 15)

    fake.responses["1400"] = ncst_response("20261010", "1400", T1H="25.0")
    assert svc.poll(kst(2026, 10, 10, 14, 15)) is True
    assert svc.cache.observed_at == int(kst(2026, 10, 10, 14, 0))
    assert svc.next_poll == kst(2026, 10, 10, 15, 12)


def test_early_in_hour_waits_for_settle():
    fake = FakeKma({"1300": ncst_response("20261010", "1300")})
    svc = make(fake, retry_sec=120)
    svc.poll(kst(2026, 10, 10, 14, 1))

    # 14:03 이 아니라 14:12 까지 기다린다
    assert svc.next_poll == kst(2026, 10, 10, 14, 12)


def test_failure_keeps_cache_and_retries():
    fake = FakeKma({"1400": ncst_response("20261010", "1400")})
    svc = make(fake, retry_sec=120)
    svc.poll(kst(2026, 10, 10, 14, 20))
    cached = svc.cache

    fake.fail = KmaError("timeout")
    now = kst(2026, 10, 10, 15, 12)
    assert svc.poll(now) is False
    assert svc.cache == cached
    assert svc.next_poll == now + 120


# ==================================================================== 특보


def test_warning_change_is_new_even_if_observation_same():
    fake = FakeKma({"1400": ncst_response("20261010", "1400")}, warning=NONE)
    svc = make(fake, SEOUL, warn_poll_sec=600)
    assert svc.poll(kst(2026, 10, 10, 14, 20)) is True

    fake.warning = HEAT
    t = kst(2026, 10, 10, 14, 30)
    assert svc.due(t)
    assert svc.poll(t) is True
    assert [w.name for w in svc.warnings.items] == ["폭염경보"]

    # 그대로면 발행할 변화 없음
    assert svc.poll(kst(2026, 10, 10, 14, 40)) is False


def test_warning_failure_keeps_last_and_unknown_before_first():
    fake = FakeKma({"1400": ncst_response("20261010", "1400")})
    fake.warning_fail = KmaError("down")
    svc = make(fake, SEOUL)
    now = kst(2026, 10, 10, 14, 20)
    svc.poll(now)

    p = svc.payload(reason="update", now=now)
    assert (p["warnings"], p["warnings_issued_at"]) == (None, None)     # 모름

    fake.warning_fail = None
    fake.warning = HEAT
    svc.poll(kst(2026, 10, 10, 14, 30))
    fake.warning_fail = KmaError("down")
    svc.poll(kst(2026, 10, 10, 14, 40))
    assert [w.name for w in svc.warnings.items] == ["폭염경보"]          # 실패해도 유지


def test_warnings_disabled_without_areas():
    fake = FakeKma({"1400": ncst_response("20261010", "1400")}, warning=HEAT)
    svc = make(fake, SEOUL_NO_WARN)
    now = kst(2026, 10, 10, 14, 20)
    svc.poll(now)

    assert not any("getWthrWrnMsg" in u for u in fake.urls)
    assert svc.payload(reason="update", now=now)["warnings"] is None


def test_warning_alone_before_first_observation_is_not_published():
    fake = FakeKma(warning=HEAT)
    svc = make(fake, SEOUL)
    assert svc.poll(kst(2026, 10, 10, 14, 20)) is False
    assert svc.warnings is not None


# ==================================================================== get


def test_reply_uses_cache_without_calling_api():
    fake = FakeKma({"1400": ncst_response("20261010", "1400")}, warning=NONE)
    svc = make(fake, SEOUL)
    svc.poll(kst(2026, 10, 10, 14, 20))
    calls = len(fake.urls)

    assert svc.reply(kst(2026, 10, 10, 14, 50)) is True
    assert len(fake.urls) == calls


def test_reply_refreshes_when_empty_or_stale():
    fake = FakeKma({"1400": ncst_response("20261010", "1400")})
    svc = make(fake, stale_sec=5400)

    assert svc.reply(kst(2026, 10, 10, 14, 20)) is True        # 비어 있으면 조회

    fake.responses["1600"] = ncst_response("20261010", "1600")
    assert svc.reply(kst(2026, 10, 10, 16, 20)) is True        # 2시간 넘음 → 조회
    assert svc.cache.observed_at == int(kst(2026, 10, 10, 16, 0))


def test_reply_false_when_never_fetched():
    fake = FakeKma()
    fake.fail = KmaError("down")
    assert make(fake).reply(kst(2026, 10, 10, 14, 20)) is False


# ==================================================================== payload


def test_payload_shape():
    fake = FakeKma(
        {"1400": ncst_response("20261010", "1400", T1H="31.2", REH="58", RN1="0", WSD="1.8")},
        warning=HEAT,
    )
    svc = make(fake, SEOUL)
    now = kst(2026, 10, 10, 14, 20)
    svc.poll(now)

    assert svc.payload(reason="update", now=now) == {
        "version": 1,
        "sent_ts": int(now),
        "src_id": "rpi4",
        "reason": "update",
        "source": "kma",
        "location": {"name": "서울", "nx": 60, "ny": 127},
        "observed_at": int(kst(2026, 10, 10, 14, 0)),
        "temperature_c": 31.2,
        "humidity_pct": 58.0,
        "precip_1h_mm": 0.0,
        "precip_type": "NONE",
        "wind_speed_ms": 1.8,
        "warnings": [{"name": "폭염경보", "type": "HEAT", "level": "WARNING"}],
        "warnings_issued_at": int(kst(2026, 10, 10, 14, 0)),
    }
