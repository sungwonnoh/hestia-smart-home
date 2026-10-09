import json

import pytest

from hestia_engine.clock import ReplayClock
from hestia_engine.config import load
from hestia_engine.engine import Engine, RecordingPublisher
from hestia_engine.fsm import MemoryT0Log
from hestia_engine.messages import parse
from hestia_engine.timers import Scheduler
from hestia_engine.world import WorldState

from test_activity import HOME, POLICY, MORNING, NIGHT

STILL_LIMIT = 900        # policy.toml 의 scenarios.sleep.still_sec
PROBE_SEC = 60           # probe_timeout_sec
SUSTAIN = 60             # end_sustain_sec — 줄이면 여기도


class Ctx:
    def __init__(self, tmp_path, start: float = NIGHT):
        h = tmp_path / "home.toml"
        p = tmp_path / "policy.toml"
        h.write_text(HOME, encoding="utf-8")
        p.write_text(POLICY, encoding="utf-8")
        self.config = load(h, p)
        self.clock = ReplayClock(start)
        self.sched = Scheduler(self.clock)
        self.world = WorldState(self.clock, self.config, self.sched)
        self.pub = RecordingPublisher()
        self.t0 = MemoryT0Log()
        self.engine = Engine(
            self.clock, self.config, self.world, self.sched, self.pub,
            t0log=self.t0,
        )

    def at(self, ts: float):
        while (due := self.sched.next_due()) is not None and due <= ts:
            self.sched.run_due(until=due)
        self.clock.advance_to(ts)
        return self

    def send(self, topic: str, payload: dict):
        self.engine.ingest(topic, json.dumps(payload))
        return self

    # ---- 센서

    def presence(self, vid: str, *, energy: int = 45, seq: int = 1,
                 present: bool = True):
        return self.send(f"hestia/sensor/{vid}/state", {
            "version": 1, "sent_ts": 0, "src_id": vid, "seq": seq,
            "type": "presence", "present": present, "confidence": "high",
            "energy": energy if present else 0, "distance_cm": 150,
        })

    def bed(self, vid: str, occupied: bool, seq: int = 1):
        return self.send(f"hestia/sensor/{vid}/state", {
            "version": 1, "sent_ts": 0, "src_id": vid, "seq": seq,
            "type": "bed", "occupied": occupied,
        })

    # ---- 가전

    def device(self, vid: str, device_type: str, **fields):
        body = {
            "version": 1, "sent_ts": 0, "src_id": vid, "seq": 1,
            "device_type": device_type, "source": "mock", **fields,
        }
        return self.send(f"hestia/device/{vid}/state", body)

    def light(self, vid: str, brightness: int = 100, power: str = "ON",
              seq: int = 1):
        return self.send(f"hestia/device/{vid}/state", {
            "version": 1, "sent_ts": 0, "src_id": vid, "seq": seq,
            "device_type": "smart_light", "source": "mock",
            "power": power, "brightness": brightness,
        })

    # ---- 조회

    def of(self, topic: str) -> tuple[dict, ...]:
        return self.pub.of_topic(f"hestia/{topic}")

    def decisions(self) -> tuple[dict, ...]:
        return tuple(
            p for p in self.of("intervention/decision") if p["kind"] == "sleep"
        )

    def reasons(self) -> list[str]:
        return [p["reason"] for p in self.decisions()]

    def pushes(self) -> tuple[dict, ...]:
        return tuple(
            p for p in self.of("notify/push") if p["scenario"] == "SLEEP_ROUTINE"
        )

    def cmds(self) -> tuple[dict, ...]:
        return tuple(p for _, t, p in self.pub.published if t.endswith("/cmd"))

    def sleeps(self, type_: str) -> tuple:
        """sleep_start / sleep_end 로그."""
        return self.t0.of_type(type_)

    @property
    def runner(self):
        return self.engine.scenarios


@pytest.fixture
def c(tmp_path):
    return Ctx(tmp_path)


def sofa_still(c: Ctx, *, seq: int = 1):
    """소파 패드 + 거실 재실 → IN_SOFA_AWAKE.

    TV 가 꺼져 있으므로 프로브는 조명으로 간다. 그래서 조명 state 도
    넣어둔다 — 없으면 프로브 자체가 안 나간다.
    """
    c.device("vd-01", "smart_tv", power="OFF")
    c.light("vd-02", brightness=100)
    c.bed("vs-02", occupied=True)
    c.presence("vs-01", energy=3, seq=seq)
    return c


