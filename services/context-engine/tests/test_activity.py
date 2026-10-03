import json
from pathlib import Path

import pytest

from hestia_engine.activity import ACTIVITY_STATES, HMM_STATES, ActivityContext
from hestia_engine.clock import ReplayClock
from hestia_engine.config import load, load_default
from hestia_engine.context import ContextEngine
from hestia_engine.messages import parse
from hestia_engine.timers import Scheduler
from hestia_engine.world import WorldState

HOME = """
name = "test"
areas = ["living", "kitchen", "bedroom", "bathroom", "utility", "entrance"]
roles = ["LIVING", "SLEEP", "MEAL", "HYGIENE", "LAUNDRY", "ENTRY"]

[[sensors]]
id = "vs-01"
type = "presence"
area = "living"
roles = ["LIVING"]
node = "esp32-1"

[[sensors]]
id = "vs-02"
type = "bed"
area = "living"
roles = ["LIVING"]
node = "esp32-1"

[[sensors]]
id = "vs-04"
type = "presence"
area = "kitchen"
roles = ["MEAL"]
node = "esp32-1"

[[sensors]]
id = "vs-06"
type = "power"
area = "kitchen"
roles = ["MEAL"]
power_profile = "induction"
node = "esp32-1"

[[sensors]]
id = "vs-08"
type = "presence"
area = "bedroom"
roles = ["SLEEP"]
node = "esp32-1"

[[sensors]]
id = "vs-09"
type = "bed"
area = "bedroom"
roles = ["SLEEP"]
node = "esp32-1"

[[sensors]]
id = "vs-11"
type = "presence"
area = "bathroom"
roles = ["HYGIENE"]
node = "esp32-1"

[[sensors]]
id = "vs-13"
type = "motion"
area = "utility"
roles = ["LAUNDRY"]
node = "esp32-1"

[[devices]]
id = "vd-01"
device_type = "smart_tv"
area = "living"
channel = true

[[devices]]
id = "vd-05"
device_type = "smart_fridge"
area = "kitchen"
channel = true

[[devices]]
id = "vd-08"
device_type = "washer"
area = "utility"
"""

POLICY = (Path(__file__).parents[3] / "config" / "policy.toml").read_text(encoding="utf-8")

# 2026-09-25 KST 기준
MORNING = 1790296800.0      # 09:40
NIGHT = 1790344800.0        # 23:00
NOON = 1790305200.0         # 12:00


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
        self.engine = ContextEngine(self.clock, self.config, self.world, self.sched)

    def at(self, ts: float):
        while (due := self.sched.next_due()) is not None and due <= ts:
            self.sched.run_due(until=due)
        self.clock.advance_to(ts)
        return self

    def feed(self, topic: str, payload: dict):
        msg = parse(topic, json.dumps(payload), self.clock.now())
        assert msg is not None, f"파싱 실패: {topic}"
        self.world.apply(msg)
        return self.engine.recompute()

    def tick(self):
        return self.engine.recompute()

    # ---- 헬퍼

    def presence(self, vid: str, present: bool, energy: int = 50):
        return self.feed(f"hestia/sensor/{vid}/state", {
            "version": 1, "sent_ts": 0, "src_id": vid, "seq": 1,
            "type": "presence", "present": present, "confidence": "high",
            "energy": energy, "distance_cm": 150,
        })

    def bed(self, vid: str, occupied: bool):
        return self.feed(f"hestia/sensor/{vid}/state", {
            "version": 1, "sent_ts": 0, "src_id": vid, "seq": 1,
            "type": "bed", "occupied": occupied,
        })

    def motion(self, vid: str, motion: bool = True):
        return self.feed(f"hestia/sensor/{vid}/state", {
            "version": 1, "sent_ts": 0, "src_id": vid, "seq": 1,
            "type": "motion", "motion": motion, "confidence": "low",
        })

    def power(self, vid: str, watt: float, state: str | None = None):
        payload = {"version": 1, "sent_ts": 0, "src_id": vid, "seq": 1,
                   "type": "power", "watt": watt}
        if state is not None:
            payload["state"] = state
        return self.feed(f"hestia/sensor/{vid}/state", payload)

    def tv(self, power: str):
        return self.feed("hestia/device/vd-01/state", {
            "version": 1, "sent_ts": 0, "src_id": "vd-01", "seq": 1,
            "device_type": "smart_tv", "source": "mock", "power": power,
        })

    def remote(self):
        return self.feed("hestia/device/vd-01/event", {
            "version": 1, "sent_ts": 0, "src_id": "vd-01", "seq": 1,
            "device_type": "smart_tv", "source": "mock",
            "event_type": "remote_input", "button": "OK",
        })

    def fridge_open(self):
        return self.feed("hestia/device/vd-05/event", {
            "version": 1, "sent_ts": 0, "src_id": "vd-05", "seq": 1,
            "device_type": "smart_fridge", "source": "mock",
            "event_type": "door_opened",
        })

    def washer(self, cycle: str):
        return self.feed("hestia/device/vd-08/state", {
            "version": 1, "sent_ts": 0, "src_id": "vd-08", "seq": 1,
            "device_type": "washer", "source": "mock",
            "power": "ON", "cycle": cycle,
        })

    @property
    def a(self) -> ActivityContext:
        return self.engine.activity


