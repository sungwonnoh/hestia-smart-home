import json

import pytest

from hestia_engine.clock import ReplayClock
from hestia_engine.config import load
from hestia_engine.engine import Engine, RecordingPublisher
from hestia_engine.timers import Scheduler
from hestia_engine.world import WorldState
from hestia_engine.notify import CLOSED_TIMEOUT, VOICE

from test_activity import HOME, POLICY, MORNING


class Ctx:
    def __init__(self, tmp_path, start: float = MORNING):
        h = tmp_path / "home.toml"
        p = tmp_path / "policy.toml"
        h.write_text(HOME, encoding="utf-8")
        p.write_text(POLICY, encoding="utf-8")
        self.config = load(h, p)
        self.clock = ReplayClock(start)
        self.sched = Scheduler(self.clock)
        self.world = WorldState(self.clock, self.config, self.sched)
        self.pub = RecordingPublisher()
        self.engine = Engine(
            self.clock, self.config, self.world, self.sched, self.pub
        )

    def at(self, ts: float):
        while (due := self.sched.next_due()) is not None and due <= ts:
            self.sched.run_due(until=due)
        self.clock.advance_to(ts)
        return self

    def send(self, topic: str, payload: dict):
        self.engine.ingest(topic, json.dumps(payload))
        return self

    def bathroom(self, present: bool, energy: int = 3, seq: int = 1):
        """화장실 재실. energy 3 은 정지 상태다 (still_max = 10)."""
        return self.send("hestia/sensor/vs-11/state", {
            "version": 1, "sent_ts": 0, "src_id": "vs-11", "seq": seq,
            "type": "presence", "present": present, "confidence": "high",
            "energy": energy, "distance_cm": 80,
        })

    def device(self, vid: str, device_type: str, power: str = "ON"):
        fields: dict = {
            "version": 1, "sent_ts": 0, "src_id": vid, "seq": 1,
            "device_type": device_type, "source": "mock",
        }
        if device_type == "smart_fridge":
            fields["door"] = "CLOSED"
        else:
            fields["power"] = power
        return self.send(f"hestia/device/{vid}/state", fields)

    def of(self, topic: str) -> tuple[dict, ...]:
        return self.pub.of_topic(f"hestia/{topic}")

    def decisions(self) -> tuple[dict, ...]:
        return tuple(
            p for p in self.of("intervention/decision")
            if p["kind"].startswith("safety")
        )

    def reasons(self) -> list[str]:
        return [p["reason"] for p in self.decisions()]

    def pushes(self) -> tuple[dict, ...]:
        return tuple(p for p in self.of("notify/push") if p["scenario"] == "SAFETY")


@pytest.fixture
def c(tmp_path):
    return Ctx(tmp_path)


STILL_LIMIT = 900        # policy.toml 의 thresholds.safety.still_sec


# ============================================================ 판정


def test_no_one_no_judgement(c):
    """아무도 없으면 판정 대상이 아니다.

    still_sec 이 0 인 것은 '움직이고 있다' 가 아니라 '아무도 없다'
    일 수 있다. 빈 화장실에 대고 MOVING 을 계속 발행하면 안 된다.
    """
    c.at(MORNING + 100)
    c.engine.scenarios.tick(c.engine.context)
    assert c.decisions() == ()


def test_moving_is_not_candidate(c):
    """사람이 있고 움직이면 정상이다."""
    c.bathroom(True, energy=50)
    assert c.reasons() == ["MOVING"]
    assert c.pushes() == ()


def test_still_under_limit(c):
    c.bathroom(True, energy=3)
    c.at(MORNING + 600).bathroom(True, energy=3, seq=2)

    assert c.reasons() == ["MOVING"]
    assert c.pushes() == ()


def test_still_over_limit_notifies(c):
    """사람이 있는데 오래 움직이지 않는다 — 낙상 의심."""
    c.device("vd-01", "smart_tv")
    c.bathroom(True, energy=3)
    c.at(MORNING + STILL_LIMIT + 10).bathroom(True, energy=3, seq=2)

    assert "STILL_TOO_LONG" in c.reasons()
    assert len(c.pushes()) == 1
    assert c.pushes()[0]["priority"] == "health"


def test_moving_again_resets(c):
    """움직이면 정지 시계가 처음부터 다시 간다."""
    c.bathroom(True, energy=3)
    c.at(MORNING + 600).bathroom(True, energy=50, seq=2)     # 움직임
    c.at(MORNING + 1200).bathroom(True, energy=3, seq=3)     # 다시 정지

    assert "STILL_TOO_LONG" not in c.reasons()
    assert c.pushes() == ()


def test_decision_transition_recorded(c):
    """MOVING → STILL_TOO_LONG 전이가 기록으로 남아야
    나중에 오탐률을 볼 수 있다."""
    c.device("vd-01", "smart_tv")
    c.bathroom(True, energy=50)
    c.at(MORNING + 100).bathroom(True, energy=3, seq=2)
    c.at(MORNING + STILL_LIMIT + 200).bathroom(True, energy=3, seq=3)

    assert c.reasons() == ["MOVING", "STILL_TOO_LONG"]


def test_decision_has_no_distribution_fields(c):
    """안전은 고정 임계다. 개인 분포를 쓰면 '평소 20분씩
    안 움직인다' 를 학습해 진짜 낙상을 놓친다."""
    c.bathroom(True, energy=3)

    d = c.decisions()[0]
    assert d["tail_probability"] is None
    assert d["predictability"] is None
    assert d["factors"]["area"] == "bathroom"
    assert d["factors"]["limit_sec"] == STILL_LIMIT