# ============================================================ 후보 판정


def test_no_probe_while_moving(c):
    """움직이는 중이면 떠보지 않는다."""
    sofa_still(c)
    c.presence("vs-01", energy=50, seq=2)
    c.engine.scenarios.tick(c.engine.context)
    assert c.pushes() == ()


def test_no_probe_before_limit(c):
    """15분이 차야 떠본다. 5분이면 TV 보다 잠깐 멈춘 것도 걸린다."""
    sofa_still(c)
    c.at(NIGHT + 600).engine.scenarios.tick(c.engine.context)
    assert c.pushes() == ()
    assert c.decisions() == ()


def test_timer_scheduled_while_still(c):
    """정지 중에는 임계에 닿는 시각을 예약한다 — 센서가 조용해도
    떠볼 시점이 와야 한다."""
    sofa_still(c)
    timers = c.engine.scenarios.tick(c.engine.context)
    assert "sleep-probe" in [k for k, _ in timers]


def test_probe_after_limit(c):
    """15분 정지하면 떠본다. 센서가 조용해도 타이머가 떠볼 시점을 만든다."""
    sofa_still(c)
    c.at(NIGHT + STILL_LIMIT + 10)

    assert c.runner._probe is not None
    assert c.cmds()[-1]["params"]["brightness"] == 70


def test_no_probe_in_other_states(c):
    """주방에서 멈춰 있는 것은 취침이 아니다."""
    c.presence("vs-04", energy=3)
    c.at(NIGHT + STILL_LIMIT + 10).presence("vs-04", energy=3, seq=2)
    assert c.pushes() == ()


# ============================================================ 배너 프로브


def test_banner_when_tv_on(c):
    """TV 가 켜져 있으면 배너로 묻는다."""
    c.device("vd-01", "smart_tv", power="ON")
    c.presence("vs-01", energy=3)
    c.at(NIGHT + STILL_LIMIT + 10).presence("vs-01", energy=3, seq=2)

    assert len(c.pushes()) == 1
    assert c.runner._probe.method == "banner"


def test_banner_does_not_require_ack(c):
    """requires_ack 을 켜면 에스컬레이션이 자는 사람을 음성으로 깨운다."""
    c.device("vd-01", "smart_tv", power="ON")
    c.presence("vs-01", energy=3)
    c.at(NIGHT + STILL_LIMIT + 10).presence("vs-01", energy=3, seq=2)

    assert c.pushes()[0]["requires_ack"] is False


def test_awake_when_acked(c):
    """[예] 를 누르면 깨어 있는 것이다."""
    c.device("vd-01", "smart_tv", power="ON")
    c.presence("vs-01", energy=3)
    c.at(NIGHT + STILL_LIMIT + 10).presence("vs-01", energy=3, seq=2)

    nid = c.pushes()[0]["notify_id"]
    c.engine.notifier.on_ack(nid, "SEEN", "vd-10")
    c.engine.scenarios.tick(c.engine.context)

    assert c.reasons() == ["AWAKE"]
    assert c.runner.asleep is None


# ============================================================ 조명 프로브


def test_dim_when_tv_off(c):
    """디스플레이는 TV 대행이라 TV 가 꺼져 있으면 배너를 띄울 근거가
    없다. 조명을 한 단계 내려 반응을 본다."""
    sofa_still(c)
    c.at(NIGHT + STILL_LIMIT + 10)

    assert c.pushes() == ()
    assert c.runner._probe.method == "dim"
    assert c.cmds()[-1]["params"]["brightness"] == 70


def test_dim_is_relative_to_base(c):
    """비율이라 어두운 조명도 내려간다. 40 → 28.
    야간등을 40으로 쓰는 집에 절대값 70을 쓰면 오히려 밝아진다."""
    sofa_still(c)
    c.light("vd-02", brightness=40, seq=2)
    c.at(NIGHT + STILL_LIMIT + 10).presence("vs-01", energy=3, seq=2)

    assert c.cmds()[-1]["params"]["brightness"] == 28
    assert c.runner._probe is not None


