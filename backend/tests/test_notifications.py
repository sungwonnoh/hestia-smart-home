"""hestia/notify/push · ack → Backend Notification DTO 변환 (명세 17-A.8)."""

from datetime import datetime

import pytest

from app.services.notification_service import channel_room, scenario_type
from conftest import envelope

SENT_TS = 1755500000

REGISTRY = envelope("rpi5", devices=[
    {"virtual_id": "vd-01", "device_type": "smart_tv", "area": "living"},
    {"virtual_id": "vd-05", "device_type": "smart_fridge", "area": "kitchen"},
])


def push(**fields):
    base = dict(
        notify_id="n-20260818-03",
        scenario="WAKE_ROUTINE",
        priority="normal",
        channels=["vd-05"],
        requires_ack=True,
        ack_deadline=SENT_TS + 600,
        escalation_level=1,
        payload={"title": "수분 섭취", "text": "물 한 잔 드세요"},
    )
    msg = envelope("rpi5", **{**base, **fields})
    msg["sent_ts"] = SENT_TS
    return msg


def receive_one(hestia, **fields):
    assert hestia.receive("hestia/notify/push", push(**fields))
    return hestia.get("/notifications").json()[0]


# ------------------------------------------------------------ scenario → type


@pytest.mark.parametrize("scenario, expected", [
    ("MEDICATION_PROMPT", "REMINDER"),
    ("WAKE_ROUTINE", "REMINDER"),
    ("SLEEP_ROUTINE", "INFO"),
    ("SAFETY", "SAFETY"),
    ("SENSOR_FAULT", "WARNING"),
])
def test_scenario_maps_to_type_and_scenario_is_kept(hestia, scenario, expected):
    n = receive_one(hestia, scenario=scenario)
    assert n["type"] == expected
    assert n["scenario"] == scenario  # 원본 보존


def test_unknown_scenario_falls_back_safely(hestia):
    n = receive_one(hestia, scenario="LAUNDRY_DONE")
    assert n["scenario"] == "LAUNDRY_DONE"
    assert n["type"] == "INFO"
    # 모르는 scenario 라도 priority 가 safety 면 SAFETY 로 올린다 (기존 정책)
    assert scenario_type("SOMETHING_NEW", "safety") == "SAFETY"
    assert scenario_type("", "normal") == "INFO"


def test_mqtt_type_field_is_not_used(hestia):
    """type 은 MQTT 필드가 아니다. 들어와도 scenario 로만 정한다."""
    n = receive_one(hestia, scenario="SLEEP_ROUTINE", type="SAFETY")
    assert n["type"] == "INFO"


# ------------------------------------------------------------ 필드 변환


def test_push_fields_map_to_dto(hestia):
    hestia.receive("hestia/registry/devices", REGISTRY)
    n = receive_one(hestia)
    assert n["id"] == "n-20260818-03"
    assert n["priority"] == "normal"
    assert n["title"] == "수분 섭취"
    assert n["message"] == "물 한 잔 드세요"  # payload.text → message
    assert n["delivered"] is False and n["seen"] is False


def test_title_falls_back_to_text_when_missing(hestia):
    n = receive_one(hestia, payload={"text": "물 한 잔 드세요"})
    assert n["title"] == "물 한 잔 드세요"
    assert n["message"] == "물 한 잔 드세요"


def test_sent_ts_becomes_iso_created_at(hestia):
    n = receive_one(hestia)
    created = datetime.fromisoformat(n["createdAt"])
    assert created.tzinfo is not None  # offset 포함 ISO 8601
    assert created.timestamp() == SENT_TS


# ------------------------------------------------------------ channels → roomId


def test_channel_virtual_id_becomes_registry_area(hestia):
    hestia.receive("hestia/registry/devices", REGISTRY)
    n = receive_one(hestia, channels=["vd-05"])
    assert n["roomId"] == "kitchen"  # vd-05 를 그대로 복사하지 않는다


def test_voice_is_skipped_when_finding_room(hestia):
    hestia.receive("hestia/registry/devices", REGISTRY)
    n = receive_one(hestia, channels=["voice", "vd-05"])
    assert n["roomId"] == "kitchen"


def test_voice_only_channel_has_no_room(hestia):
    hestia.receive("hestia/registry/devices", REGISTRY)
    n = receive_one(hestia, channels=["voice"])
    assert n["roomId"] is None


def test_unregistered_channel_has_no_room_and_does_not_fail(hestia):
    n = receive_one(hestia, channels=["vd-99"])  # registry 수신 전 / 미등록
    assert n["roomId"] is None
    assert receive_one(hestia, notify_id="n-2", channels=[])["roomId"] is None


def test_channel_room_takes_first_resolvable_channel():
    areas = {"vd-01": "living", "vd-05": "kitchen", "vd-09": None}
    assert channel_room(["vd-09", "vd-05", "vd-01"], areas.get) == "kitchen"
    assert channel_room(["voice"], areas.get) is None


# ------------------------------------------------------------ hestia/notify/ack


def ack(notify_id, ack_type, src_id="vd-05"):
    return envelope(src_id, notify_id=notify_id, ack_type=ack_type)


def test_delivered_ack_marks_delivered_only(hestia):
    receive_one(hestia)
    assert hestia.receive("hestia/notify/ack", ack("n-20260818-03", "DELIVERED"))
    n = hestia.get("/notifications").json()[0]
    assert n["delivered"] is True
    assert n["seen"] is False


def test_seen_ack_marks_seen_and_delivered(hestia):
    receive_one(hestia)
    assert hestia.receive("hestia/notify/ack", ack("n-20260818-03", "SEEN"))
    n = hestia.get("/notifications").json()[0]
    assert n["seen"] is True
    assert n["delivered"] is True


def test_mqtt_ack_is_not_published_again(hestia):
    receive_one(hestia)
    hestia.receive("hestia/notify/ack", ack("n-20260818-03", "SEEN"))
    assert hestia.mqtt.published == []


def test_ack_for_unknown_notification_is_ignored(hestia):
    assert hestia.receive("hestia/notify/ack", ack("n-nope", "SEEN"))
    assert hestia.get("/notifications").json() == []


def test_invalid_ack_type_is_dropped(hestia):
    receive_one(hestia)
    assert not hestia.receive("hestia/notify/ack", ack("n-20260818-03", "READ"))
    assert hestia.get("/notifications").json()[0]["seen"] is False
    assert hestia.container.ingest.stats["dropped_schema"] == 1
