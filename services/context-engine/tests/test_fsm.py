import json
from pathlib import Path

import pytest

from hestia_engine.clock import ReplayClock
from hestia_engine.config import load, load_default
from hestia_engine.context import ContextEngine
from hestia_engine.fsm import FileT0Log, MemoryT0Log, T0Entry, T0Log
from hestia_engine.messages import parse
from hestia_engine.timers import Scheduler
from hestia_engine.world import WorldState

from test_activity import HOME, POLICY, MORNING, NIGHT


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
        self.t0log = MemoryT0Log()
        self.engine = ContextEngine(
            self.clock, self.config, self.world, self.sched, t0log=self.t0log
        )

    def at(self, ts: float):
        while (due := self.sched.next_due()) is not None and due <= ts:
            self.sched.run_due(until=due)
        self.clock.advance_to(ts)
        return self

    def feed(self, topic: str, payload: dict):
        msg = parse(topic, json.dumps(payload), self.clock.now())
        assert msg is not None
        self.world.apply(msg)
        self.engine.note_event(msg)
        return self.engine.recompute()

    def tick(self):
        return self.engine.recompute()

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

    def power(self, vid: str, watt: float, state: str):
        return self.feed(f"hestia/sensor/{vid}/state", {
            "version": 1, "sent_ts": 0, "src_id": vid, "seq": 1,
            "type": "power", "watt": watt, "state": state,
        })

    def dispensed(self):
        return self.feed("hestia/device/vd-06/event", {
            "version": 1, "sent_ts": 0, "src_id": "vd-06", "seq": 1,
            "device_type": "water_purifier", "source": "mock",
            "event_type": "dispensed", "water_type": "AMBIENT", "amount_ml": 200,
        })

    @property
    def meal(self):
        return self.engine.meal_fsm

    @property
    def wake(self):
        return self.engine.wake_fsm.state


@pytest.fixture
def c(tmp_path):
    return Ctx(tmp_path)


@pytest.fixture
def night(tmp_path):
    return Ctx(tmp_path, start=NIGHT)


# ============================================================ t0 로그


def test_memory_log_satisfies_protocol():
    assert isinstance(MemoryT0Log(), T0Log)
    assert isinstance(FileT0Log(Path("/tmp/x.jsonl")), T0Log)


def test_file_log_appends_jsonl(tmp_path):
    path = tmp_path / "sub" / "t0.jsonl"
    log = FileT0Log(path)
    log.write(T0Entry("2026-09-25", "meal", 1790295600.0, "sensor", False, 2100.0))
    log.write(T0Entry("2026-09-26", "meal", 1790382000.0, "sensor", True, 1800.0))

    lines = path.read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == 2
    assert json.loads(lines[1])["prompted"] is True


def test_file_log_failure_does_not_raise(tmp_path):
    """로그 실패가 엔진을 멈추게 해서는 안 된다."""
    bad = tmp_path / "file.txt"
    bad.write_text("x", encoding="utf-8")
    log = FileT0Log(bad / "nested" / "t0.jsonl")
    log.write(T0Entry("2026-09-25", "meal", 0.0, "sensor", False, 0.0))   # 예외 없이 통과


# ============================================================ Meal FSM


def test_session_opens_on_meal_prep(c):
    c.presence("vs-04", True)
    c.at(MORNING + 100).power("vs-06", 1180, "ON")
    assert c.engine.activity.state == "MEAL_PREP"
    assert c.meal.t0 is not None


def test_kitchen_misc_does_not_open_session(c):
    """물 마시러 들른 것이 식사로 기록되면 KDE 분포가 망가진다."""
    c.presence("vs-04", True)
    assert c.engine.activity.state == "KITCHEN_MISC"
    assert c.meal.t0 is None


def test_t0_is_cooking_start_not_judgement_time(c):
    """판정 시각을 쓰면 t0 가 밀리고 KDE 분포가 통째로 틀어진다."""
    c.presence("vs-04", True)
    c.at(MORNING + 100).power("vs-06", 1180, "ON")
    assert c.meal.t0 == MORNING + 100


def test_t0_survives_prep_to_eating(c):
    """명세: MEAL_PREP -> EATING 전이 시에도 t0 는 유지된다."""
    c.presence("vs-04", True)
    c.at(MORNING + 100).power("vs-06", 1180, "ON")
    t0 = c.meal.t0

    c.at(MORNING + 1000).power("vs-06", 3, "STANDBY")
    c.at(MORNING + 1300).presence("vs-04", True, energy=12)
    assert c.engine.activity.state == "EATING"
    assert c.meal.t0 == t0


def test_activity_payload_carries_t0(c):
    c.presence("vs-04", True)
    c.at(MORNING + 100).power("vs-06", 1180, "ON")
    assert c.engine.activity.payload(c.clock.now())["t0"] == MORNING + 100


