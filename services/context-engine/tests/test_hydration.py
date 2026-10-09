import json

import pytest

from hestia_engine.clock import ReplayClock
from hestia_engine.config import load
from hestia_engine.engine import Engine, RecordingPublisher
from hestia_engine.timers import Scheduler
from hestia_engine.world import WorldState
from hestia_engine.scenarios import AsleepState

from test_activity import HOME as BASE_HOME, POLICY, MORNING, NIGHT

# 온습도와 현관문. HOME 에 없어서 여기서 더한다.
HOME = BASE_HOME + """
[[sensors]]
id = "vs-03c"
type = "climate"
area = "living"
roles = ["LIVING"]
node = "esp32-1"

[[sensors]]
id = "vs-07"
type = "door"
area = "entrance"
roles = ["ENTRY"]
node = "esp32-1"
"""

HOUR = 3600
GAP = 4 * HOUR              # scenarios.hydration.gap_sec
SHORT_GAP = 3 * HOUR        # short_gap_sec — 덥거나 건조할 때
SLEEP_GAP = 2 * HOUR        # sleep_end_gap_sec
RENOTIFY = 2 * HOUR
TO_CHECKPOINT = 37200       # 09:40 -> 20:00


class Ctx:
    def __init__(self, tmp_path, start: float = MORNING, policy: str = POLICY):
        h = tmp_path / "home.toml"
        p = tmp_path / "policy.toml"
        h.write_text(HOME, encoding="utf-8")
        p.write_text(policy, encoding="utf-8")
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

    def tick(self):
        self.engine.context.recompute()
        self.engine.scenarios.tick(self.engine.context)
        return self

    # ---- 상태

    def home(self):
        """거실 재실. 알림이 나갈 수 있는 최소 상태 — vd-10 이 채널이다."""
        return self.presence("vs-01", True)

    def presence(self, vid: str, present: bool, energy: int = 50, seq: int = 1):
        return self.send(f"hestia/sensor/{vid}/state", {
            "version": 1, "sent_ts": 0, "src_id": vid, "seq": seq,
            "type": "presence", "present": present, "confidence": "high",
            "energy": energy, "distance_cm": 150,
        })

    def door(self, open_: bool, seq: int = 1):
        return self.send("hestia/sensor/vs-07/state", {
            "version": 1, "sent_ts": 0, "src_id": "vs-07", "seq": seq,
            "type": "door", "open": open_,
        })

    def climate(self, temp: float = 22.0, humidity: float = 50.0, seq: int = 1):
        return self.send("hestia/sensor/vs-03c/state", {
            "version": 1, "sent_ts": 0, "src_id": "vs-03c", "seq": seq,
            "type": "climate", "temperature_c": temp, "humidity_pct": humidity,
        })

    def dispensed(self, amount_ml: int = 200, seq: int = 1):
        return self.send("hestia/device/vd-06/event", {
            "version": 1, "sent_ts": 0, "src_id": "vd-06", "seq": seq,
            "device_type": "water_purifier", "source": "mock",
            "event_type": "dispensed", "water_type": "AMBIENT",
            "amount_ml": amount_ml,
        })

    # ---- 조회

    def of(self, topic: str) -> tuple[dict, ...]:
        return self.pub.of_topic(f"hestia/{topic}")

    def decisions(self) -> tuple[dict, ...]:
        return tuple(
            p for p in self.of("intervention/decision") if p["kind"] == "hydration"
        )

    def reasons(self) -> list[str]:
        return [p["reason"] for p in self.decisions()]

    def pushes(self) -> tuple[dict, ...]:
        return tuple(
            p for p in self.of("notify/push")
            if p["scenario"] == "HYDRATION_PROMPT"
        )

    @property
    def runner(self):
        return self.engine.scenarios


@pytest.fixture
def c(tmp_path):
    return Ctx(tmp_path)


# ============================================================ 간격


def test_nothing_without_history(c):
    """재시작 직후에는 기준점이 없다. 즉시 알림이 나가면 안 된다."""
    c.home()
    c.at(MORNING + GAP + 100).tick()
    assert c.pushes() == ()


def test_no_prompt_within_gap(c):
    c.home()
    c.dispensed()
    c.at(MORNING + GAP - 600).tick()
    assert c.pushes() == ()


def test_long_gap_prompts(c):
    """마지막 급수로부터 4시간."""
    c.home()
    c.dispensed()
    c.at(MORNING + GAP + 100).tick()

    assert c.reasons() == ["LONG_GAP"]
    assert len(c.pushes()) == 1


