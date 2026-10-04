import json

import pytest

from hestia_engine.clock import ReplayClock
from hestia_engine.config import load
from hestia_engine.engine import Engine, RecordingPublisher
from hestia_engine.messages import parse
from hestia_engine.timers import Scheduler
from hestia_engine.world import WorldState

from test_activity import HOME, POLICY, MORNING
from test_model import kde_payload


def lag_dist(peak_min: int = 30, step: int = 5, span: int = 24) -> dict:
    """기상 후 peak_min 분 근처에 봉우리가 있는 hydration_lag 분포."""
    density = [0.0] * span
    center = peak_min // step
    for i in range(max(0, center - 1), min(span, center + 2)):
        density[i] = 1.0
    total = sum(density)
    return {"grid_min": 0, "grid_step": step,
            "density": [d / total for d in density]}


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

    def model(self, peak_min: int = 30, predictability: float = 0.33):
        payload = kde_payload(hydration_lag=lag_dist(peak_min))
        payload["predictability"] = {"hydration_lag": predictability}
        return self.send("hestia/model/kde", payload)

    def presence(self, vid: str, present: bool, energy: int = 50):
        return self.send(f"hestia/sensor/{vid}/state", {
            "version": 1, "sent_ts": 0, "src_id": vid, "seq": 1,
            "type": "presence", "present": present, "confidence": "high",
            "energy": energy, "distance_cm": 150,
        })

    def bed(self, vid: str, occupied: bool):
        return self.send(f"hestia/sensor/{vid}/state", {
            "version": 1, "sent_ts": 0, "src_id": vid, "seq": 1,
            "type": "bed", "occupied": occupied,
        })

    def device(self, vid: str, device_type: str, power: str = "ON"):
        return self.send(f"hestia/device/{vid}/state", {
            "version": 1, "sent_ts": 0, "src_id": vid, "seq": 1,
            "device_type": device_type, "source": "mock", "power": power,
        })

    def dispensed(self):
        return self.send("hestia/device/vd-06/event", {
            "version": 1, "sent_ts": 0, "src_id": "vd-06", "seq": 1,
            "device_type": "water_purifier", "source": "mock",
            "event_type": "dispensed", "water_type": "AMBIENT", "amount_ml": 200,
        })

    def wake_up(self):
        """기상 확정까지의 최소 흐름."""
        self.bed("vs-09", True)
        self.at(MORNING + 100).bed("vs-09", False)
        self.at(MORNING + 110).presence("vs-11", True)
        self.at(MORNING + 200).engine.ingest(
            "hestia/sensor/vs-11/state",
            json.dumps({"version": 1, "sent_ts": 0, "src_id": "vs-11", "seq": 9,
                        "type": "presence", "present": True, "confidence": "high",
                        "energy": 40, "distance_cm": 110}),
        )
        return self

    def of(self, topic: str) -> tuple[dict, ...]:
        return self.pub.of_topic(f"hestia/{topic}")

    def reasons(self) -> list[str]:
        return [p["reason"] for p in self.of("intervention/decision")]


@pytest.fixture
def c(tmp_path):
    return Ctx(tmp_path)


# ============================================================ 트리거


def test_nothing_before_wake(c):
    """기상 전에는 판정조차 하지 않는다."""
    c.model()
    c.presence("vs-01", True)
    assert c.of("intervention/decision") == ()


def test_grace_delays_first_judgement(c):
    """일어나자마자 묻지 않는다 — 물을 바로 마시는 사람도 있다."""
    c.model()
    c.wake_up()
    c.at(MORNING + 600).engine.scenarios.tick(c.engine.context)
    assert c.of("intervention/decision") == ()


def test_judges_after_grace(c):
    c.model()
    c.wake_up()
    c.at(MORNING + 1100).engine.scenarios.tick(c.engine.context)
    assert len(c.of("intervention/decision")) == 1


def test_stops_once_hydrated(c):
    """이미 마셨으면 묻지 않는다."""
    c.model()
    c.wake_up()
    c.at(MORNING + 300).dispensed()
    c.at(MORNING + 1100).engine.scenarios.tick(c.engine.context)
    assert c.of("notify/push") == ()


# ============================================================ 판정


def test_normal_lag_no_notify(c):
    """기상 30분 봉우리에서 20분은 평소 범위다."""
    c.model(peak_min=30)
    c.wake_up()
    c.at(MORNING + 1300).engine.scenarios.tick(c.engine.context)      # 기상 후 20분

    assert c.reasons() == ["TIME_NORMAL"]
    assert c.of("notify/push") == ()