def test_session_survives_brief_absence(c):
    """주방에서 잠깐 거실로 갔다 오는 것을 두 번의 식사로 세면 안 된다."""
    c.presence("vs-04", True)
    c.at(MORNING + 100).power("vs-06", 1180, "ON")
    t0 = c.meal.t0

    c.at(MORNING + 1000).power("vs-06", 3, "STANDBY")
    c.at(MORNING + 1300).presence("vs-04", False)   # 잠깐 이탈
    c.at(MORNING + 1500).tick()                     # grace 600 안
    assert c.meal.t0 == t0


def test_session_closes_after_grace(c):
    c.presence("vs-04", True)
    c.at(MORNING + 100).power("vs-06", 1180, "ON")
    c.at(MORNING + 1000).power("vs-06", 3, "STANDBY")
    c.at(MORNING + 1300).presence("vs-04", False)
    c.at(MORNING + 2100).tick()                     # grace 600 초과
    assert c.meal.t0 is None
    assert len(c.t0log.of_type("meal")) == 1


def test_closed_session_is_logged(c):
    c.presence("vs-04", True)
    c.at(MORNING + 100).power("vs-06", 1180, "ON")
    c.at(MORNING + 1000).power("vs-06", 3, "STANDBY")
    c.at(MORNING + 1300).presence("vs-04", False)
    c.at(MORNING + 2100).tick()

    entry = c.t0log.of_type("meal")[0]
    assert entry.t0 == MORNING + 100
    assert entry.date == "2026-09-25"
    assert entry.source == "sensor"
    assert entry.prompted is False
    assert entry.duration_sec > 0


def test_short_session_not_logged(c):
    """min_duration_sec 미만은 기록하지 않는다."""
    c.presence("vs-04", True)
    c.at(MORNING + 100).power("vs-06", 1180, "ON")
    c.at(MORNING + 150).power("vs-06", 3, "STANDBY")   # 50초
    c.at(MORNING + 160).presence("vs-04", False)
    c.at(MORNING + 1000).tick()
    assert c.t0log.of_type("meal") == ()


def test_session_timeout(c):
    """2시간이면 강제 종료한다. 전력이 꺼지기 전까지 다시 열지 않는다 —
    같은 t0 가 2시간마다 쌓이면 KDE 분포가 왜곡된다."""
    c.presence("vs-04", True)
    c.at(MORNING + 100).power("vs-06", 1180, "ON")

    c.at(MORNING + 7400).tick()
    assert len(c.t0log.of_type("meal")) == 1
    assert c.meal.t0 is None

    c.at(MORNING + 8000).tick()                     # 여전히 ON 이지만
    assert c.meal.t0 is None                        # 다시 열리지 않는다


def test_prompted_flag(c):
    """유도된 행동이 개인 분포를 오염시키지 않도록 분리 기록한다."""
    c.presence("vs-04", True)
    c.at(MORNING + 100).power("vs-06", 1180, "ON")
    c.meal.mark_prompted()

    c.at(MORNING + 1000).power("vs-06", 3, "STANDBY")
    c.at(MORNING + 1300).presence("vs-04", False)
    c.at(MORNING + 2100).tick()
    assert c.t0log.of_type("meal")[0].prompted is True


def test_two_meals_two_entries(c):
    """하루에 여러 번 열린다. 아침·점심을 나누지 않고 t0 시각만 남긴다."""
    for offset in (0, 20000):
        c.at(MORNING + offset + 100).presence("vs-04", True)
        c.at(MORNING + offset + 200).power("vs-06", 1180, "ON")
        c.at(MORNING + offset + 1000).power("vs-06", 3, "STANDBY")
        c.at(MORNING + offset + 1300).presence("vs-04", False)
        c.at(MORNING + offset + 2100).tick()
    assert len(c.t0log.of_type("meal")) == 2


def test_fsm_does_not_change_activity(c):
    """단방향 — FSM 은 activity 를 읽기만 한다."""
    c.presence("vs-04", True)
    c.at(MORNING + 100).power("vs-06", 1180, "ON")
    before = c.engine.activity.state
    c.meal.mark_prompted()
    c.at(MORNING + 200).tick()
    assert c.engine.activity.state == before


# ============================================================ wake FSM


def test_starts_asleep(c):
    c.tick()
    assert c.wake.state == "ASLEEP"
    assert c.wake.wake_t0 is None


def test_wake_confirmed_on_activity(c):
    c.bed("vs-09", True)
    c.at(MORNING + 100).bed("vs-09", False)
    c.at(MORNING + 110).presence("vs-11", True)     # 화장실
    c.at(MORNING + 200).tick()                      # activity min_hold 60초 경과
    assert c.engine.activity.state == "BATHROOM"
    assert c.wake.state == "AWAKE"


