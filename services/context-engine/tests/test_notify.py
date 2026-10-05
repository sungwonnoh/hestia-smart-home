import pytest

from hestia_engine.clock import ReplayClock
from hestia_engine.config import load
from hestia_engine.context import PresenceContext, SuppressionContext
from hestia_engine.engine import RecordingPublisher
from hestia_engine.notify import (
    CLOSED_EXPIRED,
    CLOSED_SEEN,
    CLOSED_TIMEOUT,
    VOICE,
    ChannelSelector,
    Notifier,
    NotifyLimits,
    NotifyStore,
    PendingNotify,
)
from hestia_engine.timers import Scheduler
from hestia_engine.world import WorldState

from test_activity import HOME, POLICY, MORNING, NIGHT


class FakeWake:
    """wake FSM 대신. comply 판정에 필요한 것은 done_at 하나뿐이다."""

    def __init__(self) -> None:
        self._at: dict[str, float] = {}

    def done_at(self, kind: str) -> float | None:
        return self._at.get(kind)

    def mark(self, kind: str, at: float) -> None:
        self._at[kind] = at


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
        self.wake = FakeWake()
        self.notifier = Notifier(
            self.clock, self.config, self.world, self.sched,
            publish=lambda t, p, r: self.pub.publish(t, p, retain=r),
            wake_fsm=self.wake,
        )

    def at(self, ts: float):
        while (due := self.sched.next_due()) is not None and due <= ts:
            self.sched.run_due(until=due)
        self.clock.advance_to(ts)
        return self

    def device_on(self, vid: str, device_type: str, power: str = "ON"):
        """기기 state 를 넣는다. 타입마다 필수 필드가 다르다 —
        냉장고는 power 가 없고 door 를 싣는다 (명세).
        """
        import json
        from hestia_engine.messages import parse

        fields: dict = {
            "version": 1, "sent_ts": 0, "src_id": vid, "seq": 1,
            "device_type": device_type, "source": "mock",
        }
        if device_type == "smart_fridge":
            fields["door"] = "CLOSED"
        else:
            fields["power"] = power
        if device_type == "display_node":
            fields["display"] = ""

        msg = parse(f"hestia/device/{vid}/state", json.dumps(fields), self.clock.now())
        assert msg is not None
        self.world.apply(msg)
        return self

    def send(self, scenario: str = "WAKE_ROUTINE", **kw):
        kw.setdefault("title", "제목")
        kw.setdefault("text", "본문")
        # comply_kind 를 준 테스트는 wake 판정을 기대한다
        if kw.get("comply_kind") and "comply_check" not in kw:
            kw["comply_check"] = "wake"
        return self.notifier.send(scenario=scenario, **kw)

    def topics(self) -> list[str]:
        return [t for _, t, _ in self.pub.published]

    def of(self, topic: str) -> tuple[dict, ...]:
        return self.pub.of_topic(f"hestia/{topic}")


@pytest.fixture
def c(tmp_path):
    return Ctx(tmp_path)


@pytest.fixture
def night(tmp_path):
    return Ctx(tmp_path, start=NIGHT)


def presence_at(area: str | None) -> PresenceContext:
    return PresenceContext(
        name="presence", since=0.0, user_area=area,
        areas={area: True} if area else {},
    )


def suppressed(reason: str = "COOLDOWN") -> SuppressionContext:
    return SuppressionContext(
        name="suppression", since=0.0,
        active=True, reason=reason, except_=("SAFETY",),
    )


# ============================================================ 보관


def test_store_add_and_get():
    s = NotifyStore()
    n = PendingNotify("n-1", "WAKE_ROUTINE", "normal", 0.0, 10.0, 20.0, 30.0)
    s.add(n)
    assert s.get("n-1") is n
    assert s.has_open("WAKE_ROUTINE") is True


def test_store_close_once():
    """중복 outcome 을 막는다."""
    s = NotifyStore()
    s.add(PendingNotify("n-1", "WAKE_ROUTINE", "normal", 0.0, 10.0, 20.0, 30.0))

    assert s.close("n-1", CLOSED_SEEN, 5.0) is not None
    assert s.close("n-1", CLOSED_TIMEOUT, 9.0) is None      # 두 번째는 None


def test_store_open_notifies():
    s = NotifyStore()
    s.add(PendingNotify("n-1", "A", "normal", 0.0, 10.0, 20.0, 30.0))
    s.add(PendingNotify("n-2", "B", "normal", 0.0, 10.0, 20.0, 30.0))
    s.close("n-1", CLOSED_SEEN, 5.0)

    assert len(s.open_notifies()) == 1
    assert s.has_open("A") is False
    assert s.has_open("B") is True


