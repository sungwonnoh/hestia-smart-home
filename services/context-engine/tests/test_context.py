import json
from pathlib import Path

import pytest

from hestia_engine.clock import ReplayClock
from hestia_engine.config import load, load_default
from hestia_engine.context import (
    AwayContext,
    ContextEngine,
    OccupancyContext,
    PresenceContext,
    _combine,
)
from hestia_engine.messages import parse
from hestia_engine.timers import Scheduler
from hestia_engine.world import WorldState

HOME = """
name = "test"
areas = ["living", "kitchen", "bedroom", "entrance"]
roles = ["LIVING", "SLEEP", "MEAL", "ENTRY"]

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
id = "vs-03"
type = "presence"
area = "kitchen"
roles = ["MEAL"]
node = "esp32-2"

[[sensors]]
id = "vs-04"
type = "motion"
area = "kitchen"
roles = ["MEAL"]
node = "esp32-2"

[[sensors]]
id = "vs-05"
type = "presence"
area = "bedroom"
roles = ["SLEEP"]
node = "esp32-1"

[[sensors]]
id = "vs-06"
type = "door"
area = "entrance"
roles = ["ENTRY"]
node = "esp32-1"

[[sensors]]
id = "vs-07"
type = "motion"
area = "entrance"
roles = ["ENTRY"]
node = "esp32-1"
"""

POLICY = """
[power.default]
standby_max_w = 5
on_min_w = 50
on_exit_w = 30
min_hold_sec = 10

[presence]
mmwave_absent_grace_sec = 30
pir_absent_timeout_sec = 300
area_switch_min_sec = 20

[presence.energy]
still_max = 10
active_min = 30

[presence.confidence]
method = "max"
mmwave_active = 0.95
mmwave_still = 0.85
bed = 0.80
pir_recent = 0.60
pir_stale = 0.35
runner_up_penalty = 0.5

[away]
door_exit_quiet_sec = 600
door_exit_window_sec = 1800
no_motion_unknown_sec = 3600

[occupancy]
multi_min_sec = 120
multi_min_confidence = 0.80
single_restore_sec = 300
"""

T0 = 1_000_000.0


class Ctx:
    """clock / world / context 를 한 벌로 묶은 테스트 하네스."""

    def __init__(self, tmp_path):
        h = tmp_path / "home.toml"
        p = tmp_path / "policy.toml"
        h.write_text(HOME, encoding="utf-8")
        p.write_text(POLICY, encoding="utf-8")
        self.config = load(h, p)
        self.clock = ReplayClock(T0)
        self.sched = Scheduler(self.clock)
        self.world = WorldState(self.clock, self.config, self.sched)
        self.engine = ContextEngine(self.clock, self.config, self.world, self.sched)

    def at(self, ts: float):
        """시계를 옮기고 그 사이 타이머를 발화시킨다."""
        while (due := self.sched.next_due()) is not None and due <= ts:
            self.sched.run_due(until=due)
        self.clock.advance_to(ts)
        return self

    def feed(self, topic: str, payload: dict):
        msg = parse(topic, json.dumps(payload), self.clock.now())
        assert msg is not None, f"파싱 실패: {topic}"
        self.world.apply(msg)
        return self.engine.recompute()

    # ---- 메시지 헬퍼

    def presence(self, vid: str, present: bool, energy: int = 50):
        return self.feed(f"hestia/sensor/{vid}/state", {
            "version": 1, "sent_ts": 0, "src_id": vid, "seq": 1,
            "type": "presence", "present": present, "confidence": "high",
            "energy": energy, "distance_cm": 150,
        })

    def motion(self, vid: str, motion: bool = True):
        return self.feed(f"hestia/sensor/{vid}/state", {
            "version": 1, "sent_ts": 0, "src_id": vid, "seq": 1,
            "type": "motion", "motion": motion, "confidence": "low",
        })

    def bed(self, vid: str, occupied: bool):
        return self.feed(f"hestia/sensor/{vid}/state", {
            "version": 1, "sent_ts": 0, "src_id": vid, "seq": 1,
            "type": "bed", "occupied": occupied,
        })

    def door(self, vid: str, is_open: bool):
        return self.feed(f"hestia/sensor/{vid}/state", {
            "version": 1, "sent_ts": 0, "src_id": vid, "seq": 1,
            "type": "door", "open": is_open,
        })

    def node(self, nid: str, online: bool):
        return self.feed(f"hestia/node/{nid}/status", {
            "version": 1, "sent_ts": 0, "src_id": nid,
            "online": online, "ts_synced": True,
        })

    # ---- 조회

    @property
    def p(self) -> PresenceContext:
        return self.engine.presence

    @property
    def a(self) -> AwayContext:
        return self.engine.away

    @property
    def o(self) -> OccupancyContext:
        return self.engine.occupancy


