# tests/test_timeutil.py
from datetime import timezone

import pytest

from hestia_engine.timeutil import (
    KST,
    day_key,
    grid_index,
    grid_size,
    is_same_day,
    minutes_since_midnight,
)

# 2026-09-25 09:40:00 KST  ==  2026-09-25 00:40:00 UTC
TS = 1790296800.0


def test_minutes_since_midnight_is_kst():
    assert minutes_since_midnight(TS) == 9 * 60 + 40


def test_utc_differs_by_nine_hours():
    """타임존을 안 맞추면 9시간 어긋난다는 것을 명시적으로 박아둔다."""
    assert minutes_since_midnight(TS, tz=timezone.utc) == 40


def test_seconds_are_discarded():
    assert minutes_since_midnight(TS + 59) == minutes_since_midnight(TS)


def test_grid_index_basic():
    assert grid_index(580) == 38           # 09:40 -> 09:30~09:45


def test_grid_index_boundary_rounds_down_to_next_cell():
    assert grid_index(585) == 39           # 09:45 는 39번의 시작
    assert grid_index(584) == 38


def test_grid_index_day_edges():
    assert grid_index(0) == 0
    assert grid_index(1439) == 95


def test_grid_index_with_five_minute_step():
    assert grid_index(37, grid_step=5) == 7


def test_grid_index_with_offset():
    assert grid_index(100, grid_min=60, grid_step=5) == 8


def test_grid_size():
    assert grid_size(15) == 96
    assert grid_size(5) == 288


def test_invalid_step_rejected():
    with pytest.raises(ValueError):
        grid_index(100, grid_step=0)
    with pytest.raises(ValueError):
        grid_size(-5)


def test_day_key_uses_kst():
    assert day_key(TS) == "2026-09-25"
    assert day_key(TS, tz=timezone.utc) == "2026-09-25"


def test_day_key_crosses_at_kst_midnight():
    """UTC 15:00 = KST 자정. 이 경계에서 날짜가 갈린다."""
    before = 1790261999.0                  # KST 2026-09-24 23:59:59
    after = before + 1                     # KST 2026-09-25 00:00:00
    assert day_key(before) == "2026-09-24"
    assert day_key(after) == "2026-09-25"
    assert is_same_day(before, after) is False


def test_kst_offset():
    assert KST.utcoffset(None).total_seconds() == 9 * 3600