def test_late_lag_notifies(c):
    """기상 후 한참 지났는데 안 마셨다 — 이 사람 분포에서 꼬리."""
    c.model(peak_min=30)
    c.wake_up()
    c.at(MORNING + 5000).engine.scenarios.tick(c.engine.context)

    assert "TIME_ANOMALY" in c.reasons()
    assert len(c.of("notify/push")) == 1


def test_unreliable_pattern_blocks(c):
    """단순히 늦다는 이유만으로 개입하지 않는다 (명세)."""
    c.model(peak_min=30, predictability=0.01)
    c.wake_up()
    c.at(MORNING + 5000).engine.scenarios.tick(c.engine.context)

    assert "PATTERN_UNRELIABLE" in c.reasons()
    assert c.of("notify/push") == ()


def test_no_model_no_notify(c):
    c.wake_up()
    c.at(MORNING + 5000).engine.scenarios.tick(c.engine.context)

    assert c.reasons() == ["NO_KDE_MODEL"]
    assert c.of("notify/push") == ()


# ============================================================ decision 발행


def test_decision_only_on_change(c):
    """판정은 context 마다 일어난다. 매번 발행하면 적재량이 의미 없이 는다."""
    c.model()
    c.wake_up()
    for step in (1100, 1200, 1300):
        c.at(MORNING + step).engine.scenarios.tick(c.engine.context)

    assert len(c.of("intervention/decision")) == 1      # TIME_NORMAL 한 번


def test_decision_published_on_reason_change(c):
    c.model(peak_min=30)
    c.wake_up()
    c.at(MORNING + 1300).engine.scenarios.tick(c.engine.context)      # NORMAL
    c.at(MORNING + 5000).engine.scenarios.tick(c.engine.context)      # ANOMALY

    assert c.reasons() == ["TIME_NORMAL", "TIME_ANOMALY"]


def test_decision_payload_shape(c):
    c.model()
    c.wake_up()
    c.at(MORNING + 1300).engine.scenarios.tick(c.engine.context)

    d = c.of("intervention/decision")[0]
    assert d["kind"] == "hydration"
    assert d["decision_id"].startswith("d-")
    assert set(d) >= {
        "version", "sent_ts", "src_id", "decision_id",
        "candidate", "reason", "kind",
        "tail_probability", "predictability", "confidence", "factors",
    }


def test_notify_links_to_decision(c):
    """candidate 인 판정과 그 알림이 이어져야 추적이 된다."""
    c.model(peak_min=30)
    c.wake_up()
    c.at(MORNING + 5000).engine.scenarios.tick(c.engine.context)

    decision = [d for d in c.of("intervention/decision") if d["candidate"]][0]
    nid = c.of("notify/push")[0]["notify_id"]
    pending = c.engine.notifier.store.get(nid)
    assert pending.decision_id == decision["decision_id"]


# ============================================================ 전체 흐름


def test_full_cycle_complied(c):
    """판정 → 알림 → 급수 → outcome."""
    c.model(peak_min=30)
    c.wake_up()
    c.device("vd-05", "smart_fridge")
    c.at(MORNING + 5000).engine.scenarios.tick(c.engine.context)

    assert len(c.of("notify/push")) == 1

    c.at(MORNING + 5300).dispensed()
    c.at(MORNING + 7000)
    c.sched.run_due()

    r = c.of("intervention/outcome")[0]["user_response"]
    assert r["complied"] is True
    assert r["evidence"] == "dispensed"


def test_full_cycle_not_complied(c):
    c.model(peak_min=30)
    c.wake_up()
    c.device("vd-05", "smart_fridge")
    c.at(MORNING + 5000).engine.scenarios.tick(c.engine.context)

    c.at(MORNING + 7000)
    c.sched.run_due()

    assert c.of("intervention/outcome")[0]["user_response"]["complied"] is False


def test_timer_path_triggers_scenario(c):
    """시간 경과로만 성립하는 조건이 타이머 경로에서도 걸린다."""
    c.model(peak_min=30)
    c.wake_up()
    before = len(c.of("intervention/decision"))

    c.clock.advance_to(MORNING + 5000)
    c.sched.run_due()

    assert len(c.of("intervention/decision")) > before


def test_no_duplicate_notify(c):
    c.model(peak_min=30)
    c.wake_up()
    c.device("vd-05", "smart_fridge")
    for step in (5000, 5100, 5200):
        c.at(MORNING + step).engine.scenarios.tick(c.engine.context)

    assert len(c.of("notify/push")) == 1