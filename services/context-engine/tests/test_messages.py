import json

import pytest

from hestia_engine import messages as m
from hestia_engine.messages import parse

RECV = 1790296800.0


def env(**extra) -> dict:
    """공통 Envelope + 타입별 필드."""
    return {"version": 1, "sent_ts": 1790296798, "src_id": "vs-01", "seq": 1, **extra}


def presence(**extra) -> dict:
    return env(type="presence", present=True, confidence="high", **extra)


def go(topic: str, payload: dict):
    return parse(topic, json.dumps(payload), RECV)


# ============================================================ Envelope


def test_recv_ts_is_injected_not_from_payload():
    msg = go("hestia/sensor/vs-01/state", presence())
    assert msg.recv_ts == RECV
    assert msg.sent_ts == 1790296798.0      # 별개로 보존


def test_src_id_mismatch_rejected():
    """토픽의 vid 와 payload 의 src_id 가 다르면 버린다.

    통과시키면 World State 가 엉뚱한 센서에 값을 쓴다.
    """
    assert go("hestia/sensor/vs-09/state", presence()) is None


def test_missing_required_field_rejected():
    p = presence()
    del p["present"]
    assert go("hestia/sensor/vs-01/state", p) is None


def test_missing_envelope_field_rejected():
    p = presence()
    del p["sent_ts"]
    assert go("hestia/sensor/vs-01/state", p) is None


def test_version_mismatch_is_accepted(caplog):
    """거부하지 않는다 — 공통 필드는 여전히 유효하다. 경고만 남긴다."""
    m._warned_versions.clear()
    p = presence()
    p["version"] = 2
    with caplog.at_level("WARNING"):
        msg = go("hestia/sensor/vs-01/state", p)
    assert isinstance(msg, m.PresenceMessage)
    assert any("버전 불일치" in r.getMessage() for r in caplog.records)


def test_version_warning_only_once_per_node():
    m._warned_versions.clear()
    p = presence()
    p["version"] = 2
    for _ in range(3):
        go("hestia/sensor/vs-01/state", p)
    assert len(m._warned_versions) == 1


# ============================================================ 타입 검사


def test_bool_does_not_pass_as_number():
    """isinstance(True, int) 가 True 라서 명시적으로 막아야 한다.

    막지 않으면 watt=True 가 1.0 이 되어 조용히 틀린다.
    """
    p = env(type="power", watt=True)
    assert go("hestia/sensor/vs-01/state", p) is None


def test_int_accepted_where_float_expected():
    p = env(type="power", watt=1180)
    msg = go("hestia/sensor/vs-01/state", p)
    assert msg.watt == 1180.0
    assert isinstance(msg.watt, float)


def test_float_rejected_where_int_expected():
    assert go("hestia/sensor/vs-01/state", presence(energy=42.5)) is None


def test_wrong_type_rejected():
    p = presence()
    p["present"] = "true"
    assert go("hestia/sensor/vs-01/state", p) is None


# ============================================================ _one_of


def test_enum_case_sensitive():
    """펌웨어가 'on' 을 보내면 잡아야 한다.

    통과하면 power == 'ON' 비교가 조용히 False 가 된다.
    """
    p = env(type="power", watt=1180, state="on")
    assert go("hestia/sensor/vs-01/state", p) is None


def test_enum_unknown_value_rejected():
    p = env(type="power", watt=1180, state="IDLE")
    assert go("hestia/sensor/vs-01/state", p) is None


def test_enum_valid_value():
    p = env(type="power", watt=1180, state="ON")
    assert go("hestia/sensor/vs-01/state", p).state == "ON"


# ============================================================ 선택 필드


def test_optional_absent_uses_default():
    msg = go("hestia/sensor/vs-01/state", presence())
    assert msg.energy == 0
    assert msg.distance_cm == 0


def test_optional_enum_absent_is_none():
    """power 의 state 는 선택 — 없으면 RPi5 가 watt 로 판정한다."""
    p = env(type="power", watt=1180)
    assert go("hestia/sensor/vs-01/state", p).state is None


def test_optional_null_treated_as_absent():
    msg = go("hestia/sensor/vs-01/state", presence(energy=None))
    assert msg.energy == 0


def test_unknown_field_ignored():
    """명세: 수신 측은 알 수 없는 필드를 무시한다."""
    msg = go("hestia/sensor/vs-01/state", presence(future_field="x"))
    assert isinstance(msg, m.PresenceMessage)


# ============================================================ 진입점


def test_unsubscribed_topic_silently_none():
    assert go("hestia/context/activity", env()) is None
    assert go("hestia/notify/push", env()) is None


def test_foreign_prefix_none():
    assert go("home/sensor/vs-01/state", presence()) is None


def test_malformed_json_none():
    assert parse("hestia/sensor/vs-01/state", "{not json", RECV) is None