@pytest.fixture
def c(tmp_path):
    return Ctx(tmp_path)


# ============================================================ presence


def test_mmwave_present_makes_area_occupied(c):
    c.presence("vs-03", True)
    assert c.p.areas["kitchen"] is True
    assert c.p.user_area == "kitchen"
    assert c.p.confidence == pytest.approx(0.95)


def test_mmwave_absent_after_grace(c):
    """present=false 를 즉시 믿되 30초 유예. 노드 재부팅 한 건을 거른다."""
    c.presence("vs-03", True)
    c.at(T0 + 100).presence("vs-03", False)
    assert c.p.areas["kitchen"] is True          # 아직 유예 중

    c.at(T0 + 131).engine.recompute()
    assert c.p.areas["kitchen"] is False


def test_still_energy_lowers_score(c):
    """정지 중이어도 재실이지만 점수가 낮다."""
    c.presence("vs-03", True, energy=5)
    assert c.p.areas["kitchen"] is True
    assert c.p.confidence == pytest.approx(0.85)


def test_bed_is_evidence(c):
    c.bed("vs-02", True)
    assert c.p.areas["living"] is True
    assert c.p.user_area == "living"
    assert c.p.confidence == pytest.approx(0.80)


def test_pir_decays_then_times_out(c):
    """PIR 은 마지막 true 이후 경과로 판단한다. false 를 믿지 않는다."""
    c.motion("vs-04", True)
    assert c.p.areas["kitchen"] is True
    assert c.p.confidence == pytest.approx(0.60)

    c.at(T0 + 200).engine.recompute()            # 150 < 200 < 300
    assert c.p.areas["kitchen"] is True
    assert c.p.confidence == pytest.approx(0.35)  # pir_stale

    c.at(T0 + 301).engine.recompute()
    assert c.p.areas["kitchen"] is False


def test_pir_false_does_not_clear_immediately(c):
    """가만히 서 있으면 PIR 반응이 멎는다. 그것으로 부재를 판정하지 않는다."""
    c.motion("vs-04", True)
    c.at(T0 + 10).motion("vs-04", False)
    assert c.p.areas["kitchen"] is True


def test_runner_up_penalty(c):
    """두 구역이 비슷하게 확실하면 '어디 있는지' 자체가 불확실하다."""
    c.presence("vs-03", True)                    # kitchen 0.95
    c.at(T0 + 100).presence("vs-01", True)       # living 0.95
    c.at(T0 + 200).engine.recompute()            # 전환 억제 해제 후
    assert c.p.confidence == pytest.approx(0.95 * 0.5)


def test_area_switch_is_held(c):
    """지나가는 동안 user_area 가 왕복하면 안 된다."""
    c.presence("vs-03", True)
    assert c.p.user_area == "kitchen"

    c.at(T0 + 100).presence("vs-01", True)       # living 도 켜짐
    assert c.p.user_area == "kitchen"            # 20초 안 됐으니 유지
    assert c.p.areas["living"] is True           # areas 는 반영