def test_store_purge_keeps_open():
    """닫힌 뒤에도 comply 창이 남았으면 지우지 않는다."""
    s = NotifyStore()
    s.add(PendingNotify("n-1", "A", "normal", 0.0, 10.0, 20.0, comply_until=100.0))
    s.add(PendingNotify("n-2", "B", "normal", 0.0, 10.0, 20.0, comply_until=100.0))
    s.close("n-1", CLOSED_SEEN, 5.0)

    assert s.purge(before=50.0) == 0       # comply_until 이 아직 뒤
    assert s.purge(before=200.0) == 1      # n-1 만. n-2 는 열려 있다
    assert len(s) == 1


# ============================================================ 채널 선택


def test_picks_affinity_order(c):
    """affinity 순서가 선호 순위다."""
    c.device_on("vd-10", "display_node")
    c.device_on("vd-05", "smart_fridge")
    c.device_on("vd-01", "smart_tv")
    chosen = c.notifier.channels.select("WAKE_ROUTINE", presence_at(None))
    assert chosen == ("vd-10", "vd-05", "vd-01")    # display → fridge → tv



def test_prefers_user_area(c):
    c.device_on("vd-10", "display_node")
    c.device_on("vd-05", "smart_fridge")
    c.device_on("vd-01", "smart_tv")
    chosen = c.notifier.channels.select("WAKE_ROUTINE", presence_at("living"))
    assert "vd-05" not in chosen                     # 주방 냉장고는 빠진다
    assert chosen == ("vd-10", "vd-01")


def test_falls_back_when_area_empty(c):
    """근접 기기가 없다고 아무것도 안 보내는 것보다 낫다."""
    c.device_on("vd-05", "smart_fridge")
    chosen = c.notifier.channels.select("WAKE_ROUTINE", presence_at("bathroom"))
    assert "vd-05" in chosen


def test_skips_powered_off(c):
    c.device_on("vd-05", "smart_fridge")
    c.device_on("vd-01", "smart_tv", power="OFF")
    chosen = c.notifier.channels.select("WAKE_ROUTINE", presence_at("living"))
    assert "vd-01" not in chosen


def test_unknown_power_is_candidate(c):
    """state 를 못 받았다고 빼면 기동 직후 모든 알림이 막힌다."""
    chosen = c.notifier.channels.select("WAKE_ROUTINE", presence_at(None))
    assert len(chosen) > 0


def test_excludes_tried(c):
    """같은 채널 반복은 하지 않는다 (명세)."""
    c.device_on("vd-05", "smart_fridge")
    c.device_on("vd-01", "smart_tv")
    chosen = c.notifier.channels.select(
        "WAKE_ROUTINE", presence_at(None), exclude=("vd-05",)
    )
    assert "vd-05" not in chosen


def test_level_two_is_voice(c):
    """에스컬레이션 2단계는 음성으로 채널을 바꾼다."""
    assert c.notifier.channels.select("WAKE_ROUTINE", None, level=2) == (VOICE,)


def test_voice_blocked_in_quiet_hours(night):
    assert night.notifier.channels.select("WAKE_ROUTINE", None, level=2) == ()


def test_safety_voice_pierces_quiet_hours(night):
    """안전은 정숙 시간대를 뚫는다."""
    assert night.notifier.channels.select("SAFETY", None, level=2) == (VOICE,)


def test_display_node_is_channel(c):
    """ESP32 디스플레이가 affinity 에 있으면 채널 후보다."""
    c.device_on("vd-10", "display_node")
    chosen = c.notifier.channels.select("WAKE_ROUTINE", presence_at("living"))
    assert "vd-10" in chosen


# ============================================================ 총량 제한


def test_daily_limit(c):
    limits = NotifyLimits(c.clock, c.config)
    for i in range(8):
        ok, _ = limits.allows("normal")
        assert ok
        limits.note_sent()
        c.clock.advance_by(2000)             # min_interval 회피

    ok, why = limits.allows("normal")
    assert ok is False
    assert why == "DAILY_LIMIT"


def test_min_interval(c):
    """각각은 규칙을 지켰는데 총량이 과한 상황을 막는다."""
    limits = NotifyLimits(c.clock, c.config)
    limits.note_sent()
    c.clock.advance_by(60)

    ok, why = limits.allows("normal")
    assert ok is False
    assert why == "MIN_INTERVAL"


def test_health_is_never_dropped(c):
    limits = NotifyLimits(c.clock, c.config)
    for _ in range(20):
        limits.note_sent()

    ok, why = limits.allows("health")
    assert ok is True
    assert why is None


def test_counter_rolls_over_midnight(c):
    limits = NotifyLimits(c.clock, c.config)
    for _ in range(8):
        limits.note_sent()
    assert limits.sent_today == 8

    c.clock.advance_to(MORNING + 86400)       # 다음 날
    assert limits.sent_today == 0
    assert limits.allows("normal")[0] is True


