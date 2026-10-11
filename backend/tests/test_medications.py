"""복약 일정 저장 API와 hestia/registry/medications (Context Engine #40 형식)."""

import sqlite3
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.schemas.medication import legacy_schedule
from conftest import FakeMqtt, Hestia

BP = {
    "name": "혈압약",
    "schedule": {"type": "FIXED", "times": ["20:00", "8:00"]},
    "days": 30,
    "startDate": "2026-10-09",
    "refillRequired": True,
}
COLD = {
    "name": "감기약",
    "schedule": {"type": "AFTER_MEAL", "delayMin": 30},
    "days": 5,
    "startDate": "2026-10-09",
}


def test_empty_until_added(hestia):
    assert hestia.get("/medications").json() == []


def test_create_and_list(hestia):
    res = hestia.post("/medications", json=BP)
    assert res.status_code == 201
    med = res.json()
    assert med["id"].startswith("med-")
    assert med["name"] == "혈압약"
    # 시각은 HH:MM 으로 맞추고 하루 순서로 정렬
    assert med["schedule"] == {"type": "FIXED", "delayMin": None, "times": ["08:00", "20:00"]}
    assert med["startDate"] == "2026-10-09"
    assert med["endDate"] == "2026-11-07"  # 30일분의 마지막 날
    assert med["refillRequired"] is True
    assert hestia.get("/medications").json() == [med]


def test_after_meal(hestia):
    med = hestia.post("/medications", json=COLD).json()
    assert med["schedule"] == {"type": "AFTER_MEAL", "delayMin": 30, "times": []}
    assert med["endDate"] == "2026-10-13"
    assert med["refillRequired"] is False


def test_after_meal_delay_defaults_to_30_and_drops_times(hestia):
    med = hestia.post("/medications", json={
        **COLD, "schedule": {"type": "AFTER_MEAL", "times": ["08:00"]}}).json()
    assert med["schedule"] == {"type": "AFTER_MEAL", "delayMin": 30, "times": []}


def test_right_after_meal_is_zero(hestia):
    med = hestia.post("/medications", json={
        **COLD, "schedule": {"type": "AFTER_MEAL", "delayMin": 0}}).json()
    assert med["schedule"]["delayMin"] == 0


def test_fixed_drops_delay_and_duplicates(hestia):
    med = hestia.post("/medications", json={
        **BP, "schedule": {"type": "FIXED", "delayMin": 30, "times": ["08:00", "8:00"]}}).json()
    assert med["schedule"] == {"type": "FIXED", "delayMin": None, "times": ["08:00"]}


def test_start_date_defaults_to_today(hestia):
    med = hestia.post("/medications", json={**BP, "startDate": None}).json()
    assert med["startDate"] == date.today().isoformat()
    assert med["endDate"] == (date.today() + timedelta(days=29)).isoformat()


@pytest.mark.parametrize("schedule", [
    None,
    {"type": "BEDTIME"},                                  # #40 은 after_meal / fixed 만
    {"type": "BEFORE_MEAL"},
    {"type": "FIXED", "times": []},
    {"type": "FIXED"},
    {"type": "FIXED", "times": ["24:00"]},
    {"type": "FIXED", "times": ["8시"]},
    {"type": "FIXED", "times": ["06:00", "08:00", "10:00", "12:00", "14:00", "16:00", "18:00"]},
    {"type": "AFTER_MEAL", "delayMin": -1},
    {"type": "AFTER_MEAL", "delayMin": 181},
])
def test_invalid_schedule_is_rejected(hestia, schedule):
    assert hestia.post("/medications", json={**BP, "schedule": schedule}).status_code == 422
    assert hestia.get("/medications").json() == []


def test_invalid_values_are_rejected(hestia):
    for bad in (
        {**BP, "name": "   "},
        {**BP, "days": 0},
        {**BP, "days": 366},
    ):
        assert hestia.post("/medications", json=bad).status_code == 422, bad
    assert hestia.get("/medications").json() == []


def test_update_keeps_start_date_when_omitted(hestia):
    med = hestia.post("/medications", json=BP).json()
    res = hestia.put(f"/medications/{med['id']}", json={
        "name": "혈압약", "schedule": {"type": "AFTER_MEAL", "delayMin": 0}, "days": 14,
    })
    assert res.status_code == 200
    updated = res.json()
    assert updated["id"] == med["id"]
    assert updated["schedule"] == {"type": "AFTER_MEAL", "delayMin": 0, "times": []}
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


def start(path):
    mqtt = FakeMqtt()
    app = create_app(Settings(db_path=str(path)), mqtt=mqtt)
    return TestClient(app), mqtt


def test_saved_across_restart(tmp_path):
    client, mqtt = start(tmp_path / "hestia.db")
    with client:
        Hestia(client, mqtt).post("/medications", json=BP)
    client, mqtt = start(tmp_path / "hestia.db")
    with client:
        meds = Hestia(client, mqtt).get("/medications").json()
    assert [(m["name"], m["schedule"]["times"]) for m in meds] == [("혈압약", ["08:00", "20:00"])]


# ------------------------------------------------------------ 이전 형식 변환


