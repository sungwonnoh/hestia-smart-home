"""REST API — Flutter Repository 계약."""

from conftest import SETUP, envelope, make_hestia, Hestia


def test_health(hestia):
    body = hestia.get("/health").json()
    assert body["status"] == "ok"
    assert body["mqtt"] == {"enabled": True, "connected": True}


def test_setup_is_404_until_saved_then_round_trips(hestia):
    assert hestia.get("/setup").status_code == 404

    saved = hestia.put("/setup", json=SETUP)
    assert saved.status_code == 200
    body = hestia.get("/setup").json()
    assert [r["id"] for r in body["rooms"]] == ["living", "kitchen"]
    assert body["rooms"][1]["roles"] == ["MEAL"]
    assert {d["id"] for d in body["devices"]} == {"tv-01", "light-01", "air-conditioner-01"}
    assert body["preferences"]["quietHours"]["start"] == "23:00"
    assert body["preferences"]["safety"]["emergencyContact"] == "010-0000-0000"


def test_setup_keeps_device_binding_and_state_on_resave(hestia):
    hestia.put("/setup", json=SETUP)
    hestia.receive("hestia/device/vd-03/state",
                   envelope("vd-03", device_type="air_conditioner", power="ON", temp_set=24))
    hestia.put("/setup", json=SETUP)
    ac = next(d for d in hestia.get("/devices").json() if d["id"] == "air-conditioner-01")
    assert ac["virtualId"] == "vd-03"
    assert ac["status"]["targetTemp"] == 24


def test_rooms_create_and_conflict(hestia):
    assert hestia.post("/rooms", json={"id": "bedroom", "name": "침실", "roles": ["SLEEP"]}).status_code == 201
    assert hestia.post("/rooms", json={"id": "bedroom", "name": "침실"}).status_code == 409
    assert hestia.get("/rooms").json() == [{"id": "bedroom", "name": "침실", "roles": ["SLEEP"]}]


def test_update_device(hestia):
    hestia.put("/setup", json=SETUP)
    res = hestia.put("/devices/tv-01", json={"roomId": "kitchen", "virtualId": "vd-01"})
    assert res.status_code == 200
    assert res.json()["roomId"] == "kitchen"
    assert res.json()["virtualId"] == "vd-01"

    assert hestia.put("/devices/nope", json={"name": "x"}).status_code == 404
    assert hestia.put("/devices/light-01", json={"virtualId": "vd-01"}).status_code == 409


def test_device_goes_offline_when_reports_are_stale():
    client, mqtt = make_hestia(device_stale_sec=-1)
    with client:
        h = Hestia(client, mqtt)
        h.put("/setup", json=SETUP)
        h.receive("hestia/device/vd-01/state", envelope("vd-01", device_type="smart_tv", power="ON"))
        tv = next(d for d in h.get("/devices").json() if d["id"] == "tv-01")
        assert tv["online"] is False
        assert tv["status"]["state"] == "ON"  # 마지막 상태는 그대로 보여준다


def test_preferences_round_trip_and_validation(hestia):
    assert hestia.get("/preferences").json()["sensitivity"] == "normal"
    prefs = SETUP["preferences"]
    assert hestia.put("/preferences", json=prefs).status_code == 200
    assert hestia.get("/preferences").json() == prefs

    bad = {**prefs, "quietHours": {"enabled": True, "start": "25:00", "end": "07:00"}}
    assert hestia.put("/preferences", json=bad).status_code == 422


def test_ack_seen_records_and_publishes_mqtt_ack(hestia):
    hestia.receive("hestia/notify/push", envelope("rpi5", notify_id="n-001", payload={"title": "복약"}))

    res = hestia.post("/notifications/n-001/ack", json={"ackType": "SEEN"})
    assert res.json() == {"success": True, "forwarded": True}

    n = hestia.get("/notifications").json()[0]
    assert n["seen"] is True and n["delivered"] is True

    topic, payload, qos, retain = hestia.mqtt.published[-1]
    assert topic == "hestia/notify/ack"
    assert retain is False
    assert payload["notify_id"] == "n-001"
    assert payload["ack_type"] == "SEEN"
    assert payload["src_id"] == "rpi5-api"
    assert {"version", "sent_ts", "src_id"} <= payload.keys()


def test_listing_notifications_does_not_mark_seen(hestia):
    hestia.receive("hestia/notify/push", envelope("rpi5", notify_id="n-001", payload={"title": "복약"}))
    hestia.get("/notifications")
    hestia.get("/notifications")
    assert hestia.get("/notifications").json()[0]["seen"] is False
    assert hestia.mqtt.published == []


def test_ack_delivered_only_sets_delivered(hestia):
    hestia.receive("hestia/notify/push", envelope("rpi5", notify_id="n-001", payload={"title": "복약"}))
    hestia.post("/notifications/n-001/ack", json={"ackType": "DELIVERED"})
    n = hestia.get("/notifications").json()[0]
    assert n["delivered"] is True and n["seen"] is False


def test_ack_errors(hestia):
    assert hestia.post("/notifications/none/ack", json={"ackType": "SEEN"}).status_code == 404
    hestia.receive("hestia/notify/push", envelope("rpi5", notify_id="n-001", payload={"title": "복약"}))
    assert hestia.post("/notifications/n-001/ack", json={"ackType": "READ"}).status_code == 422


def test_ack_is_recorded_even_when_broker_is_down(hestia):
    hestia.receive("hestia/notify/push", envelope("rpi5", notify_id="n-001", payload={"title": "복약"}))
    hestia.mqtt.connected = False
    res = hestia.post("/notifications/n-001/ack", json={"ackType": "SEEN"})
    assert res.json() == {"success": True, "forwarded": False}
    assert hestia.get("/notifications").json()[0]["seen"] is True


def test_explanations(hestia):
    assert hestia.get("/explanations/latest").status_code == 404

    hestia.receive("hestia/context/activity", envelope(
        "rpi5", state="MEAL_DONE", confidence=0.87,
        factors={"area": "kitchen", "presence": True}))
    hestia.receive("hestia/notify/push", envelope(
        "rpi5", notify_id="n-001", scenario="MEDICATION_PROMPT",
        payload={"title": "복약 시간입니다"}, context="activity"))

    latest = hestia.get("/explanations/latest").json()
    assert latest["contextName"] == "activity"
    assert latest["state"] == "MEAL_DONE"
    assert latest["confidence"] == 0.87
    assert latest["factors"][0] == {"key": "area", "value": "kitchen",
                                    "label": "주방 재실 감지", "satisfied": True}
    assert latest["action"] == "'복약 시간입니다' 알림을 보냈습니다."

    # 알림에서 판단으로 이동
    n = hestia.get("/notifications").json()[0]
    assert n["explanationId"] == latest["id"]
    assert hestia.get(f"/explanations/{n['explanationId']}").json()["id"] == latest["id"]
    assert hestia.get("/explanations/999").status_code == 404
    assert len(hestia.get("/explanations").json()) == 1


def test_suppression_and_plain_states_are_not_explanations(hestia):
    hestia.receive("hestia/context/suppression", envelope("rpi5", focus=True))
    hestia.receive("hestia/context/away", envelope("rpi5", state="HOME"))
    assert hestia.get("/explanations").json() == []