def test_non_object_payload_none():
    assert parse("hestia/sensor/vs-01/state", "[1,2,3]", RECV) is None


def test_unknown_sensor_type_none():
    assert go("hestia/sensor/vs-01/state", env(type="radar")) is None


def test_parse_never_raises():
    """한 노드의 잘못된 페이로드가 엔진을 멈추게 해서는 안 된다."""
    for bad in ("", "null", "{}", '{"version":"x"}', "[]"):
        assert parse("hestia/sensor/vs-01/state", bad, RECV) is None


# ============================================================ 계열별 대표


def test_device_state_roundtrip():
    p = {
        "version": 1, "sent_ts": 1790296798, "src_id": "vd-07", "seq": 64,
        "device_type": "washer", "source": "thinq", "power": "ON",
        "cycle": "RINSE", "remain_min": 18, "door": "CLOSED",
    }
    msg = go("hestia/device/vd-07/state", p)
    assert isinstance(msg, m.WasherState)
    assert (msg.cycle, msg.remain_min, msg.source) == ("RINSE", 18, "thinq")


def test_device_event_roundtrip():
    p = {
        "version": 1, "sent_ts": 1790296798, "src_id": "vd-06", "seq": 13,
        "device_type": "water_purifier", "source": "mock",
        "event_type": "dispensed", "water_type": "COLD", "amount_ml": 200,
    }
    msg = go("hestia/device/vd-06/event", p)
    assert isinstance(msg, m.DispensedEvent)
    assert (msg.water_type, msg.amount_ml) == ("COLD", 200)


def test_node_status_lwt():
    """센서 고장을 부재로 오인하지 않기 위한 안전장치."""
    p = {"version": 1, "sent_ts": 1790296798, "src_id": "esp32-3",
         "online": False, "ts_synced": True}
    msg = go("hestia/node/esp32-3/status", p)
    assert isinstance(msg, m.NodeStatus)
    assert msg.online is False


def test_ts_synced_defaults_false():
    """명세: 미수신 시 false 로 간주."""
    p = {"version": 1, "sent_ts": 1790296798, "src_id": "esp32-3", "online": True}
    assert go("hestia/node/esp32-3/status", p).ts_synced is False


def test_node_announce_emulates():
    p = {
        "version": 1, "sent_ts": 1790296798, "src_id": "esp32-3", "seq": 1,
        "fw": "1.0.2",
        "emulates": [
            {"virtual_id": "vd-05", "device_type": "smart_fridge", "source": "mock"},
            {"virtual_id": "vs-03", "type": "motion", "source": "esp32",
             "default_area": "kitchen", "default_roles": ["MEAL"]},
        ],
    }
    msg = go("hestia/node/esp32-3/announce", p)
    assert len(msg.emulates) == 2
    assert msg.emulates[1].default_roles == ("MEAL",)


def test_registry_devices():
    p = {
        "version": 1, "sent_ts": 1790296798, "src_id": "rpi4",
        "devices": [
            {"virtual_id": "vs-01", "type": "presence", "area": "living",
             "roles": ["LIVING"], "node_id": "esp32-1", "enabled": True},
            {"virtual_id": "vd-06", "device_type": "water_purifier",
             "area": "kitchen", "source": "mock", "channel": False, "enabled": False},
        ],
    }
    msg = go("hestia/registry/devices", p)
    assert msg.devices[0].roles == ("LIVING",)
    assert msg.devices[1].enabled is False        # "이 집엔 정수기가 없다"


def test_profile():
    p = {"version": 1, "sent_ts": 1790296798, "src_id": "rpi4", "profile": "DEMO"}
    assert go("hestia/system/profile", p).profile == "DEMO"


def test_model_keeps_raw_payload():
    """density 배열은 통째로 넘겨 쓰므로 딕셔너리로 유지한다."""
    p = {
        "version": 1, "sent_ts": 1790296798, "src_id": "rpi4",
        "trained_at": 1790280000, "sample_days": 21,
        "distributions": {"wake_time": {"grid_min": 0, "grid_step": 15,
                                        "density": [0.1, 0.2]}},
        "predictability": {"wake_time": 0.78},
    }
    msg = go("hestia/model/kde", p)
    assert msg.name == "kde"
    assert msg.payload["predictability"]["wake_time"] == 0.78


def test_unknown_model_name_none():
    p = {"version": 1, "sent_ts": 1790296798, "src_id": "rpi4", "trained_at": 1}
    assert go("hestia/model/whatever", p) is None