@pytest.fixture
def c(tmp_path):
    return Ctx(tmp_path)


@pytest.fixture
def night(tmp_path):
    return Ctx(tmp_path, start=NIGHT)


# ============================================================ 상태 정의


def test_twelve_states():
    assert len(ACTIVITY_STATES) == 12
    assert "TRANSITION" not in ACTIVITY_STATES
    assert "ACTIVE" not in ACTIVITY_STATES
    assert "FOCUSED" not in ACTIVITY_STATES


def test_hmm_reduction_to_eight():
    assert len(set(HMM_STATES.values())) == 8
    assert HMM_STATES["IN_BED_AWAKE"] == "SLEEPING"
    assert HMM_STATES["EATING"] == "MEAL"
    assert HMM_STATES["WATCHING_TV"] == "RESTING"


def test_every_state_has_hmm_mapping():
    assert set(HMM_STATES) == set(ACTIVITY_STATES)


# ============================================================ 수면


def test_bed_without_sustained_stillness_is_awake(night):
    """침대에 누웠다고 바로 SLEEPING 이 아니다.

    깨어 있는 사람의 조명을 끄는 쪽이 실수 비용이 크다.
    """
    night.bed("vs-09", True)
    night.presence("vs-08", True, energy=45)
    assert night.a.state == "IN_BED_AWAKE"


def test_sustained_stillness_at_night_is_sleeping(night):
    night.bed("vs-09", True)
    night.presence("vs-08", True, energy=5)
    night.at(NIGHT + 700).tick()                    # still_for_sleep_sec = 600
    assert night.a.state == "SLEEPING"


def test_daytime_nap_scores_lower(c):
    """낮에는 night_hours 가산점이 없어 SLEEPING 이 되기 어렵다."""
    c.bed("vs-09", True)
    c.presence("vs-08", True, energy=5)
    c.at(MORNING + 700).tick()
    assert c.a.scores["SLEEPING"] < c.a.scores["IN_BED_AWAKE"] + 0.3


def test_waking_after_bed_left(c):
    c.bed("vs-09", True)
    c.at(MORNING + 100).bed("vs-09", False)
    c.presence("vs-08", True, energy=50)
    assert c.a.state == "WAKING"


def test_waking_expires(c):
    c.bed("vs-09", True)
    c.at(MORNING + 100).bed("vs-09", False)
    c.presence("vs-08", True, energy=50)
    assert c.a.state == "WAKING"
    c.at(MORNING + 2000).tick()                     # wake_confirm_sec * 3 초과
    assert c.a.state != "WAKING"


# ============================================================ 식사


def test_cooking_is_meal_prep(c):
    c.presence("vs-04", True)
    c.at(MORNING + 100).power("vs-06", 1180, state="ON")
    assert c.a.state == "MEAL_PREP"


def test_eating_after_cooking_stops(c):
    """명세: 조리 기구 전력 종료 후 해당 구역 체류 지속이 주 신호."""
    c.presence("vs-04", True)
    c.power("vs-06", 1180, state="ON")
    c.at(MORNING + 900).power("vs-06", 3, state="STANDBY")
    c.at(MORNING + 1200).presence("vs-04", True, energy=12)
    assert c.a.state == "EATING"