def test_area_switch_completes_after_hold(c):
    c.presence("vs-03", True)
    c.at(T0 + 100).presence("vs-01", True)
    c.at(T0 + 130).presence("vs-03", False)      # kitchen 이 꺼지면
    c.at(T0 + 161).engine.recompute()
    assert c.p.user_area == "living"


def test_switch_not_held_when_previous_area_empty(c):
    """이전 구역이 완전히 비었으면 붙들지 않는다."""
    c.presence("vs-03", True)
    c.at(T0 + 100).presence("vs-03", False)
    c.at(T0 + 131).presence("vs-01", True)
    assert c.p.user_area == "living"


def test_since_survives_unchanged_recompute(c):
    c.presence("vs-03", True)
    first_since = c.p.since
    c.at(T0 + 300).presence("vs-03", True, energy=41)
    assert c.p.since == first_since


def test_offline_node_sensors_excluded(c):
    """센서가 죽은 것을 재실로 오인하면 안 된다."""
    c.presence("vs-03", True)
    assert c.p.areas["kitchen"] is True
    c.node("esp32-2", False)
    assert c.p.areas["kitchen"] is False


def test_factors_always_filled(c):
    """명세: 프로파일과 무관하게 항상 채운다. 시연에서 화면이 비지 않도록."""
    c.presence("vs-03", False)
    assert c.p.factors["vs-03_present"] is False
    assert "vs-03_energy" in c.p.factors


def test_no_evidence_means_no_user_area(c):
    c.presence("vs-03", False)
    c.at(T0 + 31).engine.recompute()
    assert c.p.user_area is None
    assert not any(c.p.areas.values())


# ============================================================ away


def test_home_when_any_area_occupied(c):
    c.presence("vs-03", True)
    assert c.a.state == "HOME"


def test_sensor_fault_takes_priority(c):
    """센서가 죽으면 재실 판단 자체를 믿을 수 없다. 부재로 오인하면
    낙상·장기 무활동을 놓친다."""
    c.presence("vs-03", True)
    c.node("esp32-2", False)
    assert c.a.state == "SENSOR_FAULT"
    assert c.a.evidence == "lwt_offline"


def test_away_after_door_exit_and_quiet(c):
    c.presence("vs-03", True)
    c.at(T0 + 100).presence("vs-03", False)
    c.at(T0 + 110).door("vs-06", True)
    c.at(T0 + 115).door("vs-06", False)
    c.at(T0 + 800).engine.recompute()            # 조용해진 지 700초 > 600
    assert c.a.state == "AWAY"
    assert c.a.evidence == "door_exit"


def test_away_since_is_event_time_not_decision_time(c):
    """판단이 내려진 시각이 아니라 사건이 일어난 시각.

    우리가 10분 기다린 것뿐이고, 사람이 나간 것은 문이 닫힌 때다.
    """
    c.presence("vs-03", True)
    c.at(T0 + 100).presence("vs-03", False)
    c.at(T0 + 115).door("vs-06", False)
    c.at(T0 + 800).engine.recompute()
    assert c.a.state == "AWAY"
    assert c.a.since == T0 + 115                 # 800 이 아니다


def test_unknown_when_quiet_without_door_event(c):
    """문은 안 열렸는데 조용하다 — 자고 있을 수도 쓰러졌을 수도 있다.
    성급히 AWAY 로 가면 안전 시나리오가 전부 억제된다."""
    c.presence("vs-03", True)
    c.at(T0 + 100).presence("vs-03", False)
    c.at(T0 + 3800).engine.recompute()           # 3700초 > 3600
    assert c.a.state == "UNKNOWN"
    assert c.a.evidence == "no_motion"


def test_holds_previous_while_not_quiet_enough(c):
    c.presence("vs-03", True)
    c.at(T0 + 100).presence("vs-03", False)
    c.at(T0 + 200).engine.recompute()            # 아직 100초
    assert c.a.state == "HOME"