def test_dim_uses_transition(c):
    """한 번에 바뀌면 놀란다. 보간은 노드가 한다."""
    sofa_still(c)
    c.light("vd-02", brightness=100)
    c.at(NIGHT + STILL_LIMIT + 10).presence("vs-01", energy=3, seq=2)

    assert c.cmds()[-1]["params"]["transition_ms"] == 30000


def test_no_dim_when_light_off(c):
    sofa_still(c)
    c.light("vd-02", power="OFF", brightness=0, seq=2)
    c.at(NIGHT + STILL_LIMIT + 10).presence("vs-01", energy=3, seq=2)
    assert c.cmds() == ()


def test_awake_when_light_raised(c):
    """사용자가 밝기를 올렸다 — 깨어 있다."""
    sofa_still(c)
    c.at(NIGHT + STILL_LIMIT + 10)
    assert c.runner._probe is not None

    c.light("vd-02", brightness=100, seq=3)         # 되돌림
    assert c.reasons() == ["AWAKE"]



def test_no_restore_while_visible(c):
    """깬 뒤에도 밝기는 그대로 둔다.
    보는 앞에서 되돌리면 조명이 고장난 것처럼 읽힌다."""
    sofa_still(c)
    c.at(NIGHT + STILL_LIMIT + 10)
    c.light("vd-02", brightness=70, seq=3)
    before = len(c.cmds())

    c.at(NIGHT + STILL_LIMIT + 30).presence("vs-01", energy=50, seq=3)

    assert c.reasons() == ["AWAKE"]
    assert len(c.cmds()) == before


def test_restore_after_lights_off(c):
    """사용자가 불을 끈 뒤에 되돌린다. 꺼져 있어 보이지 않는다."""
    sofa_still(c)
    c.at(NIGHT + STILL_LIMIT + 10)
    c.light("vd-02", brightness=70, seq=3)
    c.at(NIGHT + STILL_LIMIT + 30).presence("vs-01", energy=50, seq=3)

    c.light("vd-02", power="OFF", brightness=0, seq=4)
    c.engine.scenarios.tick(c.engine.context)

    assert c.cmds()[-1]["params"]["brightness"] == 100


# ============================================================ 확정


def test_asleep_after_timeout(c):
    """무반응이 신호다. 깨어 있으면 누르거나 움직인다."""
    c.device("vd-01", "smart_tv", power="ON")
    c.presence("vs-01", energy=3)
    c.at(NIGHT + STILL_LIMIT + 10).presence("vs-01", energy=3, seq=2)

    c.at(NIGHT + STILL_LIMIT + 10 + PROBE_SEC + 5)
    c.sched.run_due()

    assert "ASLEEP_CONFIRMED" in c.reasons()
    assert c.runner.asleep is not None


def test_asleep_records_area(c):
    """어디서 잠들었나 — SLEEP_CONTROL 이 제어 대상을 정한다."""
    c.device("vd-01", "smart_tv", power="ON")
    c.presence("vs-01", energy=3)
    c.at(NIGHT + STILL_LIMIT + 10).presence("vs-01", energy=3, seq=2)
    c.at(NIGHT + STILL_LIMIT + 10 + PROBE_SEC + 5)
    c.sched.run_due()

    assert c.runner.asleep.area == "living"


def test_decision_payload_shape(c):
    c.device("vd-01", "smart_tv", power="ON")
    c.presence("vs-01", energy=3)
    c.at(NIGHT + STILL_LIMIT + 10).presence("vs-01", energy=3, seq=2)
    c.at(NIGHT + STILL_LIMIT + 10 + PROBE_SEC + 5)
    c.sched.run_due()

    d = c.decisions()[-1]
    assert d["kind"] == "sleep"
    assert d["candidate"] is True
    assert d["factors"]["method"] == "banner"
    assert d["factors"]["area"] == "living"
    assert d["factors"]["still_sec"] >= STILL_LIMIT


def test_no_reprobe_while_asleep(c):
    """확정된 뒤에는 깰 때까지 다시 묻지 않는다."""
    c.device("vd-01", "smart_tv", power="ON")
    c.presence("vs-01", energy=3)
    c.at(NIGHT + STILL_LIMIT + 10).presence("vs-01", energy=3, seq=2)
    c.at(NIGHT + STILL_LIMIT + 10 + PROBE_SEC + 5)
    c.sched.run_due()
    before = len(c.pushes())

    c.at(NIGHT + STILL_LIMIT * 3).presence("vs-01", energy=3, seq=3)
    assert len(c.pushes()) == before