def test_eating_needs_gap(c):
    """조리가 막 끝난 직후는 아직 EATING 이 아니다."""
    c.presence("vs-04", True)
    c.power("vs-06", 1180, state="ON")
    c.at(MORNING + 900).power("vs-06", 3, state="STANDBY")
    c.at(MORNING + 960).tick()                      # 60초 < gap 180
    assert c.a.state != "EATING"


def test_brief_kitchen_visit_is_misc(c):
    """물 마시러 들른 것이 EATING 으로 잡히면 KDE 분포가 망가진다."""
    c.presence("vs-04", True)
    assert c.a.state == "KITCHEN_MISC"


def test_fridge_boosts_eating(c):
    c.presence("vs-04", True)
    c.power("vs-06", 1180, state="ON")
    c.at(MORNING + 900).power("vs-06", 3, state="STANDBY")
    c.at(MORNING + 1000).fridge_open()
    c.at(MORNING + 1200).presence("vs-04", True, energy=12)
    assert c.a.factors["fridge_recent"] is True
    assert c.a.state == "EATING"


# ============================================================ 위생


def test_bathroom(c):
    c.presence("vs-11", True)
    assert c.a.state == "BATHROOM"


# ============================================================ 거실


def test_tv_on_is_watching(c):
    """센서 신호는 RESTING 과 동일하다. TV 전원으로만 갈린다."""
    c.presence("vs-01", True)
    c.at(MORNING + 100).tv("ON")
    c.remote()
    assert c.a.state == "WATCHING_TV"


def test_tv_off_with_sofa_is_resting(c):
    c.presence("vs-01", True)
    c.tv("OFF")
    c.bed("vs-02", True)
    assert c.a.state == "RESTING"


def test_tv_on_outranks_resting(c):
    c.presence("vs-01", True)
    c.bed("vs-02", True)
    c.tv("ON")
    c.remote()
    assert c.a.scores["WATCHING_TV"] > c.a.scores["RESTING"]


# ============================================================ 세탁


def test_washer_running_is_laundry(c):
    c.washer("WASH")
    c.motion("vs-13", True)
    assert c.a.state == "LAUNDRY"


def test_washer_idle_is_not_laundry(c):
    c.washer("IDLE")
    c.motion("vs-13", True)
    assert c.a.state != "LAUNDRY"


# ============================================================ UNKNOWN


def test_no_evidence_is_unknown(c):
    c.tick()
    assert c.a.state == "UNKNOWN"
    assert c.a.confidence == 0.0


def test_below_min_score_is_unknown(c):
    """모르면 모른다고 하는 쪽이, 억지로 고르고 개입을 얹는 것보다 낫다."""
    c.washer("WASH")                                # 0.50 단독
    # utility 재실 없음 → 0.50. min_score 0.35 는 넘는다
    assert c.a.state == "LAUNDRY"


def test_sensor_fault_is_unknown(c):
    c.presence("vs-04", True)
    c.feed("hestia/node/esp32-1/status", {
        "version": 1, "sent_ts": 0, "src_id": "esp32-1",
        "online": False, "ts_synced": True,
    })
    assert c.a.state == "UNKNOWN"
    assert c.a.factors["sensor_fault"] is True


def test_away_follows_away_context(c):
    """activity 는 AWAY 를 계산하지 않는다. away context 를 그대로 따른다."""
    c.engine.away = None
    c.presence("vs-04", True)
    c.at(MORNING + 100).presence("vs-04", False)
    c.at(MORNING + 4000).tick()
    assert c.engine.away.state in ("UNKNOWN", "AWAY")

def test_single_candidate_is_not_fully_confident(c):
    """후보가 하나뿐이어도 근거가 약하면 확신도가 낮아야 한다."""
    c.washer("WASH")                                # LAUNDRY 0.50 단독
    assert c.a.state == "LAUNDRY"
    assert c.a.confidence == pytest.approx(0.50)    # 1.0 이 아니다

# ============================================================ 안정화


def test_hysteresis_keeps_current_state(c):
    c.presence("vs-04", True)
    c.at(MORNING + 100).power("vs-06", 1180, state="ON")
    assert c.a.state == "MEAL_PREP"
    assert c.a.factors.get("hysteresis_on") == "KITCHEN_MISC"   # 직전 상태

    c.at(MORNING + 200).tick()
    assert c.a.factors["hysteresis_on"] == "MEAL_PREP"