def test_stale_door_event_is_ignored(c):
    """창(30분) 밖의 현관 이벤트는 door_exit 근거가 되지 못한다."""
    c.door("vs-06", False)
    c.at(T0 + 2000).presence("vs-03", True)
    c.at(T0 + 2100).presence("vs-03", False)
    c.at(T0 + 2800).engine.recompute()
    assert c.a.state != "AWAY"


def test_away_recovers_to_home(c):
    c.presence("vs-03", True)
    c.at(T0 + 100).presence("vs-03", False)
    c.at(T0 + 110).door("vs-06", True)
    c.at(T0 + 115).door("vs-06", False)
    c.at(T0 + 800).engine.recompute()
    assert c.a.state == "AWAY"
    c.at(T0 + 900).presence("vs-03", True)
    assert c.a.state == "HOME"


# ============================================================ occupancy


def test_single_with_one_area(c):
    c.presence("vs-03", True)
    assert c.o.state == "SINGLE"


def test_two_strong_areas_need_duration(c):
    c.presence("vs-03", True)
    c.presence("vs-01", True)
    assert c.o.state == "SINGLE"                 # 아직 0초

    c.at(T0 + 121).engine.recompute()
    assert c.o.state == "MULTI"


def test_multi_since_is_concurrency_start(c):
    c.presence("vs-03", True)
    c.at(T0 + 50).presence("vs-01", True)        # 동시 재실 시작은 여기
    c.at(T0 + 171).engine.recompute()
    assert c.o.state == "MULTI"
    assert c.o.since == T0 + 50


def test_pir_alone_cannot_make_multi(c):
    """PIR 최대 0.6 < multi_min_confidence 0.8.
    다용도실 PIR 이 반응했다고 '두 번째 사람'으로 보면 안 된다."""
    c.presence("vs-03", True)
    c.motion("vs-07", True)                      # entrance PIR
    c.at(T0 + 200).engine.recompute()
    assert c.o.state == "SINGLE"


def test_bed_counts_as_strong(c):
    c.presence("vs-03", True)
    c.bed("vs-02", True)                         # 0.80 >= 0.80
    c.at(T0 + 121).engine.recompute()
    assert c.o.state == "MULTI"


def test_unknown_when_away(c):
    c.presence("vs-03", True)
    c.at(T0 + 100).presence("vs-03", False)
    c.at(T0 + 110).door("vs-06", True)
    c.at(T0 + 115).door("vs-06", False)
    c.at(T0 + 800).engine.recompute()
    assert c.a.state == "AWAY"
    assert c.o.state == "UNKNOWN"


def test_multi_clears_when_one_area_empties(c):
    c.presence("vs-03", True)
    c.presence("vs-01", True)
    c.at(T0 + 121).engine.recompute()
    assert c.o.state == "MULTI"

    c.at(T0 + 200).presence("vs-01", False)
    c.at(T0 + 231).engine.recompute()
    assert c.o.state == "SINGLE"


# ============================================================ 타이머


def test_timer_is_armed_for_pir_timeout(c):
    c.motion("vs-04", True)
    assert c.sched.next_due() == T0 + 300        # 근거 시각 + 300


def test_timer_uses_evidence_time_not_now(c):
    """now + N 으로 잡으면 매번 미래로 밀려 영원히 끝나지 않는다."""
    c.motion("vs-04", True)
    c.at(T0 + 100).engine.recompute()            # 100초 뒤 재계산
    assert c.sched.next_due() == T0 + 300        # 400 이 아니다


def test_timer_does_not_rearm_after_firing(c):
    """타이머가 자기를 무한히 다시 걸면 Replay 가 멈춘다."""
    c.motion("vs-04", True)
    c.at(T0 + 301).engine.recompute()
    assert f"ctx-pir-vs-04" not in c.sched.keys()


def test_timer_transition_without_message(c):
    """메시지가 안 와도 시간 경과만으로 전이가 일어난다."""
    c.motion("vs-04", True)
    assert c.p.areas["kitchen"] is True

    c.clock.advance_to(T0 + 301)
    c.sched.run_due()                            # 타이머가 recompute 를 부른다
    assert c.p.areas["kitchen"] is False


