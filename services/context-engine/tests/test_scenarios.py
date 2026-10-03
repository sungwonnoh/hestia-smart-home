"""시나리오 JSONL 통합 테스트.

단위 테스트가 손으로 상황을 만드는 것과 달리, 실제 메시지 흐름에서
각 판정 경로가 도는지 확인한다. 부스 시연에서 재생할 파일이기도 하다.
"""

from pathlib import Path

import pytest

from hestia_engine.fsm import WakeState
from hestia_engine.replay import replay_file

DATA = Path(__file__).parent / "data"


def play(name: str):
    return replay_file(DATA / name, echo=False)


def published(pub, topic: str) -> tuple[dict, ...]:
    return pub.of_topic(f"hestia/context/{topic}")


def states(pub, topic: str, key: str = "state") -> list:
    """그 context 의 상태 변천사. 연속 중복은 제거한다."""
    out = []
    for p in published(pub, topic):
        v = p.get(key)
        if not out or out[-1] != v:
            out.append(v)
    return out


# ============================================================ 공통


@pytest.mark.parametrize(
    "name", ["morning.jsonl", "fall.jsonl", "away.jsonl",
             "night.jsonl", "multi.jsonl", "fault.jsonl"]
)
def test_every_scenario_replays_cleanly(name):
    """설정에 없는 센서나 깨진 페이로드가 없어야 한다."""
    result, engine, pub, _ = play(name)
    assert result.lines > 0
    assert engine.dropped == 0
    assert engine.received == result.lines


@pytest.mark.parametrize(
    "name", ["morning.jsonl", "fall.jsonl", "away.jsonl",
             "night.jsonl", "multi.jsonl", "fault.jsonl"]
)
def test_every_scenario_is_deterministic(name):
    _, _, a, _ = play(name)
    _, _, b, _ = play(name)
    assert a.published == b.published


# ============================================================ 쓰러짐


def test_fall_accumulates_stillness():
    """present=true 인데 energy 가 계속 바닥.

    PIR 로는 못 잡는다 — 정지한 사람과 빈 방이 같은 신호다.
    판정 자체는 안전 시나리오가 생겨야 하고, 여기서는 근거가
    쌓이는 것까지 확인한다.
    """
    _, engine, _, _ = play("fall.jsonl")
    world = engine.context._presence_eval._world
    assert world.still_sec("bathroom") >= 600


def test_fall_stays_bathroom():
    """장시간 정지가 UNKNOWN 으로 빠지지 않는다."""
    _, engine, _, _ = play("fall.jsonl")
    assert engine.context.activity.state == "BATHROOM"
    assert engine.context.away.state == "HOME"


def test_fall_distance_drops():
    """자세가 낮아진 것. 지금은 쓰지 않지만 World State 가 들고 있다."""
    _, engine, _, _ = play("fall.jsonl")
    st = engine.context._presence_eval._world.sensor("vs-11")
    assert st.distance_cm < 70


# ============================================================ 외출


def test_away_detected():
    seq = states(play("away.jsonl")[2], "away")
    assert "AWAY" in seq


def test_away_evidence_is_door_exit():
    _, _, pub, _ = play("away.jsonl")
    away = [p for p in published(pub, "away") if p["state"] == "AWAY"]
    assert away[0]["evidence"] == "door_exit"


def test_away_since_is_door_close_time():
    """판단이 내려진 시각이 아니라 문이 닫힌 시각."""
    _, _, pub, _ = play("away.jsonl")
    away = [p for p in published(pub, "away") if p["state"] == "AWAY"]
    assert away[0]["since"] == 1790298330.0


def test_away_returns_home():
    seq = states(play("away.jsonl")[2], "away")
    assert seq[-1] == "HOME"


def test_away_suppresses_then_releases():
    _, engine, pub, _ = play("away.jsonl")
    reasons = states(pub, "suppression", key="reason")
    assert "AWAY" in reasons
    assert engine.context.suppression.active is False       # 귀가 후 해제


def test_away_occupancy_unknown():
    seq = states(play("away.jsonl")[2], "occupancy")
    assert "UNKNOWN" in seq


# ============================================================ 야간


def test_night_reaches_sleeping():
    seq = states(play("night.jsonl")[2], "activity")
    assert "SLEEPING" in seq


def test_night_bathroom_is_not_waking():
    """수면 중 화장실은 기상이 아니다.

    여기서 AWAKE 로 넘어가면 wake_t0 가 새벽으로 찍히고
    KDE 기상 분포가 통째로 오염된다.
    """
    _, engine, pub, _ = play("night.jsonl")
    wake = [p for p in pub.of_topic("hestia/context/wake")]
    assert all(p["state"] == "ASLEEP" for p in wake)
    assert engine.context.wake_fsm.state.wake_t0 is None


def test_night_returns_to_sleeping():
    _, engine, _, _ = play("night.jsonl")
    assert engine.context.activity.state in ("SLEEPING", "IN_BED_AWAKE")


def test_night_tv_off_before_bed():
    _, engine, _, _ = play("night.jsonl")
    assert engine.context._presence_eval._world.device("vd-01").get("power") == "OFF"


# ============================================================ 다인


def test_multi_detected():
    seq = states(play("multi.jsonl")[2], "occupancy")
    assert "MULTI" in seq


def test_multi_needs_two_strong_areas():
    _, _, pub, _ = play("multi.jsonl")
    multi = [p for p in published(pub, "occupancy") if p["state"] == "MULTI"]
    assert multi[0]["factors"]["strong_areas"] >= 2


def test_multi_since_is_concurrency_start():
    """늦게 켜진 쪽의 시각. 두 구역이 각자 changed_at 을 들고 있을 뿐이다."""
    _, _, pub, _ = play("multi.jsonl")
    multi = [p for p in published(pub, "occupancy") if p["state"] == "MULTI"]
    assert multi[0]["since"] == 1790330570.0        # 주방 mmWave 가 켜진 시각


def test_multi_suppresses_personal_notifications():
    """명세: MULTI 판정 시 개인화 알림 중단, 개인 Baseline 학습 중단."""
    _, _, pub, _ = play("multi.jsonl")
    reasons = states(pub, "suppression", key="reason")
    assert "MULTI" in reasons


def test_multi_clears():
    _, engine, _, _ = play("multi.jsonl")
    assert engine.context.occupancy.state == "SINGLE"


# ============================================================ 센서 고장


def test_fault_detected():
    seq = states(play("fault.jsonl")[2], "away")
    assert "SENSOR_FAULT" in seq


def test_fault_evidence_is_lwt():
    _, _, pub, _ = play("fault.jsonl")
    fault = [p for p in published(pub, "away") if p["state"] == "SENSOR_FAULT"]
    assert fault[0]["evidence"] == "lwt_offline"


def test_fault_is_not_away():
    """센서가 죽은 것을 부재로 오인하면 낙상·장기 무활동을 놓친다."""
    _, _, pub, _ = play("fault.jsonl")
    seq = states(pub, "away")
    assert "AWAY" not in seq


def test_fault_makes_activity_unknown():
    """재실 판단 자체를 믿을 수 없으니 활동도 모른다."""
    _, _, pub, _ = play("fault.jsonl")
    seq = states(pub, "activity")
    assert "UNKNOWN" in seq


def test_fault_occupancy_unknown():
    seq = states(play("fault.jsonl")[2], "occupancy")
    assert "UNKNOWN" in seq


def test_fault_recovers():
    _, engine, _, _ = play("fault.jsonl")
    assert engine.context.away.state == "HOME"
    assert engine.context.presence.user_area == "living"
    