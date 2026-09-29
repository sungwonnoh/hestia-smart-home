# tests/test_clock.py
import pytest

from hestia_engine.clock import Clock, RealClock, ReplayClock


def test_protocol_satisfied():
    assert isinstance(RealClock(), Clock)
    assert isinstance(ReplayClock(), Clock)


def test_replay_clock_does_not_flow_on_its_own():
    c = ReplayClock(1755500000)
    assert c.now() == 1755500000
    assert c.now() == 1755500000


def test_advance_to():
    c = ReplayClock(1755500000)
    c.advance_to(1755500180)
    assert c.now() == 1755500180


def test_advance_by_accumulates():
    c = ReplayClock(1000)
    c.advance_by(90)
    c.advance_by(30)
    assert c.now() == 1120


def test_backward_advance_rejected_and_clock_unchanged():
    c = ReplayClock(1755500000)
    with pytest.raises(ValueError):
        c.advance_to(1755499999)
    assert c.now() == 1755500000          # 실패해도 시계는 그대로


def test_negative_advance_by_rejected():
    c = ReplayClock(1000)
    with pytest.raises(ValueError):
        c.advance_by(-1)
    assert c.now() == 1000


def test_real_clock_moves_forward():
    c = RealClock()
    assert c.now() <= c.now()