def test_waking_clears_asleep(c):
    """명확한 움직임이 지속되면 깬 것이다. 뒤척임 한 번은 아니다."""
    c.device("vd-01", "smart_tv", power="ON")
    c.presence("vs-01", energy=3)
    c.at(NIGHT + STILL_LIMIT + 10).presence("vs-01", energy=3, seq=2)
    c.at(NIGHT + STILL_LIMIT + 10 + PROBE_SEC + 5)
    c.sched.run_due()
    assert c.runner.asleep is not None

    c.at(NIGHT + STILL_LIMIT + 200).presence("vs-01", energy=50, seq=3)
    assert c.runner.asleep is not None          # 아직 — 지속 확인 중

    c.at(NIGHT + STILL_LIMIT + 200 + SUSTAIN + 5).presence(
        "vs-01", energy=50, seq=4
    )
    assert c.runner.asleep is None


# ============================================================ 억제


def test_probe_suppresses_others(c):
    """프로브 중 다른 알림이 가면 사용자가 그것에 반응하고,
    프로브에도 반응한 것처럼 보여 판정이 망가진다."""
    c.device("vd-01", "smart_tv", power="ON")
    c.presence("vs-01", energy=3)
    c.at(NIGHT + STILL_LIMIT + 10).presence("vs-01", energy=3, seq=2)

    s = c.engine.context.suppression
    assert s.reason == "SLEEP_PROBE"
    assert c.engine.context.allows("WAKE_ROUTINE") is False
    assert c.engine.context.allows("SAFETY") is True


def test_probe_ends_after_timeout(c):
    c.device("vd-01", "smart_tv", power="ON")
    c.presence("vs-01", energy=3)
    c.at(NIGHT + STILL_LIMIT + 10).presence("vs-01", energy=3, seq=2)
    c.at(NIGHT + STILL_LIMIT + 10 + PROBE_SEC + 5)
    c.sched.run_due()

    assert c.engine.context.suppression.reason != "SLEEP_PROBE"


def test_probe_exempt_from_daily_limit(c):
    """떠보는 것은 개입이 아니라 관측이다. 하루 상한에 걸려 못
    보내면 수면 판정 자체가 안 된다."""
    for _ in range(20):
        c.engine.notifier.limits.note_sent()

    c.device("vd-01", "smart_tv", power="ON")
    c.presence("vs-01", energy=3)
    c.at(NIGHT + STILL_LIMIT + 10).presence("vs-01", energy=3, seq=2)

    assert len(c.pushes()) == 1


def test_probe_sets_no_cooldown(c):
    c.device("vd-01", "smart_tv", power="ON")
    c.presence("vs-01", energy=3)
    c.at(NIGHT + STILL_LIMIT + 10).presence("vs-01", energy=3, seq=2)

    assert "SLEEP_ROUTINE" not in c.engine.context.suppression.cooldowns


# ============================================================ t0 로그


def test_sleep_start_t0_is_still_since(c):
    """t0 는 확정 시각이 아니라 정지가 시작된 시각이다.
    확정 시각을 쓰면 프로브에 걸린 시간만큼 밀려 분포가 틀어진다."""
    c.device("vd-01", "smart_tv", power="ON")
    c.presence("vs-01", energy=3)
    still_began = c.clock.now()

    c.at(NIGHT + STILL_LIMIT + 10).presence("vs-01", energy=3, seq=2)
    c.at(NIGHT + STILL_LIMIT + 10 + PROBE_SEC + 5)
    c.sched.run_due()

    entries = c.sleeps("sleep_start")
    assert len(entries) == 1
    assert entries[0].t0 == still_began


def test_sleep_start_records_method(c):
    """어떻게 확정했나. 배치가 가중치로 쓴다."""
    c.device("vd-01", "smart_tv", power="ON")
    c.presence("vs-01", energy=3)
    c.at(NIGHT + STILL_LIMIT + 10).presence("vs-01", energy=3, seq=2)
    c.at(NIGHT + STILL_LIMIT + 10 + PROBE_SEC + 5)
    c.sched.run_due()

    e = c.sleeps("sleep_start")[0]
    assert e.method == "banner"
    assert e.confidence == 0.9
    assert e.area == "living"
    assert e.duration_sec == 0.0


