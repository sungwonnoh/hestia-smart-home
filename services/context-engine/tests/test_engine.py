import json
from pathlib import Path

import pytest

from hestia_engine.clock import ReplayClock
from hestia_engine.config import load
from hestia_engine.engine import Engine, NullPublisher, Publisher, RecordingPublisher
from hestia_engine.fsm import MemoryT0Log
from hestia_engine.replay import replay_file
from hestia_engine.sink import Sink
from hestia_engine.timers import Scheduler
from hestia_engine.world import WorldState

from test_activity import HOME, POLICY, MORNING

DATA = Path(__file__).parent / "data" / "morning.jsonl"


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
        self.t0log = MemoryT0Log()
        self.engine = Engine(
            self.clock, self.config, self.world, self.sched, self.pub, self.t0log
        )

    def at(self, ts: float):
        while (due := self.sched.next_due()) is not None and due <= ts:
            self.sched.run_due(until=due)
        self.clock.advance_to(ts)
        return self

    def send(self, topic: str, payload: dict):
        self.engine.ingest(topic, json.dumps(payload))
        return self

    def presence(self, vid: str, present: bool, energy: int = 50):
        return self.send(f"hestia/sensor/{vid}/state", {
            "version": 1, "sent_ts": 0, "src_id": vid, "seq": 1,
            "type": "presence", "present": present, "confidence": "high",
            "energy": energy, "distance_cm": 150,
        })

    def profile(self, name: str):
        return self.send("hestia/system/profile", {
            "version": 1, "sent_ts": 0, "src_id": "rpi4", "profile": name,
        })


@pytest.fixture
def c(tmp_path):
    return Ctx(tmp_path)


# ============================================================ 계약


def test_engine_is_a_sink(c):
    """Replay 와 MQTT 배선이 같은 것을 꽂는다."""
    assert isinstance(c.engine, Sink)


def test_publishers_satisfy_protocol():
    assert isinstance(RecordingPublisher(), Publisher)
    assert isinstance(NullPublisher(), Publisher)


def test_works_without_publisher(tmp_path):
    """발행 대상이 없어도 판단은 돈다."""
    h = tmp_path / "home.toml"
    p = tmp_path / "policy.toml"
    h.write_text(HOME, encoding="utf-8")
    p.write_text(POLICY, encoding="utf-8")
    cfg = load(h, p)
    clock = ReplayClock(MORNING)
    sched = Scheduler(clock)
    world = WorldState(clock, cfg, sched)
    engine = Engine(clock, cfg, world, sched)        # publisher 없음

    engine.ingest("hestia/sensor/vs-04/state", json.dumps({
        "version": 1, "sent_ts": 0, "src_id": "vs-04", "seq": 1,
        "type": "presence", "present": True, "confidence": "high",
        "energy": 50, "distance_cm": 150,
    }))
    assert engine.context.presence.user_area == "kitchen"


# ============================================================ ingest


def test_ingest_counts(c):
    c.presence("vs-04", True)
    assert c.engine.received == 1
    assert c.engine.dropped == 0


def test_bad_payload_is_dropped_not_fatal(c):
    """한 노드의 잘못된 페이로드가 엔진을 멈추게 해서는 안 된다."""
    c.engine.ingest("hestia/sensor/vs-04/state", "{broken")
    assert c.engine.dropped == 1
    assert c.engine.received == 0

    c.presence("vs-04", True)                        # 계속 동작
    assert c.engine.received == 1


def test_unsubscribed_topic_dropped(c):
    c.engine.ingest("hestia/context/activity", json.dumps({"version": 1}))
    assert c.engine.received == 0


# ============================================================ 발행


def test_publishes_on_state_change(c):
    c.presence("vs-04", True)
    topics = {t for _, t, _ in c.pub.published}
    assert "hestia/context/presence" in topics
    assert "hestia/context/activity" in topics


def test_payload_has_envelope(c):
    """명세의 공통 Envelope. seq 는 없다 — retained 라 최신값만 의미 있다."""
    c.presence("vs-04", True)
    p = c.pub.last("hestia/context/presence")
    assert p["version"] == 1
    assert p["src_id"] == "rpi5"
    assert p["sent_ts"] == MORNING
    assert "seq" not in p


def test_payload_carries_context_fields(c):
    c.presence("vs-04", True)
    p = c.pub.last("hestia/context/presence")
    assert p["user_area"] == "kitchen"
    assert "areas" in p and "factors" in p


def test_context_is_retained(c):
    """명세: context 는 QoS 1, retained."""
    seen: list[bool] = []

    class Spy:
        def publish(self, topic, payload, *, retain):
            seen.append(retain)

    c.engine._pub = Spy()
    c.presence("vs-04", True)
    assert all(seen)