def test_no_runaway_timers(c):
    """3일치를 돌려도 타이머가 폭주하지 않는다."""
    c.motion("vs-04", True)
    fired = 0
    for _ in range(100):
        due = c.sched.next_due()
        if due is None:
            break
        c.clock.advance_to(due)
        fired += c.sched.run_due()
    assert fired < 10

def test_timer_change_reaches_callback(tmp_path):
    """시간 경과로만 일어나는 전이도 발행 경로를 탄다."""
    got: list[str] = []
    c = Ctx(tmp_path)
    c.engine._on_change = lambda cs: got.extend(x.name for x in cs)

    c.motion("vs-04", True)
    assert c.p.areas["kitchen"] is True

    c.clock.advance_to(T0 + 301)
    c.sched.run_due()
    assert "presence" in got


# ============================================================ 통합


def test_recompute_returns_only_changed(c):
    changed = c.presence("vs-03", True)
    names = {ctx.name for ctx in changed}
    assert names == {"presence", "away", "occupancy", "activity", "suppression"}


def test_second_recompute_is_empty(c):
    """몇 번 불려도 안전해야 한다. 타이머와 메시지가 거의 동시에 와도
    중복 발행되지 않는 근거."""
    c.presence("vs-03", True)
    assert c.engine.recompute() == ()


def test_energy_jitter_does_not_republish(c):
    """factors 가 흔들릴 때마다 발행하면 의미가 없다."""
    c.presence("vs-03", True, energy=38)
    changed = c.at(T0 + 60).presence("vs-03", True, energy=41)
    assert changed == ()


def test_all_contexts_for_periodic_publish(c):
    c.presence("vs-03", True)
    assert len(c.engine.all_contexts()) == 5


def test_payload_shape(c):
    c.presence("vs-03", True)
    p = c.p.payload(c.clock.now())
    assert set(p) == {"user_area", "since", "areas", "confidence", "factors"}
    assert p["user_area"] == "kitchen"


def test_combine_methods():
    assert _combine([], "max") == 0.0
    assert _combine([0.6, 0.9], "max") == 0.9
    assert _combine([0.6, 0.6], "noisy_or") == pytest.approx(0.84)


# ============================================================ 실제 시나리오


def test_morning_scenario_tracks_user():
    """기상 → 화장실 → 주방 이동을 따라가는가."""
    from hestia_engine.replay import Replay

    clock = ReplayClock(0)
    cfg = load_default()
    sched = Scheduler(clock)
    world = WorldState(clock, cfg, sched)
    seen: list[str | None] = []

    def collect(changed):
        for ctx in changed:
            if isinstance(ctx, PresenceContext):
                seen.append(ctx.user_area)

    engine = ContextEngine(clock, cfg, world, sched, on_change=collect)

    class Feed:
        def ingest(self, topic, payload):
            msg = parse(topic, payload, clock.now())
            if msg:
                world.apply(msg)
                collect(engine.recompute())

    path = Path(__file__).parent / "data" / "morning.jsonl"
    Replay(clock, sched, Feed()).run(path)

    assert "bedroom" in seen
    assert "bathroom" in seen
    assert "kitchen" in seen
    assert seen.index("bedroom") < seen.index("kitchen")
    assert engine.presence.user_area == "kitchen"
    assert engine.away.state == "HOME"
    assert engine.occupancy.state == "SINGLE"


# ============================================================ suppression


def test_no_suppression_by_default(c):
    c.presence("vs-03", True)
    assert c.engine.suppression.active is False
    assert c.engine.suppression.reason is None
    assert c.engine.allows("WAKE_ROUTINE") is True


def test_away_suppresses(c):
    """외출 중에는 집 안 채널로 보내도 무의미하다."""
    c.presence("vs-03", True)
    c.at(T0 + 100).presence("vs-03", False)
    c.at(T0 + 110).door("vs-06", True)
    c.at(T0 + 115).door("vs-06", False)
    c.at(T0 + 800).engine.recompute()

    assert c.a.state == "AWAY"
    assert c.engine.suppression.reason == "AWAY"
    assert c.engine.allows("WAKE_ROUTINE") is False


