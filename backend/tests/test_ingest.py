"""Mock MQTT 메시지 → cache/DB 반영."""

from conftest import SETUP, envelope


def test_context_message_updates_current_context(hestia):
    assert hestia.receive("hestia/context/activity", envelope(
        "rpi5", state="EATING", since=1755499500, confidence=0.87,
        factors={"area": "kitchen", "presence": True}))
    assert hestia.receive("hestia/context/away", envelope("rpi5", state="HOME"))

    body = hestia.get("/context/current").json()
    assert list(body) == ["activity", "away"]
    assert body["activity"]["state"] == "EATING"
    assert body["activity"]["factors"]["area"] == "kitchen"
    assert "src_id" not in body["activity"]


def test_invalid_messages_are_dropped_without_crashing(hestia):
    assert not hestia.receive("hestia/context/activity", "not json")
    assert not hestia.receive("hestia/context/activity", envelope("rpi5"))  # state 없음
    assert not hestia.receive("hestia/context/unknown", envelope("rpi5", state="X"))
    assert not hestia.receive("hestia/device/vd-01/state",
                              envelope("vd-99", device_type="smart_tv", power="ON"))
    stats = hestia.container.ingest.stats
    assert stats["dropped_decode"] == 1
    assert stats["dropped_schema"] == 1
    assert stats["unknown_topic"] == 1
    assert stats["dropped_src_mismatch"] == 1
    assert hestia.get("/health").status_code == 200


def test_context_history_opens_new_row_only_when_state_changes(hestia):
    history = hestia.container.history
    hestia.receive("hestia/context/activity", envelope("rpi5", state="MEAL_PREP", confidence=0.6))
    first = history.open_context_id("activity")
    hestia.receive("hestia/context/activity", envelope("rpi5", state="MEAL_PREP", confidence=0.7))
    assert history.open_context_id("activity") == first
    assert history.get_context(first).confidence == 0.7

    hestia.receive("hestia/context/activity", envelope("rpi5", state="EATING", confidence=0.8))
    assert history.open_context_id("activity") != first
    assert history.get_context(first).ended_at is not None


def test_device_state_binds_to_registered_device_of_same_type(hestia):
    hestia.put("/setup", json=SETUP)
    hestia.receive("hestia/device/vd-01/state",
                   envelope("vd-01", device_type="smart_tv", source="mock", power="ON", volume=10))

    tv = next(d for d in hestia.get("/devices").json() if d["id"] == "tv-01")
    assert tv["virtualId"] == "vd-01"
    assert tv["online"] is True
    assert tv["status"] == {"power": "ON", "volume": 10, "state": "ON"}


def test_registry_binding_prefers_same_area(hestia):
    hestia.put("/setup", json=SETUP)  # light-01 은 kitchen
    hestia.receive("hestia/registry/devices", envelope("rpi4", devices=[
        {"virtual_id": "vd-02", "device_type": "smart_light", "area": "living"},
        {"virtual_id": "vd-07", "device_type": "smart_light", "area": "kitchen"},
    ]))
    light = next(d for d in hestia.get("/devices").json() if d["id"] == "light-01")
    assert light["virtualId"] == "vd-07"
    assert light["online"] is False  # 아직 상태 보고 없음
    assert light["status"] == {"state": "UNKNOWN"}


def test_notify_push_is_stored_once_and_cancel_hides_it(hestia):
    push = envelope("rpi5", notify_id="n-001", scenario="MEDICATION", priority="normal",
                    title="복약 시간입니다", message="식사 후 복약 시간을 확인해주세요.",
                    area="living")
    assert hestia.receive("hestia/notify/push", push)
    assert hestia.receive("hestia/notify/push", push)  # 재전송
    items = hestia.get("/notifications").json()
    assert [n["id"] for n in items] == ["n-001"]
    assert items[0]["delivered"] is False and items[0]["seen"] is False
    assert items[0]["roomId"] == "living"

    hestia.receive("hestia/notify/cancel", envelope("rpi5", notify_id="n-001"))
    assert hestia.get("/notifications").json() == []


def test_safety_priority_becomes_safety_type(hestia):
    hestia.receive("hestia/notify/push",
                   envelope("rpi5", notify_id="n-s", priority="safety", text="인덕션 확인"))
    n = hestia.get("/notifications").json()[0]
    assert n["type"] == "SAFETY"
    assert n["title"] == "인덕션 확인"


def test_intervention_outcome_is_recorded(hestia):
    assert hestia.receive("hestia/intervention/outcome", envelope(
        "rpi5", notify_id="n-001", scenario="MEDICATION", outcome="COMPLIED", delay_sec=120))
    assert hestia.container.history.count_outcomes() == 1


def test_model_and_profile_are_cached(hestia):
    hestia.receive("hestia/model/kde", envelope("rpi4", trained_at=1755400000, sample_days=30))
    hestia.receive("hestia/system/profile", envelope("rpi5", profile="DEMO"))
    snapshot = hestia.container.cache.snapshot()
    assert snapshot["model"]["kde"]["trained_at"] == 1755400000
    assert hestia.get("/health").json()["profile"] == "DEMO"