def test_repeats_up_to_max(c):
    """안 마셔도 두 번까지. 그 뒤로는 조용히 둔다."""
    c.home()
    c.dispensed()
    c.at(MORNING + GAP + 100).tick()
    c.at(MORNING + GAP + RENOTIFY + 200).tick()
    c.at(MORNING + GAP + RENOTIFY * 2 + 300).tick()

    assert len(c.pushes()) == 2


def test_renotify_holds(c):
    """권한 직후에 또 권하지 않는다."""
    c.home()
    c.dispensed()
    c.at(MORNING + GAP + 100).tick()
    c.at(MORNING + GAP + 1000).tick()

    assert len(c.pushes()) == 1


def test_drinking_resets_counter(c):
    """마셨으면 반복 카운터를 푼다."""
    c.home()
    c.dispensed()
    c.at(MORNING + GAP + 100).tick()
    assert len(c.pushes()) == 1

    c.at(MORNING + GAP + 200).dispensed(seq=2)
    c.at(MORNING + GAP * 2 + 300).tick()
    assert len(c.pushes()) == 2


# ============================================================ 수면


def test_sleep_end_prompts_after_two_hours(c):
    """밤새 무급수였으니 깬 뒤 두 시간이면 권한다.
    WAKE_ROUTINE 이 맡던 자리다."""
    c.home()
    c.runner._last_sleep_end_at = MORNING
    c.at(MORNING + SLEEP_GAP + 100).tick()

    assert c.reasons() == ["SLEEP_END"]


def test_sleep_does_not_count_toward_gap(c):
    """자는 동안은 시간을 세지 않는다. 깬 뒤부터 다시 센다."""
    c.home()
    c.dispensed()
    c.runner._last_sleep_end_at = MORNING + GAP + 100
    c.at(MORNING + GAP + 200).tick()

    assert c.pushes() == ()


def test_drinking_after_waking_switches_to_long_gap(c):
    """깨서 마셨으면 기준이 급수로 옮겨간다."""
    c.home()
    c.runner._last_sleep_end_at = MORNING
    c.at(MORNING + 600).dispensed()

    c.at(MORNING + SLEEP_GAP + 100).tick()
    assert c.pushes() == ()                     # 2시간이 아니라 4시간

    c.at(MORNING + 600 + GAP + 100).tick()
    assert c.reasons() == ["LONG_GAP"]


def test_no_prompt_while_sleeping(c):
    c.home()
    c.dispensed()
    c.runner.asleep = AsleepState(since=MORNING, area="bedroom")
    c.at(MORNING + GAP + 100).tick()

    assert c.pushes() == ()


# ============================================================ 환경


def test_heat_onset(c):
    c.home()
    c.climate(temp=22.0)
    c.at(MORNING + 100).climate(temp=29.0, seq=2)

    assert c.reasons() == ["HEAT_ONSET"]
    assert len(c.pushes()) == 1


def test_dry_onset(c):
    """건조는 겨울 난방 때 더 심하다. 더위와 따로 본다."""
    c.home()
    c.climate(temp=20.0, humidity=30.0)

    assert c.reasons() == ["DRY_ONSET"]


def test_heat_outranks_dry(c):
    """둘 다 걸려도 사용자가 받는 것은 한 번이다."""
    c.home()
    c.climate(temp=29.0, humidity=30.0)

    assert c.reasons() == ["HEAT_ONSET"]


def test_air_stress_once_per_day(c):
    """HEAT 와 DRY 를 합쳐 하루 한 번."""
    c.home()
    c.climate(temp=29.0)
    before = len(c.pushes())

    c.at(MORNING + 100).climate(temp=20.0, humidity=30.0, seq=2)
    assert len(c.pushes()) == before


def test_heat_hysteresis(c):
    """27.9 ↔ 28.1 로 울리지 않는다."""
    c.home()
    c.climate(temp=29.0)
    assert c.runner._hot is True

    c.at(MORNING + 100).climate(temp=27.0, seq=2)
    assert c.runner._hot is True            # exit 26 아래라야 풀린다

    c.at(MORNING + 200).climate(temp=25.0, seq=3)
    assert c.runner._hot is False


def test_hot_shortens_gap(c):
    """더우면 4시간이 아니라 3시간."""
    c.home()
    c.dispensed()
    c.climate(temp=29.0)                    # HEAT_ONSET 이 한 번 나간다
    c.at(MORNING + SHORT_GAP + 100).tick()

    assert "LONG_GAP" in c.reasons()


# ============================================================ 일일 권장량