def test_safety_pierces_any_suppression(c):
    """명세: 안전 시나리오는 어떤 억제도 뚫는다."""
    c.presence("vs-03", True)
    c.at(T0 + 100).presence("vs-03", False)
    c.at(T0 + 110).door("vs-06", True)
    c.at(T0 + 115).door("vs-06", False)
    c.at(T0 + 800).engine.recompute()

    assert c.engine.suppression.active is True
    assert c.engine.allows("SAFETY") is True


def test_multi_suppresses(c):
    """MULTI 판정 시 개인화 알림 중단."""
    c.presence("vs-03", True)
    c.presence("vs-01", True)
    c.at(T0 + 121).engine.recompute()

    assert c.o.state == "MULTI"
    assert c.engine.suppression.reason == "MULTI"
    assert c.engine.allows("MEDICATION_PROMPT") is False
    assert c.engine.allows("SAFETY") is True


def test_cooldown_after_notification(c):
    c.presence("vs-03", True)
    assert c.engine.allows("WAKE_ROUTINE") is True

    c.engine.note_notification()
    c.at(T0 + 60).engine.recompute()
    assert c.engine.suppression.reason == "COOLDOWN"
    assert c.engine.allows("WAKE_ROUTINE") is False


def test_cooldown_expires(c):
    c.presence("vs-03", True)
    c.engine.note_notification()
    c.at(T0 + 60).engine.recompute()
    assert c.engine.suppression.active is True

    c.at(T0 + 1900).engine.recompute()             # cooldown_sec 1800 경과
    assert c.engine.suppression.active is False


def test_cooldown_has_until(c):
    """시한부 억제는 until 이 있다. AWAY/MULTI 는 조건부라 None."""
    c.presence("vs-03", True)
    c.engine.note_notification()
    c.at(T0 + 60).engine.recompute()
    assert c.engine.suppression.until == T0 + 1800


def test_away_outranks_cooldown(c):
    """우선순위 — 더 강한 억제가 이긴다."""
    c.presence("vs-03", True)
    c.engine.note_notification()
    c.at(T0 + 100).presence("vs-03", False)
    c.at(T0 + 115).door("vs-06", False)
    c.at(T0 + 800).engine.recompute()
    assert c.engine.suppression.reason == "AWAY"


def test_probe_outranks_all(c):
    c.presence("vs-03", True)
    c.engine._suppression_eval.start_probe(stage=2, duration_sec=300)
    c.at(T0 + 60).engine.recompute()

    s = c.engine.suppression
    assert s.reason == "SLEEP_PROBE"
    assert s.stage == 2
    assert "SLEEP_ROUTINE" in s.except_          # 프로브 자신은 예외
    assert c.engine.allows("SLEEP_ROUTINE") is True
    assert c.engine.allows("WAKE_ROUTINE") is False


def test_probe_ends(c):
    c.presence("vs-03", True)
    c.engine._suppression_eval.start_probe(stage=1, duration_sec=300)
    c.at(T0 + 60).engine.recompute()
    assert c.engine.suppression.active is True

    c.engine._suppression_eval.end_probe()
    c.at(T0 + 120).engine.recompute()
    assert c.engine.suppression.active is False


def test_suppression_payload_shape(c):
    c.presence("vs-03", True)
    p = c.engine.suppression.payload(c.clock.now())
    assert set(p) == {"active", "reason", "stage", "until", "except"}


def test_suppression_timer_releases_cooldown(c):
    """시한부 억제는 타이머로 풀린다 — 메시지가 없어도."""
    c.presence("vs-03", True)
    c.engine.note_notification()
    c.at(T0 + 60).engine.recompute()
    assert c.engine.suppression.active is True

    c.clock.advance_to(T0 + 1900)
    c.sched.run_due()
    assert c.engine.suppression.active is False