def test_sleep_end_logged(c):
    """쌍으로 남긴다. duration 은 배치가 뺀다."""
    c.device("vd-01", "smart_tv", power="ON")
    c.presence("vs-01", energy=3)
    c.at(NIGHT + STILL_LIMIT + 10).presence("vs-01", energy=3, seq=2)
    c.at(NIGHT + STILL_LIMIT + 10 + PROBE_SEC + 5)
    c.sched.run_due()

    woke = NIGHT + STILL_LIMIT + 200
    c.at(woke).presence("vs-01", energy=50, seq=3)
    c.at(woke + SUSTAIN + 5).presence("vs-01", energy=50, seq=4)

    ends = c.sleeps("sleep_end")
    assert len(ends) == 1
    assert ends[0].t0 == woke


# ============================================================ 조명 2단계


def test_dim_second_step(c):
    """1단계에 무응답이면 한 단계 더 내린다. 100 → 70 → 30."""
    sofa_still(c)
    c.at(NIGHT + STILL_LIMIT + 10)
    assert c.cmds()[-1]["params"]["brightness"] == 70

    c.at(NIGHT + STILL_LIMIT + 10 + PROBE_SEC + 5)
    c.sched.run_due()

    assert c.cmds()[-1]["params"]["brightness"] == 30
    assert c.runner._probe is not None
    assert c.runner.asleep is None


def test_dim_second_step_is_from_base(c):
    """2단계는 1단계 결과가 아니라 기준 밝기 대비다.
    70 의 30% 인 21 이 아니라 100 의 30% 인 30."""
    sofa_still(c)
    c.at(NIGHT + STILL_LIMIT + 10)
    c.at(NIGHT + STILL_LIMIT + 10 + PROBE_SEC + 5)
    c.sched.run_due()

    assert c.cmds()[-1]["params"]["brightness"] == 30


def test_asleep_after_two_steps(c):
    """2단계까지 무응답이면 확정한다."""
    sofa_still(c)
    c.at(NIGHT + STILL_LIMIT + 10)
    c.at(NIGHT + STILL_LIMIT + 10 + PROBE_SEC + 5)
    c.sched.run_due()
    c.at(NIGHT + STILL_LIMIT + 10 + PROBE_SEC * 2 + 10)
    c.sched.run_due()

    assert c.runner.asleep is not None
    assert c.sleeps("sleep_start")[0].method == "dim"


def test_no_second_step_when_moving(c):
    """1단계에서 움직이면 더 내리지 않는다."""
    sofa_still(c)
    c.at(NIGHT + STILL_LIMIT + 10)
    before = len(c.cmds())

    c.at(NIGHT + STILL_LIMIT + 30).presence("vs-01", energy=50, seq=3)

    assert c.reasons() == ["AWAKE"]
    assert len(c.cmds()) == before


def test_probe_base_does_not_ratchet(c):
    """기준 밝기는 한 번만 읽는다. 매번 현재값을 읽으면
    내린 결과가 다음 기준이 되어 밝기가 복리로 줄어든다."""
    sofa_still(c)
    c.at(NIGHT + STILL_LIMIT + 10)
    c.light("vd-02", brightness=70, seq=3)
    c.at(NIGHT + STILL_LIMIT + 30).presence("vs-01", energy=50, seq=3)
    assert c.reasons() == ["AWAKE"]

    gap = 2700
    base = NIGHT + STILL_LIMIT + 30 + gap + 10
    c.at(base).presence("vs-01", energy=3, seq=4)
    c.at(base + STILL_LIMIT + 10).presence("vs-01", energy=3, seq=5)

    assert c.cmds()[-1]["params"]["brightness"] == 70   # 49 가 아니다


# ============================================================ 떠볼 수단 없음


def test_no_channel_waits_longer(c):
    """TV 도 조명도 꺼져 있으면 떠볼 수가 없다. 긴 정지로만 확정한다."""
    c.device("vd-01", "smart_tv", power="OFF")
    c.light("vd-02", power="OFF", brightness=0)
    c.bed("vs-02", occupied=True)
    c.presence("vs-01", energy=3)

    c.at(NIGHT + STILL_LIMIT + 10).presence("vs-01", energy=3, seq=2)
    assert c.runner.asleep is None              # 15분으로는 부족
    assert c.cmds() == ()

    c.at(NIGHT + 1800 + 10).presence("vs-01", energy=3, seq=3)
    assert c.runner.asleep is not None