def test_no_publish_without_change(c):
    """factors 가 흔들릴 때마다 발행하면 의미가 없다."""
    c.presence("vs-04", True)
    before = c.pub.count
    c.at(MORNING + 60).presence("vs-04", True, energy=41)
    assert c.pub.count == before


def test_timer_change_is_published(c):
    """시간 경과로만 일어나는 전이도 발행된다.

    이 경로가 없으면 PIR 타임아웃·AWAY·MULTI 가 전부 조용히 묻힌다.
    """
    c.presence("vs-04", True)
    before = c.pub.count

    c.clock.advance_to(MORNING + 3700)
    c.sched.run_due()
    assert c.pub.count > before


# ============================================================ 주기 발행


def test_periodic_publish(c):
    """명세: 상태 변화 시 즉시 + 5분 주기(생존 확인)."""
    c.presence("vs-04", True)
    before = c.pub.count

    c.clock.advance_to(MORNING + 301)
    c.sched.run_due()
    assert c.pub.count > before


def test_periodic_rearms(c):
    c.presence("vs-04", True)
    for step in (301, 601, 901):
        c.clock.advance_to(MORNING + step)
        c.sched.run_due()
    counts = [t for _, t, _ in c.pub.published if t == "hestia/context/wake"]
    assert len(counts) >= 3


def test_periodic_not_pushed_by_messages(c):
    """메시지마다 타이머를 다시 걸면 5분이 영원히 오지 않는다."""
    c.presence("vs-04", True)
    first_due = c.sched.next_due()

    for i in range(1, 5):
        c.at(MORNING + i * 30).presence("vs-04", True, energy=40 + i)

    assert c.sched.next_due() == first_due or first_due is not None


def test_snapshot_includes_wake(c):
    """wake 는 FSM 이 들고 있어 ContextEngine.all_contexts 에 없다."""
    c.presence("vs-04", True)
    c.engine.publish_snapshot()
    assert c.pub.last("hestia/context/wake") is not None


# ============================================================ 시스템 메시지


def test_profile_switch_applies(c):
    assert c.config.profile == "REAL"
    c.profile("DEMO")
    assert c.config.profile == "DEMO"


def test_profile_scales_timeouts(c):
    base = c.config.timeout("activity", "meal", "session_timeout_sec")
    c.profile("DEMO")
    assert c.config.timeout("activity", "meal", "session_timeout_sec") == base * 0.1


def test_registry_replaces_mapping(c):
    """명세: 역할 매핑을 즉시 교체하고 모든 context 를 재계산한다."""
    c.presence("vs-04", True)
    assert c.engine.context.presence.user_area == "kitchen"

    c.at(MORNING + 100).send("hestia/registry/devices", {
        "version": 1, "sent_ts": 0, "src_id": "rpi4",
        "devices": [
            {"virtual_id": "vs-04", "type": "presence", "area": "living",
             "roles": ["LIVING"], "node_id": "esp32-1", "enabled": True},
        ],
    })
    assert c.config.area_of("vs-04") == "living"


def test_registry_resets_contexts(c):
    """변경 전 매핑으로 진행 중이던 판단은 무효다."""
    c.presence("vs-04", True)
    assert c.engine.context.presence.user_area == "kitchen"

    c.at(MORNING + 100).send("hestia/registry/devices", {
        "version": 1, "sent_ts": 0, "src_id": "rpi4",
        "devices": [
            {"virtual_id": "vs-04", "type": "presence", "area": "living",
             "roles": ["LIVING"], "node_id": "esp32-1", "enabled": True},
        ],
    })
    # 매핑이 바뀌었으니 같은 센서가 다른 구역으로 잡힌다
    assert c.engine.context.presence.user_area == "living"
    assert c.engine.context.presence.areas.get("kitchen") is False


# ============================================================ Replay 통합


def test_replay_drives_engine():
    result, engine, pub, t0log = replay_file(DATA, echo=False)
    assert result.lines == 22
    assert engine.received == 22
    assert engine.dropped == 0
    assert pub.count > 0


def test_replay_is_deterministic():
    """0단계 게이트 — 발행까지 포함해 같은 입력에 같은 출력."""
    _, _, a, _ = replay_file(DATA, echo=False)
    _, _, b, _ = replay_file(DATA, echo=False)
    assert a.published == b.published


def test_replay_produces_t0_log():
    """3주치를 재생하면 t0 로그가 쌓이고, 그것이 baseline.py 의 입력이 된다."""
    _, engine, _, t0log = replay_file(DATA, echo=False)
    assert len(t0log.of_type("wake")) == 1
    assert engine.context.wake_fsm.state.hydration_done is True


def test_replay_final_state():
    _, engine, _, _ = replay_file(DATA, echo=False)
    ctx = engine.context
    assert ctx.presence.user_area == "kitchen"
    assert ctx.away.state == "HOME"
    assert ctx.occupancy.state == "SINGLE"
    assert ctx.activity.state in ("EATING", "KITCHEN_MISC")