# ============================================================ 발송


def test_send_publishes_push(c):
    c.device_on("vd-05", "smart_fridge")
    nid = c.send(presence=presence_at("kitchen"))

    assert nid is not None
    p = c.of("notify/push")[0]
    assert p["notify_id"] == nid
    assert p["scenario"] == "WAKE_ROUTINE"
    assert p["channels"] == ["vd-05"]
    assert p["payload"] == {"title": "제목", "text": "본문"}


def test_push_is_not_retained(c):
    """알림은 이벤트다. 재부팅 때 과거 알림이 되살아나면 안 된다."""
    seen: list[bool] = []
    c.notifier._publish = lambda t, p, r: seen.append(r)
    c.send()
    assert all(r is False for r in seen)


def test_no_duplicate_while_open(c):
    c.device_on("vd-05", "smart_fridge")
    first = c.send()
    second = c.at(MORNING + 60).send()
    assert first is not None
    assert second is None


def test_suppression_blocks(c):
    assert c.send(suppression=suppressed()) is None
    assert c.of("notify/push") == ()


def test_safety_pierces_suppression(c):
    c.device_on("vd-01", "smart_tv")
    assert c.send(scenario="SAFETY", suppression=suppressed()) is not None


def test_limit_blocks_send(c):
    """min_interval 안에는 다른 시나리오도 못 나간다."""
    c.device_on("vd-05", "smart_fridge")
    c.device_on("vd-01", "smart_tv")
    assert c.send(scenario="WAKE_ROUTINE") is not None
    assert c.at(MORNING + 120).send(scenario="MEDICATION_PROMPT") is None


# ============================================================ ack


def test_delivered_does_not_cancel(c):
    """배너를 띄우자마자 취소되면 사용자는 아무것도 못 본다."""
    c.device_on("vd-05", "smart_fridge")
    nid = c.send(comply_kind="hydration")
    c.notifier.on_ack(nid, "DELIVERED", "vd-05")

    assert "hestia/notify/cancel" not in c.topics()
    assert c.notifier.store.get(nid).is_open is True


def test_seen_cancels(c):
    """여러 채널로 보낸 뒤 하나가 SEEN 을 보내면 나머지를 종료한다."""
    c.device_on("vd-05", "smart_fridge")
    nid = c.send()
    c.notifier.on_ack(nid, "SEEN", "vd-05")

    assert c.of("notify/cancel")[0]["notify_id"] == nid


def test_seen_without_comply_closes(c):
    c.device_on("vd-05", "smart_fridge")
    nid = c.send()
    c.notifier.on_ack(nid, "SEEN", "vd-05")
    assert c.notifier.store.get(nid).is_open is False


def test_seen_with_comply_stays_open(c):
    """봤다는 것과 했다는 것은 다르다. 창은 계속 돈다."""
    c.device_on("vd-05", "smart_fridge")
    nid = c.send(comply_kind="hydration")
    c.notifier.on_ack(nid, "SEEN", "vd-05")

    n = c.notifier.store.get(nid)
    assert n.acked is True
    assert n.is_open is True                   # comply 관측이 남았다
    assert c.of("intervention/outcome") == ()  # outcome 은 창 종료 때


def test_unknown_ack_ignored(c):
    c.device_on("vd-05", "smart_fridge")
    nid = c.send()
    c.notifier.on_ack(nid, "WHATEVER", "vd-05")
    assert c.notifier.store.get(nid).is_open is True


# ============================================================ 에스컬레이션


def test_escalates_to_voice(c):
    c.device_on("vd-05", "smart_fridge")
    nid = c.send(scenario="MEDICATION_PROMPT")     # requires_ack = true

    c.at(MORNING + 700)
    c.sched.run_due()

    n = c.notifier.store.get(nid)
    assert n.escalation_level == 2
    assert VOICE in n.channels
    assert len(c.of("notify/speak")) == 1


def test_escalation_stops_at_max(c):
    c.device_on("vd-05", "smart_fridge")
    nid = c.send(scenario="MEDICATION_PROMPT")

    c.at(MORNING + 700)
    c.sched.run_due()
    c.at(MORNING + 1400)
    c.sched.run_due()

    n = c.notifier.store.get(nid)
    assert n.is_open is False
    assert n.closed_reason == CLOSED_TIMEOUT


def test_no_escalation_without_ack_requirement(c):
    """requires_ack 이 false 면 deadline 타이머를 걸지 않는다."""
    c.device_on("vd-05", "smart_fridge")
    nid = c.send()                                  # WAKE_ROUTINE, ack 불필요
    assert f"notify-deadline-{nid}" not in c.sched.keys()


def test_acked_stops_escalation(c):
    c.device_on("vd-05", "smart_fridge")
    nid = c.send(scenario="MEDICATION_PROMPT")
    c.notifier.on_ack(nid, "SEEN", "vd-05")

    c.at(MORNING + 700)
    c.sched.run_due()
    assert c.notifier.store.get(nid).escalation_level == 1