def test_daily_short_at_checkpoint(c):
    """20시에 권장량 미달이면 한 번."""
    c.home()
    c.dispensed(amount_ml=200)
    c.at(MORNING + TO_CHECKPOINT + 100).tick()

    assert "DAILY_SHORT" in c.reasons()


def test_no_daily_short_when_met(c):
    c.home()
    for i in range(8):
        c.at(MORNING + i * 60).dispensed(amount_ml=200, seq=i + 1)
    c.at(MORNING + TO_CHECKPOINT + 100).tick()

    assert "DAILY_SHORT" not in c.reasons()


# ============================================================ 귀가


def short_away(tmp_path):
    """2시간을 20분으로 줄인다. away context 가 1시간이면 UNKNOWN 으로
    가버려 실제 2시간 외출을 재현할 수 없다."""
    return Ctx(tmp_path, policy=POLICY.replace(
        "return_min_away_sec = 7200", "return_min_away_sec = 1200"
    ))


def go_out(c):
    c.home()
    c.at(MORNING + 100).door(True)
    c.at(MORNING + 110).door(False, seq=2)
    c.at(MORNING + 120).presence("vs-01", False, seq=2)
    c.at(MORNING + 800).tick()
    return c


def test_returned_home(tmp_path):
    """LONG_GAP 이 거의 같은 것을 잡지만 문구가 다르다."""
    c = go_out(short_away(tmp_path))
    assert c.engine.context.away.state == "AWAY"

    c.at(MORNING + 1600).presence("vs-01", True, seq=3)
    assert c.engine.context.away.state == "HOME"
    assert c.pushes() == ()                 # 10분은 기다린다

    c.at(MORNING + 2300).tick()
    assert c.reasons() == ["RETURNED_HOME"]


def test_drinking_cancels_return_prompt(tmp_path):
    """예약과 발송 사이에 마셨으면 보내지 않는다."""
    c = go_out(short_away(tmp_path))
    c.at(MORNING + 1600).presence("vs-01", True, seq=3)
    c.at(MORNING + 1700).dispensed()
    c.at(MORNING + 2300).tick()

    assert c.pushes() == ()


def test_short_trip_is_not_return(tmp_path):
    """잠깐 나갔다 온 것은 해당하지 않는다."""
    c = go_out(Ctx(tmp_path))               # 기본 2시간
    c.at(MORNING + 1600).presence("vs-01", True, seq=3)
    c.at(MORNING + 2300).tick()

    assert c.pushes() == ()


# ============================================================ 중단 시간


def test_pause_blocks(tmp_path):
    """22시 이후에는 권하지 않는다."""
    c = Ctx(tmp_path, start=NIGHT)
    c.home()
    c.dispensed()
    c.at(NIGHT + GAP + 100).tick()          # 03:00

    assert c.pushes() == ()


def test_pause_discards_one_shot(tmp_path):
    """정숙 시간에 쌓인 1회성 조건은 버린다.
    6시에 몰아서 보내면 사용자는 '왜 지금' 을 알 수 없다."""
    c = Ctx(tmp_path, start=NIGHT)
    c.home()
    c.runner._returned_at = NIGHT
    c.at(NIGHT + 1000).tick()

    assert c.runner._returned_at is None


# ============================================================ 전체 흐름


def test_outcome_complied(c):
    """알림 → 급수 → outcome."""
    c.home()
    c.dispensed()
    c.at(MORNING + GAP + 100).tick()
    assert len(c.pushes()) == 1

    c.at(MORNING + GAP + 400).dispensed(seq=2)
    c.at(MORNING + GAP + 2200)
    c.sched.run_due()

    r = c.of("intervention/outcome")[-1]["user_response"]
    assert r["complied"] is True
    assert r["evidence"] == "dispensed"


def test_outcome_not_complied(c):
    c.home()
    c.dispensed()
    c.at(MORNING + GAP + 100).tick()

    c.at(MORNING + GAP + 2200)
    c.sched.run_due()

    r = c.of("intervention/outcome")[-1]["user_response"]
    assert r["complied"] is False


def test_notify_links_to_decision(c):
    c.home()
    c.dispensed()
    c.at(MORNING + GAP + 100).tick()

    decision = c.decisions()[-1]
    nid = c.pushes()[0]["notify_id"]
    pending = c.engine.notifier.store.get(nid)
    assert pending.decision_id == decision["decision_id"]


def test_timer_path_triggers(c):
    """시간 경과로만 성립하는 조건이 타이머 경로에서도 걸린다."""
    c.home()
    c.dispensed()

    c.clock.advance_to(MORNING + GAP + 100)
    c.sched.run_due()

    assert len(c.pushes()) == 1