def test_no_channel_confidence(c):
    """떠보지 못했으니 덜 확실하다."""
    c.device("vd-01", "smart_tv", power="OFF")
    c.light("vd-02", power="OFF", brightness=0)
    c.bed("vs-02", occupied=True)
    c.presence("vs-01", energy=3)
    c.at(NIGHT + 1800 + 10).presence("vs-01", energy=3, seq=2)

    e = c.sleeps("sleep_start")[0]
    assert e.method == "none"
    assert e.confidence == 0.6


# ============================================================ 재프로브


def test_gap_after_awake(c):
    """깨어 있다고 답한 직후에는 다시 묻지 않는다."""
    c.device("vd-01", "smart_tv", power="ON")
    c.presence("vs-01", energy=3)
    c.at(NIGHT + STILL_LIMIT + 10).presence("vs-01", energy=3, seq=2)

    nid = c.pushes()[0]["notify_id"]
    c.engine.notifier.on_ack(nid, "SEEN", "vd-10")
    c.engine.scenarios.tick(c.engine.context)
    before = len(c.pushes())

    awake_at = c.clock.now()
    c.at(awake_at + STILL_LIMIT + 10).presence("vs-01", energy=3, seq=3)
    assert len(c.pushes()) == before            # 45분이 아직 안 지났다

    c.at(awake_at + 2700 + STILL_LIMIT + 10).presence(
        "vs-01", energy=3, seq=4
    )
    assert len(c.pushes()) > before


def test_resleep_emits_decision(c):
    """화장실에 다녀와 다시 누우면 그것도 발행돼야 한다.
    sleep_end 가 판정을 비우지 않으면 두 번째 확정이 묻힌다."""
    c.device("vd-01", "smart_tv", power="ON")
    c.presence("vs-01", energy=3)
    c.at(NIGHT + STILL_LIMIT + 10).presence("vs-01", energy=3, seq=2)
    c.at(NIGHT + STILL_LIMIT + 10 + PROBE_SEC + 5)
    c.sched.run_due()
    assert c.reasons().count("ASLEEP_CONFIRMED") == 1

    woke = NIGHT + STILL_LIMIT + 200
    c.at(woke).presence("vs-01", energy=50, seq=3)
    c.at(woke + SUSTAIN + 5).presence("vs-01", energy=50, seq=4)
    assert "WOKE" in c.reasons()

    again = woke + SUSTAIN + 3600
    c.at(again).presence("vs-01", energy=3, seq=5)
    c.at(again + STILL_LIMIT + 10).presence("vs-01", energy=3, seq=6)
    c.at(again + STILL_LIMIT + 10 + PROBE_SEC + 5)
    c.sched.run_due()

    assert c.reasons().count("ASLEEP_CONFIRMED") == 2
    assert len(c.sleeps("sleep_start")) == 2


# ============================================================ 압력 패드


def test_bed_false_ends_sleep_at_once(c):
    """패드가 가장 확실하다. 지속을 기다리지 않는다."""
    c.device("vd-01", "smart_tv", power="ON")
    c.bed("vs-02", occupied=True)
    c.presence("vs-01", energy=3)
    c.at(NIGHT + STILL_LIMIT + 10).presence("vs-01", energy=3, seq=2)
    c.at(NIGHT + STILL_LIMIT + 10 + PROBE_SEC + 5)
    c.sched.run_due()
    assert c.runner.asleep is not None

    left = NIGHT + STILL_LIMIT + 300
    c.at(left).bed("vs-02", occupied=False, seq=2)

    assert c.runner.asleep is None
    assert c.sleeps("sleep_end")[0].t0 == left


def test_lights_off_on_confirm(c):
    """확정하면 끈다."""
    sofa_still(c)
    c.at(NIGHT + STILL_LIMIT + 10)
    c.at(NIGHT + STILL_LIMIT + 10 + PROBE_SEC + 5)
    c.sched.run_due()
    c.at(NIGHT + STILL_LIMIT + 10 + PROBE_SEC * 2 + 10)
    c.sched.run_due()

    assert c.cmds()[-1]["params"]["power"] == "OFF"