def test_wake_t0_is_bed_left_time(c):
    """wake_t0 는 기상 확정 시각이 아니라 침대를 떠난 시각이다.

    명세: KDE 기상 분포의 입력.
    """
    c.bed("vs-09", True)
    c.at(MORNING + 100).bed("vs-09", False)
    c.presence("vs-08", True, energy=50)            # WAKING
    c.at(MORNING + 500).tick()                      # wake_confirm 300 경과
    assert c.wake.state == "AWAKE"
    assert c.wake.wake_t0 is not None


def test_wake_logged_once_per_day(c):
    c.bed("vs-09", True)
    c.at(MORNING + 100).bed("vs-09", False)
    c.at(MORNING + 110).presence("vs-11", True)
    c.at(MORNING + 200).tick()
    c.at(MORNING + 300).tick()
    assert len(c.t0log.of_type("wake")) == 1


def test_hydration_flag(c):
    c.bed("vs-09", True)
    c.at(MORNING + 100).bed("vs-09", False)
    c.at(MORNING + 110).presence("vs-11", True)
    assert c.wake.hydration_done is False

    c.at(MORNING + 300).dispensed()
    assert c.wake.hydration_done is True
    assert c.wake.hydration_prompted is False


def test_hydration_lag_logged(c):
    """명세의 KDE 분포 중 하나 — 기상에서 첫 물 사용까지의 간격."""
    c.bed("vs-09", True)
    c.at(MORNING + 100).bed("vs-09", False)
    c.at(MORNING + 110).presence("vs-11", True)
    c.at(MORNING + 400).dispensed()

    entry = c.t0log.of_type("hydration")[0]
    assert entry.duration_sec > 0


def test_hydration_only_once(c):
    c.bed("vs-09", True)
    c.at(MORNING + 100).bed("vs-09", False)
    c.at(MORNING + 110).presence("vs-11", True)
    c.at(MORNING + 300).dispensed()
    c.at(MORNING + 600).dispensed()
    assert len(c.t0log.of_type("hydration")) == 1


def test_hydration_ignored_while_asleep(c):
    c.dispensed()
    assert c.wake.hydration_done is False


def test_hydration_outside_window_ignored(c):
    """기상 후 2시간 밖의 급수는 기상 루틴이 아니다."""
    c.bed("vs-09", True)
    c.at(MORNING + 100).bed("vs-09", False)
    c.at(MORNING + 110).presence("vs-11", True)
    c.at(MORNING + 10000).dispensed()               # hydration_window 7200 초과
    assert c.wake.hydration_done is False


def test_meal_flag(c):
    c.bed("vs-09", True)
    c.at(MORNING + 100).bed("vs-09", False)
    c.at(MORNING + 110).presence("vs-11", True)
    assert c.wake.meal_done is False

    c.at(MORNING + 200).presence("vs-11", False)
    c.at(MORNING + 300).presence("vs-04", True)
    c.at(MORNING + 400).power("vs-06", 1180, "ON")
    c.at(MORNING + 1300).power("vs-06", 3, "STANDBY")
    c.at(MORNING + 1600).presence("vs-04", True, energy=12)
    assert c.engine.activity.state == "EATING"
    assert c.wake.meal_done is True


def test_reset_on_next_sleep(c):
    """리셋은 다음 수면에서. activity 는 '지금'이고 wake 는 '오늘 하루'다."""
    # 아침에 기상
    c.bed("vs-09", True)
    c.at(MORNING + 100).bed("vs-09", False)
    c.at(MORNING + 110).presence("vs-11", True)
    c.at(MORNING + 200).tick()
    assert c.wake.state == "AWAKE"

    c.at(MORNING + 300).dispensed()
    assert c.wake.hydration_done is True

    # 밤에 취침 — MORNING 09:40 에서 23:00 까지
    night_at = MORNING + 48000                      # 약 23:00
    c.at(night_at).presence("vs-11", False)
    c.at(night_at + 100).bed("vs-09", True)
    c.at(night_at + 110).presence("vs-08", True, energy=5)

    c.at(night_at + 800).tick()                     # still 600 경과 → SLEEPING
    assert c.engine.activity.state == "SLEEPING"

    c.at(night_at + 1500).tick()                    # sleep_confirm 600 경과
    assert c.wake.state == "ASLEEP"
    assert c.wake.hydration_done is False           # 리셋됨


def test_wake_payload_shape(c):
    p = c.engine.wake_fsm.state.payload(c.clock.now())
    assert set(p) == {
        "state", "wake_t0",
        "hydration_done", "hydration_prompted",
        "meal_done", "meal_prompted",
        "medication_done", "medication_prompted",
    }

