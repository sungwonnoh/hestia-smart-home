"""v2 Phase 9 — 실제 t0 adapter (Gate D). 실제 Context Engine 없이 fixture 로 검증."""

import json
import logging

import pytest

from baseline import (
    build_model,
    parse_t0_line,
    parse_t0_record,
    t0_to_kde_samples,
    t0_to_minutes,
)
from kde_fixtures import DAY0, t0_days, t0_record
from model_payload import build_kde_payload, validate_kde_payload


# KDE_REFACTOR_TASKS_v2 의 production t0 예시 (hestia/log/t0)
MQTT_T0 = {
    "version": 1,
    "sent_ts": 1755501300,
    "src_id": "rpi5",
    "date": "2026-09-25",
    "type": "meal",
    "t0": 1755499200,
    "source": "sensor",
    "prompted": False,
    "duration_sec": 2100,
}


def test_fixture_day0_is_kst_midnight():
    assert t0_to_minutes(DAY0) == 0


# ============================================================ 입력 형식


def test_mqtt_envelope_is_ignored():
    """봉투 필드(version / sent_ts / src_id)가 붙어 와도 같은 레코드다."""
    s = parse_t0_record(MQTT_T0)
    assert (s.type, s.t0, s.duration_sec, s.source, s.prompted) == ("meal", 1755499200.0, 2100.0, "sensor", False)
    assert parse_t0_line(json.dumps(MQTT_T0)) == s


def test_record_and_line_parse_the_same():
    record = t0_record(3, "wake", 7 * 60)
    assert parse_t0_record(record) == parse_t0_line(json.dumps(record))


@pytest.mark.parametrize("obj", [None, [], "meal", 1])
def test_non_object_record_rejected(obj):
    with pytest.raises(ValueError):
        parse_t0_record(obj)


# ============================================================ mapping


def test_meal_mapping():
    [k] = t0_to_kde_samples([parse_t0_record(t0_record(0, "meal", 8 * 60 + 20, duration_sec=1500))])
    assert (k.distribution, k.value) == ("meal_time", pytest.approx(500))


def test_wake_mapping():
    [k] = t0_to_kde_samples([parse_t0_record(t0_record(0, "wake", 6 * 60 + 45))])
    assert (k.distribution, k.value) == ("wake_time", pytest.approx(405))


def test_hydration_mapping_uses_duration():
    """hydration 은 t0(시각)가 아니라 기상 후 경과(duration_sec)다."""
    [k] = t0_to_kde_samples([parse_t0_record(t0_record(0, "hydration", 7 * 60 + 11, duration_sec=660))])
    assert (k.distribution, k.value) == ("hydration_lag", 11.0)


def test_sleep_is_not_mapped():
    """현재 t0 명세에 sleep type 이 없다 — 임의로 sleep_time 에 넣지 않는다."""
    records = [parse_t0_record(t0_record(d, "sleep", 23 * 60)) for d in range(5)]
    assert t0_to_kde_samples(records) == []


def test_unknown_type_is_safe(caplog):
    records = [parse_t0_record(t0_record(0, "medication", 9 * 60))] + t0_days(3)
    with caplog.at_level(logging.INFO):
        converted = t0_to_kde_samples(records)
    assert len(converted) == 9
    assert "medication" in caplog.text


# ============================================================ Gate D


def test_gate_d_t0_to_payload():
    """Gate D: meal / wake / hydration t0 → 기존 KDE fitting → payload"""
    samples = t0_to_kde_samples(t0_days(21))
    model = build_model(samples)

    assert list(model["distributions"]) == ["wake_time", "meal_time", "hydration_lag"]
    assert model["skipped"] == {"sleep_time": "표본 없음"}
    assert model["sample_days"] == 21
    assert {m["sources"][0] for m in model["meta"].values()} == {"sensor"}

    payload = build_kde_payload(model)
    validate_kde_payload(payload)

    wake = payload["distributions"]["wake_time"]["density"]
    assert max(range(96), key=wake.__getitem__) in (27, 28)          # 06:45~07:15
    lag = payload["distributions"]["hydration_lag"]["density"]
    assert max(range(24), key=lag.__getitem__) in (1, 2, 3)          # 5~20분


def test_gate_d_with_sleep_and_unknown_records():
    """sleep / 알 수 없는 type 이 섞여도 결과가 같다."""
    clean = t0_days(21)
    noisy = clean + [parse_t0_record(t0_record(d, t, 23 * 60)) for d in range(21) for t in ("sleep", "medication")]
    a = build_model(t0_to_kde_samples(clean))
    b = build_model(t0_to_kde_samples(noisy))
    assert a["distributions"] == b["distributions"]
    assert a["predictability"] == b["predictability"]


def test_prompted_is_preserved_through_adapter():
    """Phase 11 감쇠를 위해 보존한다 (아직 가중치에는 쓰지 않음)."""
    records = [parse_t0_record(t0_record(0, "meal", 8 * 60, duration_sec=900, prompted=True))]
    assert [k.prompted for k in t0_to_kde_samples(records)] == [True]