# ============================================================ 디스플레이
def test_display_node_state():
    """ESP32 디스플레이 노드. 명세의 가전 9종에는 없지만
    알림 채널이자 ack 입력 수단이라 파싱되어야 한다."""
    msg = parse("hestia/device/vd-10/state", json.dumps({
        "version": 1, "sent_ts": 1000, "src_id": "vd-10", "seq": 1,
        "device_type": "display_node", "source": "esp32",
        "power": "ON", "display": "물 한 잔 드세요", "notify_id": "n-001",
    }), 2000.0)

    assert isinstance(msg, m.DisplayNodeState)
    assert msg.power == "ON"
    assert msg.display == "물 한 잔 드세요"
    assert msg.notify_id == "n-001"


def test_display_node_empty_display():
    """아무것도 안 띄우고 있으면 빈 문자열이다.

    cancel 로 내렸는지를 이 필드로 확인한다 — null 과 '' 를 구별하지
    않으면 '모른다' 와 '없다' 가 섞인다.
    """
    msg = parse("hestia/device/vd-10/state", json.dumps({
        "version": 1, "sent_ts": 1000, "src_id": "vd-10", "seq": 1,
        "device_type": "display_node", "source": "esp32", "power": "ON",
    }), 2000.0)

    assert msg is not None
    assert msg.display == ""
    assert msg.notify_id is None


def test_display_node_requires_power():
    msg = parse("hestia/device/vd-10/state", json.dumps({
        "version": 1, "sent_ts": 1000, "src_id": "vd-10", "seq": 1,
        "device_type": "display_node", "source": "esp32",
    }), 2000.0)
    assert msg is None

# ============================================================ 전 클래스 생성

MINIMAL = [
    ("hestia/sensor/vs-01/state", presence(), m.PresenceMessage),
    ("hestia/sensor/vs-01/state", env(type="motion", motion=True, confidence="low"), m.MotionMessage),
    ("hestia/sensor/vs-01/state", env(type="door", open=True), m.DoorMessage),
    ("hestia/sensor/vs-01/state", env(type="power", watt=1180), m.PowerMessage),
    ("hestia/sensor/vs-01/state", env(type="bed", occupied=True), m.BedMessage),
    ("hestia/sensor/vs-01/state", env(type="light", illuminance_lux=12), m.LightMessage),
    ("hestia/sensor/vs-01/state", env(type="climate", temperature_c=24.3, humidity_pct=48), m.ClimateMessage),
]


def _dev(device_type: str, **extra) -> dict:
    return {"version": 1, "sent_ts": 1790296798, "src_id": "vd-01", "seq": 1,
            "device_type": device_type, "source": "mock", **extra}


MINIMAL += [
    ("hestia/device/vd-01/state", _dev("smart_tv", power="ON"), m.SmartTvState),
    ("hestia/device/vd-01/state", _dev("smart_light", power="ON"), m.SmartLightState),
    ("hestia/device/vd-01/state", _dev("air_conditioner", power="ON"), m.AirConditionerState),
    ("hestia/device/vd-01/state", _dev("air_purifier", power="ON"), m.AirPurifierState),
    ("hestia/device/vd-01/state", _dev("smart_fridge", door="CLOSED"), m.SmartFridgeState),
    ("hestia/device/vd-01/state", _dev("water_purifier", power="ON"), m.WaterPurifierState),
    ("hestia/device/vd-01/state", _dev("washer", power="ON"), m.WasherState),
    ("hestia/device/vd-01/state", _dev("robot_cleaner", status="DOCKED"), m.RobotCleanerState),
    ("hestia/device/vd-01/state", _dev("doorbell", power="ON"), m.DoorbellState),
    ("hestia/device/vd-01/event", _dev("smart_tv", event_type="remote_input", button="VOL_UP"), m.RemoteInputEvent),
    ("hestia/device/vd-01/event", _dev("smart_fridge", event_type="door_opened"), m.DoorOpenedEvent),
    ("hestia/device/vd-01/event", _dev("smart_fridge", event_type="door_closed"), m.DoorClosedEvent),
    ("hestia/device/vd-01/event", _dev("water_purifier", event_type="dispensed"), m.DispensedEvent),
    ("hestia/device/vd-01/event", _dev("washer", event_type="cycle_completed"), m.CycleCompletedEvent),
    ("hestia/device/vd-01/event", _dev("robot_cleaner", event_type="cleaning_started"), m.CleaningStartedEvent),
    ("hestia/device/vd-01/event", _dev("robot_cleaner", event_type="cleaning_finished"), m.CleaningFinishedEvent),
    ("hestia/device/vd-01/event", _dev("doorbell", event_type="ring"), m.RingEvent),
]


@pytest.mark.parametrize("topic,payload,expected", MINIMAL)
def test_every_class_constructs(topic, payload, expected):
    """slots=True + dataclass 상속 조합은 실제 생성해봐야 확실하다.

    필드 순서나 기본값 문제는 생성 시점에 터진다.
    """
    msg = go(topic, payload)
    assert isinstance(msg, expected), f"{topic} {payload.get('type') or payload.get('device_type')}"