def test_done_at_records_time(c):
    """_done 플래그만으로는 언제 했는지 모른다 — outcome 의 delay_sec 에 필요."""
    c.bed("vs-09", True)
    c.at(MORNING + 100).bed("vs-09", False)
    c.at(MORNING + 110).presence("vs-11", True)
    c.at(MORNING + 200).tick()

    c.at(MORNING + 400).dispensed()
    assert c.wake.hydration_at == MORNING + 400
    assert c.engine.wake_fsm.done_at("hydration") == MORNING + 400


def test_done_at_none_before(c):
    c.bed("vs-09", True)
    c.at(MORNING + 100).bed("vs-09", False)
    c.at(MORNING + 110).presence("vs-11", True)
    c.at(MORNING + 200).tick()
    assert c.engine.wake_fsm.done_at("hydration") is None


def test_done_at_not_published(c):
    """명세의 wake 페이로드에 없는 내부 값이다."""
    p = c.engine.wake_fsm.state.payload(c.clock.now())
    assert "hydration_at" not in p
    assert len(p) == 8


def test_done_at_resets(c):
    """다음 수면에 플래그와 함께 비워진다."""
    c.bed("vs-09", True)
    c.at(MORNING + 100).bed("vs-09", False)
    c.at(MORNING + 110).presence("vs-11", True)
    c.at(MORNING + 200).tick()
    c.at(MORNING + 400).dispensed()
    assert c.wake.hydration_at is not None

    night_at = MORNING + 48000
    c.at(night_at).presence("vs-11", False)
    c.at(night_at + 100).bed("vs-09", True)
    c.at(night_at + 110).presence("vs-08", True, energy=5)
    c.at(night_at + 800).tick()
    c.at(night_at + 1500).tick()

    assert c.wake.state == "ASLEEP"
    assert c.wake.hydration_at is None


# ============================================================ 실제 시나리오


def test_morning_scenario_logs_meal_t0():
    """3주치를 재생하면 t0 로그가 쌓이고, 그것이 baseline.py 의 입력이 된다."""
    from hestia_engine.replay import Replay

    clock = ReplayClock(0)
    cfg = load_default()
    sched = Scheduler(clock)
    world = WorldState(clock, cfg, sched)
    t0log = MemoryT0Log()
    engine = ContextEngine(clock, cfg, world, sched, t0log=t0log)

    class Feed:
        def ingest(self, topic, payload):
            msg = parse(topic, payload, clock.now())
            if msg:
                world.apply(msg)
                engine.note_event(msg)
                engine.recompute()

    Replay(clock, sched, Feed()).run(Path(__file__).parent / "data" / "morning.jsonl")

    assert engine.wake_fsm.state.state == "AWAKE"
    assert engine.wake_fsm.state.hydration_done is True
    assert engine.meal_fsm.t0 is not None            # 아직 열려 있다
    assert len(t0log.of_type("wake")) == 1


# ============================================================ retained 수신
def test_retained_power_does_not_open_session(c):
    """재시작 직후 retained 로 'ON 이다' 만 알면 묶음을 열지 않는다.

    언제 켰는지 모르는데 재시작 시각을 t0 로 쓰면 KDE 분포에
    엉뚱한 점이 찍힌다. 틀린 값보다 빈 값이 낫다.
    """
    import json
    from hestia_engine.messages import parse

    msg = parse("hestia/sensor/vs-06/state", json.dumps({
        "version": 1, "sent_ts": 1000, "src_id": "vs-06", "seq": 1,
        "type": "power", "watt": 1180, "state": "ON",
    }), c.clock.now())
    c.world.apply(msg, retained=True)
    c.engine.recompute()

    assert c.engine.activity.state == "UNKNOWN"   # 주방 재실이 없으니
    assert c.meal.t0 is None


def test_power_after_restart_opens_normally(c):
    """재시작 뒤 새로 켜지는 것은 실시간 이벤트라 정상 기록된다."""
    import json
    from hestia_engine.messages import parse

    # retained 로 ON 을 먼저 받는다
    msg = parse("hestia/sensor/vs-06/state", json.dumps({
        "version": 1, "sent_ts": 1000, "src_id": "vs-06", "seq": 1,
        "type": "power", "watt": 1180, "state": "ON",
    }), c.clock.now())
    c.world.apply(msg, retained=True)

    # 꺼졌다가
    c.at(MORNING + 100).power("vs-06", 2, "OFF")
    # 다시 켜진다 — 이건 실시간
    c.at(MORNING + 200).presence("vs-04", True)
    c.at(MORNING + 300).power("vs-06", 1180, "ON")

    assert c.meal.t0 == MORNING + 300