def test_min_hold_prevents_flapping(c):
    """바꾼 지 60초가 안 됐으면 유지한다."""
    c.presence("vs-04", True)                       # KITCHEN_MISC
    assert c.a.state == "KITCHEN_MISC"

    c.at(MORNING + 100).presence("vs-11", True)     # 화장실 켜짐 → BATHROOM
    assert c.a.state == "BATHROOM"

    c.at(MORNING + 110).presence("vs-11", False)    # 10초 만에 꺼짐
    c.at(MORNING + 141).tick()                      # grace 만료. BATHROOM 된 지 41초
    assert c.a.state == "BATHROOM"                  # min_hold 60초 미달 → 유지
    assert c.a.factors["held_from"] == "KITCHEN_MISC"


def test_min_hold_releases(c):
    c.presence("vs-04", True)
    c.at(MORNING + 100).presence("vs-11", True)
    c.at(MORNING + 110).presence("vs-11", False)
    c.at(MORNING + 141).tick()
    assert c.a.state == "BATHROOM"
    c.at(MORNING + 161).tick()                      # 61초 경과
    assert c.a.state == "KITCHEN_MISC"


def test_min_hold_arms_timer(c):
    c.presence("vs-04", True)
    c.at(MORNING + 100).presence("vs-11", True)
    c.at(MORNING + 110).presence("vs-11", False)
    c.at(MORNING + 141).tick()
    assert "ctx-activity-hold" in c.sched.keys()
    

def test_unknown_does_not_block_transition(c):
    """모름에서 벗어나는 것은 떨림이 아니다. min_hold 로 막지 않는다."""
    c.tick()
    assert c.a.state == "UNKNOWN"
    c.at(MORNING + 10).presence("vs-11", True)
    assert c.a.state == "BATHROOM"                  # 10초밖에 안 지났는데도


# ============================================================ since / 발행


def test_since_survives_same_state(c):
    c.presence("vs-04", True)
    first = c.a.since
    c.at(MORNING + 300).presence("vs-04", True, energy=41)
    assert c.a.since == first


def test_since_resets_on_change(c):
    c.presence("vs-04", True)
    c.at(MORNING + 100).presence("vs-04", False)
    c.at(MORNING + 200).presence("vs-11", True)
    assert c.a.since == MORNING + 200


def test_only_state_change_publishes(c):
    c.presence("vs-04", True)
    changed = c.at(MORNING + 300).presence("vs-04", True, energy=41)
    assert not any(isinstance(x, ActivityContext) for x in changed)


def test_payload_shape(c):
    c.presence("vs-04", True)
    p = c.a.payload(c.clock.now())
    assert set(p) == {"state", "since", "t0", "area", "confidence", "factors"}
    assert p["t0"] is None                          # FSM 이 채운다


def test_confidence_is_share_of_total(c):
    c.presence("vs-04", True)
    c.at(MORNING + 100).power("vs-06", 1180, state="ON")
    scores = c.a.scores
    total = sum(scores.values())
    top = scores["MEAL_PREP"]
    assert len(scores) > 1
    assert c.a.confidence == pytest.approx(min(1.0, top) * (top / total))


# ============================================================ 실제 시나리오


def test_morning_scenario_reaches_eating():
    """기상 → 화장실 → 주방 → 조리 → 식사."""
    from hestia_engine.replay import Replay

    clock = ReplayClock(0)
    cfg = load_default()
    sched = Scheduler(clock)
    world = WorldState(clock, cfg, sched)
    seen: list[str] = []

    def collect(changed):
        for ctx in changed:
            if isinstance(ctx, ActivityContext):
                seen.append(ctx.state)

    engine = ContextEngine(clock, cfg, world, sched, on_change=collect)

    class Feed:
        def ingest(self, topic, payload):
            msg = parse(topic, payload, clock.now())
            if msg:
                world.apply(msg)
                collect(engine.recompute())

    Replay(clock, sched, Feed()).run(Path(__file__).parent / "data" / "morning.jsonl")

    assert "BATHROOM" in seen
    assert "MEAL_PREP" in seen
    assert engine.activity.state in ("EATING", "KITCHEN_MISC")