# ============================================================ comply 판정


def test_complied_when_done_in_window(c):
    c.device_on("vd-05", "smart_fridge")
    nid = c.send(comply_kind="hydration")

    c.at(MORNING + 300)
    c.wake.mark("hydration", MORNING + 300)
    c.at(MORNING + 1900)                            # comply_window 30분 경과
    c.sched.run_due()

    r = c.of("intervention/outcome")[0]["user_response"]
    assert r["complied"] is True
    assert r["evidence"] == "dispensed"
    assert r["delay_sec"] == 300


def test_not_complied_when_nothing_happens(c):
    c.device_on("vd-05", "smart_fridge")
    c.send(comply_kind="hydration")

    c.at(MORNING + 1900)
    c.sched.run_due()

    r = c.of("intervention/outcome")[0]["user_response"]
    assert r["complied"] is False
    assert r["evidence"] is None


def test_prior_action_is_not_compliance(c):
    """09:00 에 이미 마셨는데 09:30 알림의 성과로 기록하면
    L3 가 엉뚱한 채널을 학습한다."""
    c.wake.mark("hydration", MORNING - 1800)        # 발송 전에 이미
    c.device_on("vd-05", "smart_fridge")
    c.send(comply_kind="hydration")

    c.at(MORNING + 1900)
    c.sched.run_due()

    assert c.of("intervention/outcome")[0]["user_response"]["complied"] is False


def test_action_after_window_is_not_compliance(c):
    c.device_on("vd-05", "smart_fridge")
    c.send(comply_kind="hydration")

    c.at(MORNING + 1900)
    c.sched.run_due()
    c.wake.mark("hydration", MORNING + 2500)        # 창이 닫힌 뒤

    assert c.of("intervention/outcome")[0]["user_response"]["complied"] is False


def test_acked_and_complied_are_separate(c):
    """채널 효과와 알림 효과는 다른 문제다 (명세)."""
    c.device_on("vd-05", "smart_fridge")
    nid = c.send(comply_kind="hydration")
    c.notifier.on_ack(nid, "SEEN", "vd-05")         # 봤지만

    c.at(MORNING + 1900)
    c.sched.run_due()

    r = c.of("intervention/outcome")[0]["user_response"]
    assert r["acked"] is True
    assert r["complied"] is False                   # 안 했다


# ============================================================ outcome


def test_outcome_shape(c):
    c.device_on("vd-05", "smart_fridge")
    nid = c.send(comply_kind="hydration", confidence=0.81)

    c.at(MORNING + 1900)
    c.sched.run_due()

    o = c.of("intervention/outcome")[0]
    assert o["intervention_id"] == f"i-{nid}"
    assert o["type"] == "notify"
    assert o["refs"] == [nid]
    assert o["confidence"] == 0.81
    assert set(o["user_response"]) == {
        "acked", "complied", "evidence", "delay_sec", "channels_tried"
    }


def test_outcome_emitted_once(c):
    """명세: 확정 시점에 한 번만 발행한다."""
    c.device_on("vd-05", "smart_fridge")
    nid = c.send(comply_kind="hydration")
    c.notifier.on_ack(nid, "SEEN", "vd-05")

    c.at(MORNING + 1900)
    c.sched.run_due()
    c.at(MORNING + 4000)
    c.sched.run_due()

    assert len(c.of("intervention/outcome")) == 1


def test_outcome_without_comply_kind(c):
    """comply 관측이 없으면 ack 로만 판정하고 바로 닫는다."""
    c.device_on("vd-05", "smart_fridge")
    nid = c.send()
    c.notifier.on_ack(nid, "SEEN", "vd-05")

    o = c.of("intervention/outcome")[0]
    assert o["user_response"]["acked"] is True
    assert o["user_response"]["complied"] is False


def test_channels_tried_accumulates(c):
    """L3 가 어느 채널이 실패했는지 알아야 학습된다."""
    c.device_on("vd-05", "smart_fridge")
    nid = c.send(scenario="MEDICATION_PROMPT")

    c.at(MORNING + 700)
    c.sched.run_due()
    c.at(MORNING + 1400)
    c.sched.run_due()

    tried = c.of("intervention/outcome")[0]["user_response"]["channels_tried"]
    assert "vd-05" in tried
    assert VOICE in tried


def test_expiry_closes(c):
    c.device_on("vd-05", "smart_fridge")
    nid = c.send()

    c.at(MORNING + 3700)                            # expiry_sec 3600
    c.sched.run_due()

    n = c.notifier.store.get(nid)
    assert n.closed_reason == CLOSED_EXPIRED
    assert len(c.of("notify/cancel")) == 1