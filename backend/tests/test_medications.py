"""복약 일정 저장 API."""

from datetime import date, timedelta

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from conftest import FakeMqtt, Hestia

BP = {
    "name": "혈압약",
    "slots": ["DINNER", "BREAKFAST"],
    "mealTiming": "AFTER_MEAL_30MIN",
    "days": 30,
    "startDate": "2026-10-09",
    "refillRequired": True,
}


def test_empty_until_added(hestia):
    assert hestia.get("/medications").json() == []


def test_create_and_list(hestia):
    res = hestia.post("/medications", json=BP)
    assert res.status_code == 201
    med = res.json()
    assert med["id"].startswith("med-")
    assert med["name"] == "혈압약"
    assert med["slots"] == ["BREAKFAST", "DINNER"]  # 하루 순서로 정렬
    assert med["mealTiming"] == "AFTER_MEAL_30MIN"
    assert med["startDate"] == "2026-10-09"
    assert med["endDate"] == "2026-11-07"  # 30일분의 마지막 날
    assert med["refillRequired"] is True
    assert hestia.get("/medications").json() == [med]


def test_start_date_defaults_to_today(hestia):
    med = hestia.post("/medications", json={**BP, "startDate": None}).json()
    assert med["startDate"] == date.today().isoformat()
    assert med["endDate"] == (date.today() + timedelta(days=29)).isoformat()


def test_bedtime_only_has_no_meal_timing(hestia):
    med = hestia.post("/medications", json={
        "name": "수면제", "slots": ["BEDTIME"], "mealTiming": "BEFORE_MEAL", "days": 7,
    }).json()
    assert med["slots"] == ["BEDTIME"]
    assert med["mealTiming"] is None
    assert med["refillRequired"] is False


def test_meal_slot_requires_meal_timing(hestia):
    res = hestia.post("/medications", json={**BP, "mealTiming": None})
    assert res.status_code == 422


def test_invalid_values_are_rejected(hestia):
    for bad in (
        {**BP, "name": "   "},
        {**BP, "slots": []},
        {**BP, "slots": ["NOON"]},
        {**BP, "mealTiming": "WITH_MEAL"},
        {**BP, "days": 0},
        {**BP, "days": 366},
    ):
        assert hestia.post("/medications", json=bad).status_code == 422, bad
    assert hestia.get("/medications").json() == []


def test_update_keeps_start_date_when_omitted(hestia):
    med = hestia.post("/medications", json=BP).json()
    res = hestia.put(f"/medications/{med['id']}", json={
        "name": "혈압약", "slots": ["BREAKFAST"], "mealTiming": "BEFORE_MEAL", "days": 14,
    })
    assert res.status_code == 200
    updated = res.json()
    assert updated["id"] == med["id"]
    assert updated["slots"] == ["BREAKFAST"]
    assert updated["mealTiming"] == "BEFORE_MEAL"
    assert updated["startDate"] == "2026-10-09"
    assert updated["endDate"] == "2026-10-22"
    assert updated["refillRequired"] is False
    assert hestia.get("/medications").json() == [updated]


def test_update_and_delete_unknown_are_404(hestia):
    assert hestia.put("/medications/med-nope", json=BP).status_code == 404
    assert hestia.client.delete("/api/v1/medications/med-nope").status_code == 404


def test_delete(hestia):
    med = hestia.post("/medications", json=BP).json()
    assert hestia.client.delete(f"/api/v1/medications/{med['id']}").status_code == 204
    assert hestia.get("/medications").json() == []


def test_saved_across_restart(tmp_path):
    def start():
        mqtt = FakeMqtt()
        app = create_app(Settings(db_path=str(tmp_path / "hestia.db")), mqtt=mqtt)
        return TestClient(app), mqtt

    client, mqtt = start()
    with client:
        Hestia(client, mqtt).post("/medications", json=BP)
    client, mqtt = start()
    with client:
        meds = Hestia(client, mqtt).get("/medications").json()
    assert [m["name"] for m in meds] == ["혈압약"]


# ------------------------------------------------------------ hestia/registry/medications


def schedule_messages(hestia):
    return [(p, retain) for topic, p, _qos, retain in hestia.mqtt.published
            if topic == "hestia/registry/medications"]


def test_changes_publish_full_schedule_retained(hestia):
    med = hestia.post("/medications", json=BP).json()
    payload, retain = schedule_messages(hestia)[-1]
    assert retain is True
    assert payload["version"] == 1
    assert payload["src_id"] == "rpi5-api"
    assert isinstance(payload["sent_ts"], int)
    # MQTT 는 snake_case, refillRequired → refill_notice
    assert payload["medications"] == [{
        "id": med["id"],
        "name": "혈압약",
        "slots": ["BREAKFAST", "DINNER"],
        "meal_timing": "AFTER_MEAL_30MIN",
        "days": 30,
        "start_date": "2026-10-09",
        "end_date": "2026-11-07",
        "refill_notice": True,
    }]

    other = hestia.post("/medications", json={
        "name": "수면제", "slots": ["BEDTIME"], "days": 7}).json()
    items = schedule_messages(hestia)[-1][0]["medications"]
    assert [m["id"] for m in items] == [med["id"], other["id"]]
    assert items[1]["meal_timing"] is None and items[1]["refill_notice"] is False

    hestia.put(f"/medications/{med['id']}", json={**BP, "days": 14})
    first = schedule_messages(hestia)[-1][0]["medications"][0]
    assert (first["days"], first["end_date"]) == (14, "2026-10-22")

    hestia.client.delete(f"/api/v1/medications/{med['id']}")
    hestia.client.delete(f"/api/v1/medications/{other['id']}")
    # 다 지우면 빈 목록을 남겨 엔진이 이전 일정을 지우게 한다
    assert schedule_messages(hestia)[-1][0]["medications"] == []
    assert len(schedule_messages(hestia)) == 5


def test_failed_requests_do_not_publish(hestia):
    hestia.post("/medications", json={**BP, "days": 0})
    hestia.put("/medications/med-nope", json=BP)
    hestia.client.delete("/api/v1/medications/med-nope")
    assert schedule_messages(hestia) == []


def test_saved_while_broker_down_then_republished_on_connect(hestia):
    hestia.mqtt.connected = False
    assert hestia.post("/medications", json=BP).status_code == 201  # 저장은 된다
    assert schedule_messages(hestia) == []

    hestia.mqtt.reconnect()
    payload, retain = schedule_messages(hestia)[-1]
    assert retain is True
    assert [m["name"] for m in payload["medications"]] == ["혈압약"]