@pytest.mark.parametrize("slots, timing, expected", [
    (["BREAKFAST", "LUNCH", "DINNER"], "AFTER_MEAL_30MIN", ("AFTER_MEAL", 30, [])),
    (["BREAKFAST", "LUNCH", "DINNER"], "RIGHT_AFTER_MEAL", ("AFTER_MEAL", 0, [])),
    # 세 끼가 아니거나, 식전이거나, 자기 전이 섞이면 기본 시각의 FIXED
    (["BREAKFAST", "DINNER"], "AFTER_MEAL_30MIN", ("FIXED", None, ["08:00", "18:00"])),
    (["BREAKFAST", "LUNCH", "DINNER"], "BEFORE_MEAL", ("FIXED", None, ["08:00", "12:00", "18:00"])),
    (["BEDTIME"], None, ("FIXED", None, ["22:00"])),
    (["DINNER", "BREAKFAST", "LUNCH", "BEDTIME"], "AFTER_MEAL_30MIN",
     ("FIXED", None, ["08:00", "12:00", "18:00", "22:00"])),
])
def test_legacy_schedule(slots, timing, expected):
    assert legacy_schedule(slots, timing) == expected


def test_legacy_db_is_migrated(tmp_path):
    path = tmp_path / "hestia.db"
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE medications (id TEXT PRIMARY KEY, name TEXT NOT NULL, slots TEXT NOT NULL, "
        "meal_timing TEXT, days INTEGER NOT NULL, start_date TEXT NOT NULL, "
        "refill_required INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL, "
        "updated_at REAL NOT NULL)"
    )
    conn.executemany(
        "INSERT INTO medications VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("med-cold", "감기약", "BREAKFAST,LUNCH,DINNER", "AFTER_MEAL_30MIN", 5,
             "2026-10-09", 0, 1.0, 1.0),
            ("med-bp", "혈압약", "BREAKFAST,DINNER", "AFTER_MEAL_30MIN", 30,
             "2026-10-09", 1, 2.0, 2.0),
        ],
    )
    conn.commit()
    conn.close()

    client, mqtt = start(path)
    with client:
        h = Hestia(client, mqtt)
        meds = h.get("/medications").json()
        assert [(m["id"], m["schedule"]) for m in meds] == [
            ("med-cold", {"type": "AFTER_MEAL", "delayMin": 30, "times": []}),
            ("med-bp", {"type": "FIXED", "delayMin": None, "times": ["08:00", "18:00"]}),
        ]
        assert meds[1]["refillRequired"] is True
        # 변환한 뒤에도 그대로 고치고 저장할 수 있다
        assert h.put("/medications/med-bp", json=BP).status_code == 200

    # 다시 켜도 변환을 반복하지 않는다
    client, mqtt = start(path)
    with client:
        assert len(Hestia(client, mqtt).get("/medications").json()) == 2


# ------------------------------------------------------------ hestia/registry/medications


def schedule_messages(hestia):
    return [(p, retain) for topic, p, _qos, retain in hestia.mqtt.published
            if topic == "hestia/registry/medications"]


def test_changes_publish_full_schedule_retained(hestia):
    cold = hestia.post("/medications", json=COLD).json()
    bp = hestia.post("/medications", json=BP).json()
    payload, retain = schedule_messages(hestia)[-1]
    assert retain is True
    assert payload["version"] == 1
    assert payload["src_id"] == "rpi5-api"
    assert isinstance(payload["sent_ts"], int)
    # Context Engine #40 parse_medications 형식: snake_case, schedule type 은 소문자
    assert payload["medications"] == [
        {
            "id": cold["id"],
            "name": "감기약",
            "schedule": {"type": "after_meal", "delay_min": 30},
            "start_date": "2026-10-09",
            "days": 5,
            "end_date": "2026-10-13",
            "refill_notice": False,
        },
        {
            "id": bp["id"],
            "name": "혈압약",
            "schedule": {"type": "fixed", "times": ["08:00", "20:00"]},
            "start_date": "2026-10-09",
            "days": 30,
            "end_date": "2026-11-07",
            "refill_notice": True,
        },
    ]

    hestia.put(f"/medications/{bp['id']}", json={**BP, "days": 14})
    second = schedule_messages(hestia)[-1][0]["medications"][1]
    assert (second["days"], second["end_date"]) == (14, "2026-10-22")

    hestia.client.delete(f"/api/v1/medications/{cold['id']}")
    hestia.client.delete(f"/api/v1/medications/{bp['id']}")
    # 다 지우면 빈 목록을 남겨 엔진이 이전 일정을 지우게 한다
    assert schedule_messages(hestia)[-1][0]["medications"] == []
    assert len(schedule_messages(hestia)) == 5


def test_published_schedule_only_has_engine_types(hestia):
    """엔진은 모르는 type 이 하나라도 있으면 목록 전체를 버린다."""
    hestia.post("/medications", json=COLD)
    hestia.post("/medications", json=BP)
    items = schedule_messages(hestia)[-1][0]["medications"]
    for item in items:
        s = item["schedule"]
        assert s["type"] in {"after_meal", "fixed"}
        if s["type"] == "after_meal":
            assert set(s) == {"type", "delay_min"} and isinstance(s["delay_min"], int)
        else:
            assert set(s) == {"type", "times"} and s["times"]


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
