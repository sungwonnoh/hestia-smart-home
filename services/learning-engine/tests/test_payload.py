"""v2 Phase 8 — hestia/model/kde payload / 발행."""

import copy
import json
import math
import sys
from pathlib import Path

import pytest

import mqtt_publisher
from baseline import build_model
from kde_fixtures import all_distributions
from model_payload import (
    PAYLOAD_KEYS,
    TOPIC,
    PayloadError,
    build_kde_payload,
    missing_distributions,
    stamp_sent,
    to_json,
    validate_kde_payload,
)


CONTEXT_ENGINE = Path(__file__).resolve().parents[3] / "services" / "context-engine"


@pytest.fixture(scope="module")
def full_payload():
    return build_kde_payload(build_model(all_distributions()), trained_at=1790280000, sent_ts=1790296800)


def broken(payload, mutate):
    p = copy.deepcopy(payload)
    mutate(p)
    return p


# ============================================================ 구조


def test_required_fields(full_payload):
    assert list(full_payload) == list(PAYLOAD_KEYS)
    assert (full_payload["version"], full_payload["src_id"]) == (1, "rpi4")
    assert (full_payload["trained_at"], full_payload["sent_ts"]) == (1790280000, 1790296800)
    assert full_payload["sample_days"] > 0


def test_gate_c_all_distributions(full_payload):
    """Gate C: 4개 분포가 하나의 payload 에 들어간다."""
    names = ["wake_time", "sleep_time", "meal_time"]
    assert list(full_payload["distributions"]) == names
    assert list(full_payload["predictability"]) == names
    validate_kde_payload(full_payload, require_all=True)


def test_grids(full_payload):
    d = full_payload["distributions"]
    for name in ("wake_time", "sleep_time", "meal_time"):
        assert (d[name]["grid_min"], d[name]["grid_step"], len(d[name]["density"])) == (0, 15, 96)


def test_model_meta_is_not_in_payload(full_payload):
    """meta / skipped 는 보고용 — MQTT 로 나가지 않는다."""
    assert "meta" not in full_payload and "skipped" not in full_payload
    assert set(full_payload["distributions"]["meal_time"]) == {"grid_min", "grid_step", "density"}


def test_json_round_trip(full_payload):
    assert json.loads(to_json(full_payload)) == full_payload


def test_default_model_is_partial_but_valid():
    """기본(breakfast)은 meal_time 만 — 키를 빼고 보내며 경고만 남긴다."""
    from baseline import load_aruba_samples
    breakfast = Path(__file__).resolve().parents[3] / "data" / "processed" / "aruba" / "breakfast_preparation.csv"
    payload = build_kde_payload(build_model(load_aruba_samples(breakfast)))
    assert list(payload["distributions"]) == ["meal_time"]
    assert missing_distributions(payload) == ["wake_time", "sleep_time"]
    with pytest.raises(PayloadError, match="누락"):
        validate_kde_payload(payload, require_all=True)


def test_stamp_sent_changes_only_sent_ts(full_payload):
    stamped = stamp_sent(full_payload, 1790300000)
    assert stamped["sent_ts"] == 1790300000
    assert {k: v for k, v in stamped.items() if k != "sent_ts"} == {
        k: v for k, v in full_payload.items() if k != "sent_ts"
    }
    assert full_payload["sent_ts"] == 1790296800       # 원본은 그대로


# ============================================================ 방어


def _set(path, value):
    def mutate(p):
        target = p
        for key in path[:-1]:
            target = target[key]
        target[path[-1]] = value
    return mutate


def _del(path):
    def mutate(p):
        target = p
        for key in path[:-1]:
            target = target[key]
        del target[path[-1]]
    return mutate


@pytest.mark.parametrize(
    "mutate, match",
    [
        (_del(["trained_at"]), "누락"),
        (_set(["meta"], {}), "명세에 없는"),
        (_set(["version"], 2), "version"),
        (_set(["version"], True), "version"),
        (_set(["src_id"], "rpi5"), "src_id"),
        (_set(["sent_ts"], 0), "sent_ts"),
        (_set(["trained_at"], math.nan), "trained_at"),
        (_set(["sample_days"], -1), "sample_days"),
        (_set(["sample_days"], True), "sample_days"),
        (_set(["distributions"], {}), "distributions"),
        (_set(["distributions", "medication_time"], {}), "알 수 없는"),
        (_set(["distributions", "meal_time", "grid_step"], 10), "격자"),
        (_set(["distributions", "meal_time", "extra"], 1), "grid_min / grid_step / density"),
        (_set(["distributions", "meal_time", "density"], []), "비었거나"),
        (_set(["distributions", "meal_time", "density"], [1 / 95] * 95), "96칸"),
        (_set(["distributions", "meal_time", "density"], [math.nan] + [1 / 95] * 95), "NaN"),
        (_set(["distributions", "meal_time", "density"], [-0.01, 0.01 + 1 / 96] + [1 / 96] * 94), "음수"),
        (_set(["distributions", "meal_time", "density"], [0.5 / 96] * 96), "합이 1"),
        (_set(["distributions", "sleep_time", "density"], ["0.1"] * 96), "숫자"),
        (_set(["distributions", "hydration_lag"], {"grid_min": 0, "grid_step": 5, "density": [1.0]}), "알 수 없는"),
        (_del(["predictability", "meal_time"]), "이름이 다릅니다"),
        (_set(["predictability", "meal_time"], 1.5), "0~1"),
        (_set(["predictability", "meal_time"], math.inf), "0~1"),
    ],
)
def test_invalid_payload_is_rejected(full_payload, mutate, match):
    with pytest.raises(PayloadError, match=match):
        validate_kde_payload(broken(full_payload, mutate))


