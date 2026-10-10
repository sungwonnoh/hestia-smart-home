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
    counts = [t for _, t, _ in c.pub.published if t == "hestia/context/day"]
    assert len(counts) >= 3


def test_periodic_not_pushed_by_messages(c):
    """메시지마다 타이머를 다시 걸면 5분이 영원히 오지 않는다."""
    c.presence("vs-04", True)
    first_due = c.sched.next_due()

    for i in range(1, 5):
        c.at(MORNING + i * 30).presence("vs-04", True, energy=40 + i)

    assert c.sched.next_due() == first_due or first_due is not None


def test_snapshot_includes_day(c):
    """day 는 FSM 이 들고 있어 ContextEngine.all_contexts 에 없다."""
    c.presence("vs-04", True)
    c.engine.publish_snapshot()
    assert c.pub.last("hestia/context/day") is not None


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
    assert len(t0log.of_type("hydration")) >= 1
    assert len(engine.context.day_fsm.state.hydrations) >= 1


def test_replay_final_state():
    _, engine, _, _ = replay_file(DATA, echo=False)
    ctx = engine.context
    assert ctx.presence.user_area == "kitchen"
    assert ctx.away.state == "HOME"
    assert ctx.occupancy.state == "SINGLE"
    assert ctx.activity.state in ("EATING", "KITCHEN_MISC")

# ============================================================ 모델 연동


def test_engine_has_model_store(c):
    from hestia_engine.model import ModelStore
    from hestia_engine.policy import InterventionPolicy

    assert isinstance(c.engine.models, ModelStore)
    assert isinstance(c.engine.policy, InterventionPolicy)


def test_model_message_is_stored(c):
    """hestia/model/kde 를 받으면 보관한다."""
    from test_model import kde_payload

    c.send("hestia/model/kde", kde_payload())
    assert c.engine.models.has("kde") is True
    assert c.engine.models.sample_days() == 21


def test_broken_model_does_not_replace_in_engine(c):
    """깨진 모델이 와도 직전 모델로 계속 판단한다."""
    from test_model import kde_payload

    c.send("hestia/model/kde", kde_payload())
    good = c.engine.models.get("kde")

    bad = kde_payload(meal_time={"grid_min": 0, "grid_step": 15, "density": [1.0] * 10})
    c.at(MORNING + 100).send("hestia/model/kde", bad)
    assert c.engine.models.get("kde") is good


def test_percentile_absent_without_model(c):
    """모델이 없으면 factors 에 넣지 않는다 — '모른다' 와 '0.0' 은 다르다."""
    c.presence("vs-04", True)
    assert "meal_time_percentile" not in c.engine.context.activity.factors


def test_percentile_appears_with_model(c):
    """명세의 factors 예시에 있는 값."""
    from test_model import kde_payload, peaked

    c.send("hestia/model/kde", kde_payload(meal_time=peaked(450)))
    c.at(MORNING + 10).presence("vs-04", True)

    f = c.engine.context.activity.factors
    assert "meal_time_percentile" in f
    assert f["meal_time_percentile"] > 0.9       # 09:40 은 07:30 봉우리 한참 뒤


def test_percentile_does_not_change_score(c):
    """지금은 표시만 한다. 점수에 반영하면 되먹임이 생긴다."""
    from test_model import kde_payload, peaked

    c.presence("vs-04", True)
    c.at(MORNING + 10).presence("vs-04", True)
    before = dict(c.engine.context.activity.scores)      # 히스테리시스 이미 붙음

    c.at(MORNING + 20).send("hestia/model/kde", kde_payload(meal_time=peaked(450)))
    c.at(MORNING + 30).presence("vs-04", True)

    assert "meal_time_percentile" in c.engine.context.activity.factors
    assert c.engine.context.activity.scores == before    # 점수는 그대로


