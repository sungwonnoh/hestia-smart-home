# tests/test_timers.py
from hestia_engine.clock import ReplayClock
from hestia_engine.timers import Scheduler


def fixture():
    clock = ReplayClock(1000)
    return clock, Scheduler(clock), []


def test_nothing_fires_before_due():
    clock, sched, fired = fixture()
    sched.after(60, lambda: fired.append("a"))
    assert sched.run_due() == 0
    clock.advance_to(1059)
    assert sched.run_due() == 0
    assert fired == []


def test_fires_in_due_order():
    clock, sched, fired = fixture()
    sched.after(300, lambda: fired.append("late"))
    sched.after(60, lambda: fired.append("early"))
    clock.advance_to(2000)
    assert sched.run_due() == 2
    assert fired == ["early", "late"]


def test_same_due_keeps_registration_order():
    clock, sched, fired = fixture()
    sched.at(1500, lambda: fired.append("first"))
    sched.at(1500, lambda: fired.append("second"))
    sched.run_due(until=2000)
    assert fired == ["first", "second"]


def test_callback_sees_scheduled_time_not_current_time():
    """예정 시각 규칙 — 이 테스트가 깨지면 Replay 결정성이 깨진 것."""
    clock, sched, seen = fixture()
    sched.after(60, lambda: seen.append(clock.now()))
    sched.after(300, lambda: seen.append(clock.now()))
    sched.run_due(until=2000)
    assert seen == [1060, 1300]


def test_cancel():
    clock, sched, fired = fixture()
    sched.after(60, lambda: fired.append("x"), key="ack-n-01")
    assert sched.cancel("ack-n-01") is True
    assert sched.cancel("ack-n-01") is False
    clock.advance_to(2000)
    assert sched.run_due() == 0
    assert fired == []


def test_same_key_replaces_previous():
    clock, sched, fired = fixture()
    sched.after(60, lambda: fired.append("first"), key="deadline")
    sched.after(600, lambda: fired.append("second"), key="deadline")
    assert sched.pending() == 1
    sched.run_due(until=2000)
    assert fired == ["second"]


def test_callback_can_schedule_next_stage():
    """에스컬레이션 1단계 -> 2단계가 같은 루프에서 처리된다."""
    clock, sched, fired = fixture()

    def stage2():
        fired.append(("stage2", clock.now()))

    def stage1():
        fired.append(("stage1", clock.now()))
        sched.after(120, stage2, key="esc")

    sched.after(60, stage1, key="esc")
    sched.run_due(until=5000)
    assert fired == [("stage1", 1060), ("stage2", 1180)]


def test_callback_can_cancel_another_timer():
    clock, sched, fired = fixture()
    sched.after(120, lambda: fired.append("victim"), key="victim")
    sched.after(60, lambda: sched.cancel("victim"))
    sched.run_due(until=2000)
    assert fired == []


def test_next_due():
    clock, sched, _ = fixture()
    assert sched.next_due() is None
    sched.after(60, lambda: None, key="a")
    sched.after(10, lambda: None, key="b")
    assert sched.next_due() == 1010
    sched.cancel("b")
    assert sched.next_due() == 1060


def test_three_day_gap_costs_nothing():
    """무활동 감지 타이머가 3일 뒤여도 즉시 발화한다."""
    clock, sched, fired = fixture()
    sched.after(3 * 86400, lambda: fired.append(clock.now()))
    clock.advance_to(1000 + 3 * 86400)
    assert sched.run_due() == 1
    assert fired == [1000 + 3 * 86400]


def test_pending_excludes_cancelled():
    clock, sched, _ = fixture()
    sched.after(60, lambda: None, key="a")
    sched.after(90, lambda: None, key="b")
    sched.cancel("a")
    assert sched.pending() == 1