def test_non_serializable_is_rejected():
    with pytest.raises(PayloadError, match="직렬화"):
        to_json({"x": object()})


# ============================================================ 발행


class FakeInfo:
    def __init__(self, rc=0, published=True):
        self.rc = rc
        self._published = published
        self.waited = None

    def wait_for_publish(self, timeout=None):
        self.waited = timeout

    def is_published(self):
        return self._published


class FakeClient:
    def __init__(self, info=None):
        self.info = info or FakeInfo()
        self.calls = []

    def connect(self, host, port, keepalive):
        self.calls.append(("connect", host, port))

    def loop_start(self):
        self.calls.append(("loop_start",))

    def loop_stop(self):
        self.calls.append(("loop_stop",))

    def publish(self, topic, body, qos, retain):
        self.calls.append(("publish", topic, body, qos, retain))
        return self.info

    def disconnect(self):
        self.calls.append(("disconnect",))


def test_publish_qos1_retained(full_payload):
    client = FakeClient()
    sent = mqtt_publisher.publish_kde_model(full_payload, "broker", 1883, client_factory=lambda: client, timeout=3)

    names = [c[0] for c in client.calls]
    assert names == ["connect", "loop_start", "publish", "loop_stop", "disconnect"]
    _, topic, body, qos, retain = client.calls[2]
    assert (topic, qos, retain) == (TOPIC, 1, True) == ("hestia/model/kde", 1, True)
    assert json.loads(body) == sent
    assert client.info.waited == 3


def test_publish_stamps_sent_ts_at_send_time(full_payload):
    sent = mqtt_publisher.publish_kde_model(full_payload, client_factory=FakeClient)
    assert sent["sent_ts"] >= full_payload["sent_ts"]
    assert sent["trained_at"] == full_payload["trained_at"]


def test_publish_validates_before_connecting(full_payload):
    client = FakeClient()
    bad = broken(full_payload, _set(["predictability", "meal_time"], 2.0))
    with pytest.raises(PayloadError):
        mqtt_publisher.publish_kde_model(bad, client_factory=lambda: client)
    assert client.calls == []


@pytest.mark.parametrize("info, match", [(FakeInfo(rc=4), "rc=4"), (FakeInfo(published=False), "PUBACK")])
def test_publish_failure_still_disconnects(full_payload, info, match):
    client = FakeClient(info)
    with pytest.raises(mqtt_publisher.PublishError, match=match):
        mqtt_publisher.publish_kde_model(full_payload, client_factory=lambda: client)
    assert [c[0] for c in client.calls][-2:] == ["loop_stop", "disconnect"]


def test_default_client_uses_callback_api_v2():
    pytest.importorskip("paho.mqtt")
    import paho.mqtt.client as mqtt
    client = mqtt_publisher.default_client_factory()
    assert client._callback_api_version == mqtt.CallbackAPIVersion.VERSION2


# ============================================================ Context Engine 계약


@pytest.mark.skipif(sys.version_info < (3, 11), reason="context-engine 은 Python 3.11+")
def test_context_engine_accepts_payload(full_payload):
    """RPi5 가 실제로 받아 ModelStore 에 보관하는지 — 거부되면 학습이 조용히 사라진다."""
    sys.path.insert(0, str(CONTEXT_ENGINE))
    try:
        from hestia_engine.messages import parse
        from hestia_engine.model import ModelStore, query
    finally:
        sys.path.remove(str(CONTEXT_ENGINE))

    msg = parse(TOPIC, to_json(full_payload).encode("utf-8"), recv_ts=1790296801.0)
    assert msg is not None

    store = ModelStore()
    assert store.apply(msg) is True
    assert store.sample_days() == full_payload["sample_days"]
    for name in full_payload["distributions"]:
        assert store.distribution(name) is not None
    assert query(store, "meal_time", 12 * 60)["predictability"] == pytest.approx(
        full_payload["predictability"]["meal_time"]
    )
    assert 0 <= query(store, "sleep_time", 23 * 60)["tail_probability"] <= 1
