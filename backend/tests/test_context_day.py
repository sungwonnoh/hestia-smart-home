"""hestia/context/day — Context Engine 이 wake 대신 보내는 오늘 하루 기록."""

from conftest import envelope

DAY = envelope("rpi5", date="2026-10-11", meals=[1791665992, 1791683992],
               hydrations=[1791680392], medications=[])


def test_day_is_served_without_state(hestia):
    assert hestia.receive("hestia/context/day", DAY)
    day = hestia.get("/context/current").json()["day"]
    assert day == {
        "date": "2026-10-11",
        "meals": [1791665992, 1791683992],
        "hydrations": [1791680392],
        "medications": [],
    }


def test_day_is_not_an_explanation(hestia):
    """기록이지 판단이 아니므로 판단 근거(설명)로 남기지 않는다."""
    hestia.receive("hestia/context/activity", envelope("rpi5", state="EATING", confidence=0.87))
    hestia.receive("hestia/context/day", DAY)
    latest = hestia.get("/explanations/latest").json()
    assert latest["contextName"] == "activity"
    assert all(e["contextName"] != "day" for e in hestia.get("/explanations").json())


def test_wake_is_no_longer_known(hestia):
    """Context Engine 은 #35 에서 wake 를 없앴다."""
    assert hestia.receive("hestia/context/wake", envelope("rpi5", state="AWAKE")) is False
    assert "wake" not in hestia.get("/context/current").json()


def test_day_requires_date(hestia):
    bad = envelope("rpi5", meals=[])
    assert hestia.receive("hestia/context/day", bad) is False