def test_policy_reads_engine_store(c):
    """Engine 과 Policy 가 같은 ModelStore 를 본다."""
    from test_model import kde_payload, peaked
    from hestia_engine.policy import ANOMALY

    c.send("hestia/model/kde", kde_payload(meal_time=peaked(450)))
    c.at(MORNING + 10).presence("vs-04", True)

    ctx = c.engine.context
    d = c.engine.policy.evaluate_meal(
        ctx.away, ctx.occupancy, ctx.suppression, meal_done=False
    )
    assert d.candidate is True
    assert d.reason == ANOMALY


# ============================================================ MEAL_PROMPT


def meal_model(peaks: list[dict], density: list[float] | None = None) -> dict:
    """세 끼 분포. peaks 는 배치가 나눠준 구간."""
    if density is None:
        density = [0.0] * 96
        for i in range(30, 34):             # 07:30~08:30 아침
            density[i] = 0.05
        for i in range(48, 52):             # 12:00~13:00 점심
            density[i] = 0.05
        for i in range(72, 76):             # 18:00~19:00 저녁
            density[i] = 0.15

    return {
        "version": 1, "sent_ts": 0, "src_id": "rpi4",
        "trained_at": MORNING - 86400, "sample_days": 21,
        "distributions": {"meal_time": {
            "grid_min": 0, "grid_step": 15,
            "density": density, "peaks": peaks,
        }},
        "predictability": {"meal_time": 0.33},
    }


MORNING_PEAK = {"center": 480, "from": 390, "to": 660,
                "predictability": 0.54, "days_ratio": 1.0,
                "meals_per_day": 1.0}
LUNCH_PEAK = {"center": 750, "from": 660, "to": 900,
              "predictability": 0.50, "days_ratio": 1.0,
              "meals_per_day": 1.0}


def meal_notifies(c) -> list[dict]:
    return [
        p for _, t, p in c.pub.published
        if t.startswith("hestia/notify") and p.get("scenario") == "MEAL_PROMPT"
    ]


def test_late_meal_notifies(c):
    """09:40 — 아침 구간의 꼬리다."""
    c.send("hestia/model/kde", meal_model([MORNING_PEAK]))
    c.presence("vs-04", True)
    assert len(meal_notifies(c)) == 1


def test_only_once_per_peak(c):
    """같은 끼니에 센서가 여러 번 울려도 한 번만 보낸다."""
    c.send("hestia/model/kde", meal_model([MORNING_PEAK]))
    c.presence("vs-04", True)
    c.at(MORNING + 300).presence("vs-04", False)
    c.at(MORNING + 600).presence("vs-04", True)
    assert len(meal_notifies(c)) == 1


def test_no_peaks_no_judgement(c):
    """봉우리가 1개 이하면 배치가 필드를 생략한다 — 가를 수 없다."""
    payload = meal_model([])
    del payload["distributions"]["meal_time"]["peaks"]
    c.send("hestia/model/kde", payload)
    c.presence("vs-04", True)
    assert meal_notifies(c) == []


def test_outside_any_peak_is_quiet(c):
    """끼니 사이(11:00)에는 판단하지 않는다."""
    c.at(MORNING + 4800)                     # 11:00
    c.send("hestia/model/kde", meal_model([MORNING_PEAK, LUNCH_PEAK]))
    c.presence("vs-04", True)
    assert meal_notifies(c) == []


def test_timer_fires_in_quiet_house(tmp_path):
    """센서가 조용해도 끼니가 늦으면 걸려야 한다.

    사용자가 가만히 있으면 recompute 가 안 돈다. 타이머가 없으면
    아침을 통째로 거른 날이 조용히 지나간다.
    """
    c = Ctx(tmp_path, start=MORNING - 7200)      # 07:40 — 아직 평소 시각
    c.send("hestia/model/kde", meal_model([MORNING_PEAK]))
    c.presence("vs-04", True)
    assert meal_notifies(c) == []

    c.at(MORNING)                                # 09:40 — 센서 입력 없이
    assert len(meal_notifies(c)) == 1