# ============================================================ 억제 관통


def test_pierces_cooldown(c):
    """SAFETY 는 억제를 뚫는다. 명세의 네 시나리오 중 유일하다."""
    from hestia_engine.context import SuppressionContext

    c.device("vd-01", "smart_tv")
    c.bathroom(True, energy=3)

    nid = c.engine.notifier.send(
        scenario="SAFETY", title="t", text="x", priority="health",
        suppression=SuppressionContext(
            name="suppression", since=0.0,
            active=True, reason="COOLDOWN", except_=("SAFETY",),
        ),
    )
    assert nid is not None


def test_pierces_daily_limit(c):
    """health 는 폐기하지 않는다 (명세)."""
    for _ in range(20):
        c.engine.notifier.limits.note_sent()

    ok, why = c.engine.notifier.limits.allows("health")
    assert ok is True
    assert why is None


def test_voice_pierces_quiet_hours(c, tmp_path):
    """정숙 시간대에도 음성을 쓴다."""
    from hestia_engine.notify import VOICE

    night = Ctx(tmp_path, start=MORNING + 50400)      # 23시경
    assert night.engine.notifier.channels.select("SAFETY", None, level=2) == (VOICE,)


# ============================================================ 타이머


def test_timer_scheduled_while_still(c):
    """쓰러져 있으면 센서가 조용해 재계산 계기가 없다.
    임계에 닿는 시각을 직접 예약해야 한다."""
    c.bathroom(True, energy=3)

    timers = c.engine.scenarios.tick(c.engine.context)
    keys = [k for k, _ in timers]
    assert "safety-bathroom" in keys


def test_timer_fires_and_notifies(c):
    """메시지가 안 와도 타이머가 알림을 띄운다."""
    c.device("vd-01", "smart_tv")
    c.bathroom(True, energy=3)

    c.at(MORNING + STILL_LIMIT + 60)
    c.sched.run_due()

    assert len(c.pushes()) == 1


def test_timer_cancelled_when_area_empty(c):
    """사람이 나가면 그 타이머는 의미가 없다."""
    c.bathroom(True, energy=3)
    assert "safety-bathroom" in c.engine._scenario_timers

    c.at(MORNING + 300).bathroom(False, energy=0, seq=2)
    assert "safety-bathroom" not in c.engine._scenario_timers


def test_no_timer_while_moving(c):
    """움직이고 있으면 예약하지 않는다 — 멈추는 순간
    센서가 메시지를 보내 다시 걸린다."""
    c.bathroom(True, energy=50)

    timers = c.engine.scenarios.tick(c.engine.context)
    assert [k for k, _ in timers if k.startswith("safety")] == []


# ============================================================ comply


def test_complied_when_moving_again(c):
    """'괜찮으신가요' 에 대한 응답은 움직임이다."""
    c.device("vd-01", "smart_tv")
    c.bathroom(True, energy=3)
    c.at(MORNING + STILL_LIMIT + 10).bathroom(True, energy=3, seq=2)
    assert len(c.pushes()) == 1

    c.at(MORNING + STILL_LIMIT + 100).bathroom(True, energy=50, seq=3)
    c.at(MORNING + STILL_LIMIT + 1000)
    c.sched.run_due()

    outs = [o for o in c.of("intervention/outcome") if o["scenario"] == "SAFETY"]
    assert outs
    r = outs[-1]["user_response"]
    assert r["complied"] is True
    assert r["evidence"] == "movement:bathroom"


def test_not_complied_when_still(c):
    """계속 안 움직이면 응답이 없는 것이다."""
    c.device("vd-01", "smart_tv")
    c.bathroom(True, energy=3)
    c.at(MORNING + STILL_LIMIT + 10).bathroom(True, energy=3, seq=2)

    c.at(MORNING + STILL_LIMIT + 1000)
    c.sched.run_due()

    outs = [o for o in c.of("intervention/outcome") if o["scenario"] == "SAFETY"]
    assert outs
    assert outs[-1]["user_response"]["complied"] is False


# ============================================================ 에스컬레이션

"""
def test_escalates_without_limit(c):
    # SAFETY 는 단계 제한이 없다 — 응답할 때까지 올린다.
    c.device("vd-01", "smart_tv")
    c.bathroom(True, energy=3)
    c.at(MORNING + STILL_LIMIT + 10).bathroom(True, energy=3, seq=2)

    nid = c.pushes()[0]["notify_id"]
    c.at(MORNING + STILL_LIMIT + 200)
    c.sched.run_due()

    n = c.engine.notifier.store.get(nid)
    assert n.escalation_level >= 2
"""

def test_escalation_ends_when_no_channel_left(c):
    """화장실에 화면이 없으면 1단계가 이미 음성이다.

    TV 는 디스플레이로 대행되는데 화장실에 디스플레이가 없어
    폴백으로 음성이 간다. 음성으로 두 번 말해봐야 의미가 없으니
    거기서 끝난다 — 보호자 통보가 3단계로 붙으면 이어진다.
    """
    c.device("vd-01", "smart_tv")
    c.bathroom(True, energy=3)
    c.at(MORNING + STILL_LIMIT + 10).bathroom(True, energy=3, seq=2)

    assert c.pushes()[0]["channels"] == [VOICE]

    nid = c.pushes()[0]["notify_id"]
    c.at(MORNING + STILL_LIMIT + 200)
    c.sched.run_due()

    n = c.engine.notifier.store.get(nid)
    assert n.escalation_level == 1
    assert n.closed_reason == CLOSED_TIMEOUT