"""수면 기록 → 밤잠 / 낮잠 구분 → sleep_time / wake_time 학습 표본 (학습 데이터 선별)."""

import json
import logging
from datetime import datetime, timedelta

import numpy as np
import pytest

from baseline import build_model, calculate_predictability, fit_distribution
from model_payload import build_kde_payload, validate_kde_payload
from samples import KdeSample, values
from sleep_sessions import (
    MAX_SESSION_HOURS,
    NAP,
    NIGHT,
    SleepSession,
    classify_sessions,
    drop_implausible,
    load_records,
    merge_sessions,
    night_samples,
    parse_session_record,
    samples_from_records,
    session_records,
    write_records,
)


def t(s: str) -> datetime:
    return datetime.fromisoformat(s)


def session(start, end, **kw):
    return SleepSession(t(start), t(end), **kw)


def hhmm(sample):
    m = int(round(sample.value))
    return f"{m // 60 % 24:02d}:{m % 60:02d}"


# ============================================================ 병합


def test_short_gap_is_merged():
    merged = merge_sessions([
        session("2026-09-01 23:00", "2026-09-02 02:00"),
        session("2026-09-02 02:10", "2026-09-02 07:00"),
    ])
    assert len(merged) == 1
    assert (merged[0].start, merged[0].end, merged[0].parts) == (t("2026-09-01 23:00"), t("2026-09-02 07:00"), 2)


def test_bridge_merges_long_gap():
    a, b = session("2026-09-01 23:00", "2026-09-02 02:00"), session("2026-09-02 02:40", "2026-09-02 07:00")
    assert len(merge_sessions([a, b])) == 2
    assert len(merge_sessions([a, b], bridges=[t("2026-09-02 02:01")])) == 1


def test_negative_gap_is_not_merged():
    """시각 역전(기록 오류)은 합치지 않는다."""
    merged = merge_sessions([
        session("2026-09-01 23:00", "2026-09-02 07:00"),
        session("2026-09-02 06:00", "2026-09-02 06:30"),
    ])
    assert len(merged) == 2


def test_merge_keeps_last_wake_flag_and_prompted():
    merged = merge_sessions([
        session("2026-09-01 23:00", "2026-09-02 02:00", prompted=True),
        session("2026-09-02 02:05", "2026-09-02 07:00", wake_observed=False),
    ])
    assert merged[0].prompted is True
    assert merged[0].wake_observed is False


def test_implausible_session_dropped():
    kept, dropped = drop_implausible([
        session("2026-09-01 23:00", "2026-09-02 07:00"),
        session("2026-09-02 00:10", "2026-09-03 08:24"),       # 32시간 — 센서 공백
    ])
    assert len(kept) == 1 and len(dropped) == 1
    assert MAX_SESSION_HOURS == 24


# ============================================================ 밤잠 / 낮잠


def test_night_and_nap():
    c = classify_sessions([
        session("2026-09-01 14:00", "2026-09-01 15:00"),      # 낮잠
        session("2026-09-01 23:00", "2026-09-02 07:00"),      # 밤잠
        session("2026-09-02 13:30", "2026-09-02 14:10"),      # 다음 날 낮잠
        session("2026-09-02 22:30", "2026-09-03 06:30"),      # 다음 밤잠
    ])
    assert [x.kind for x in c] == [NAP, NIGHT, NAP, NIGHT]
    assert [x.night for x in c] == ["2026-09-01", "2026-09-01", "2026-09-02", "2026-09-02"]


def test_after_midnight_sleep_belongs_to_previous_night():
    [c] = classify_sessions([session("2026-09-02 00:30", "2026-09-02 07:30")])
    assert (c.kind, c.night) == (NIGHT, "2026-09-01")


def test_no_fixed_bedtime_assumed():
    """고정 취침 시각이 없다 — 늦게 자는 사람의 03:00~11:00 수면도 그 밤의 밤잠이다."""
    c = classify_sessions([
        session("2026-09-01 15:00", "2026-09-01 16:00"),
        session("2026-09-02 03:00", "2026-09-02 11:00"),
    ])
    assert [x.kind for x in c] == [NAP, NIGHT]


def test_lone_nap_without_night_is_night_known_limitation():
    """알려진 한계: 그 밤의 수면 기록이 없으면 낮잠이 그 범위의 가장 긴 수면이 된다."""
    c = classify_sessions([
        session("2026-09-02 00:30", "2026-09-02 07:30"),       # 9/1 밤
        session("2026-09-02 13:00", "2026-09-02 14:00"),       # 9/2 범위 — 밤 기록 없음
    ])
    assert [(x.kind, x.night) for x in c] == [(NIGHT, "2026-09-01"), (NIGHT, "2026-09-02")]


def test_tie_goes_to_first():
    c = classify_sessions([
        session("2026-09-01 21:00", "2026-09-01 23:00"),
        session("2026-09-02 01:00", "2026-09-02 03:00"),
    ])
    assert [x.kind for x in c] == [NIGHT, NAP]


def test_naps_are_not_learned():
    c = classify_sessions([
        session("2026-09-01 14:00", "2026-09-01 15:00"),
        session("2026-09-01 23:00", "2026-09-02 07:00"),
    ])
    s = night_samples(c, "sensor", proxy=False)
    assert [hhmm(x) for x in s["sleep_time"]] == ["23:00"]
    assert [hhmm(x) for x in s["wake_time"]] == ["07:00"]
    assert s["sleep_time"][0].date == "2026-09-01" and s["wake_time"][0].date == "2026-09-02"


def test_unobserved_wake_keeps_bedtime_only():
    c = classify_sessions([session("2026-09-01 22:10", "2026-09-01 23:58", wake_observed=False)])
    s = night_samples(c, "aruba", proxy=True)
    assert len(s["sleep_time"]) == 1 and s["wake_time"] == []
    assert s["sleep_time"][0].proxy is True


# ============================================================ 내부 레코드 (SLEEP.md 형식)


def test_parse_session_record_iso_and_epoch():
    iso = parse_session_record({"type": "sleep", "t0": "2026-09-01T23:00:00", "duration_sec": 28800})
    epoch = parse_session_record({"type": "sleep", "t0": 1788271200, "duration_sec": 28800})   # 2026-09-01 23:00 KST
    assert iso == epoch
    assert iso.end == t("2026-09-02 07:00")


@pytest.mark.parametrize("obj", [
    {"type": "wake", "t0": "2026-09-01T23:00:00", "duration_sec": 1},
    {"type": "sleep", "duration_sec": 100},
    {"type": "sleep", "t0": "2026-09-01T23:00:00"},
    {"type": "sleep", "t0": "2026-09-01T23:00:00", "duration_sec": -1},
    {"type": "sleep", "t0": "2026-09-01T23:00:00", "duration_sec": True},
    {"type": "sleep", "t0": "어제 밤", "duration_sec": 100},
    {"type": "sleep", "t0": "2026-09-01T23:00:00", "duration_sec": 100, "prompted": "no"},
    [],
])
def test_invalid_session_record(obj):
    with pytest.raises(ValueError):
        parse_session_record(obj)


def records(*sessions):
    """(start, end, wake 기록 여부)"""
    out = []
    for start, end, woke in sessions:
        s = session(start, end)
        out.append({"type": "sleep", "t0": s.start.isoformat(), "duration_sec": s.minutes * 60,
                    "source": "sensor", "prompted": False, "date": s.start.date().isoformat()})
        if woke:
            out.append({"type": "wake", "t0": s.end.isoformat(), "duration_sec": 0,
                        "source": "sensor", "prompted": False, "date": s.end.date().isoformat()})
    return out


def test_records_to_night_samples():
    """Context Engine 이 수면 기록(밤잠 + 낮잠)을 주면 밤잠만 골라 학습한다."""
    series, classified = samples_from_records(records(
        ("2026-09-01 14:00", "2026-09-01 15:00", True),
        ("2026-09-01 23:00", "2026-09-02 07:00", True),
        ("2026-09-02 23:30", "2026-09-03 06:45", False),
    ))
    assert [c.kind for c in classified] == [NAP, NIGHT, NIGHT]
    assert [hhmm(x) for x in series["sleep_time"]] == ["23:00", "23:30"]
    assert [hhmm(x) for x in series["wake_time"]] == ["07:00"]          # wake 기록이 없는 밤은 제외


def test_bad_record_is_skipped(caplog):
    good = records(("2026-09-01 23:00", "2026-09-02 07:00", True))
    with caplog.at_level(logging.WARNING):
        series, _ = samples_from_records(good + [{"type": "sleep", "t0": "x", "duration_sec": 5}])
    assert len(series["sleep_time"]) == 1
    assert "건너뜀" in caplog.text


def test_export_and_reload_round_trip(tmp_path):
    original = classify_sessions([
        session("2026-09-01 14:00", "2026-09-01 15:00"),
        session("2026-09-01 23:00", "2026-09-02 07:00"),
        session("2026-09-02 22:10", "2026-09-02 23:58", wake_observed=False),
    ])
    path = tmp_path / "sleep.jsonl"
    write_records(session_records(original, "aruba"), path)

    lines = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()]
    assert [r["type"] for r in lines] == ["sleep", "wake", "sleep", "wake", "sleep"]
    assert set(lines[0]) == {"date", "type", "t0", "source", "prompted", "duration_sec"}

    series, reloaded = samples_from_records(load_records(path))
    assert [c.kind for c in reloaded] == [c.kind for c in original]
    assert [hhmm(x) for x in series["sleep_time"]] == ["23:00", "22:10"]
    assert [hhmm(x) for x in series["wake_time"]] == ["07:00"]


def test_load_records_skips_broken_lines(tmp_path, caplog):
    path = tmp_path / "sleep.jsonl"
    path.write_text('{"type": "sleep"}\n{broken\n[1]\n\n', encoding="utf-8")
    with caplog.at_level(logging.WARNING):
        assert load_records(path) == [{"type": "sleep"}]
    assert "sleep.jsonl:2" in caplog.text


# ============================================================ SLEEP.md §15


def clock(*hhmm_list, distribution="sleep_time"):
    out = []
    for i, x in enumerate(hhmm_list):
        h, m = map(int, x.split(":"))
        out.append(KdeSample(distribution, float(h * 60 + m), f"2026-09-{i + 1:02d}", "sensor"))
    return out


def test_15_1_sleep_time_basic():
    _, density = fit_distribution("sleep_time", values(clock("22:30", "23:00", "23:15", "23:30")))
    assert len(density) == 96
    assert sum(density) == pytest.approx(1.0)


def test_15_2_wake_time_basic():
    _, density = fit_distribution("wake_time", values(clock("06:30", "06:45", "07:00", "07:15", distribution="wake_time")))
    assert len(density) == 96
    assert sum(density) == pytest.approx(1.0)
    assert int(np.argmax(density)) in (26, 27, 28)                 # 06:30~07:15


def test_15_3_circular_boundary():
    """23:50 / 00:00 / 00:10 은 자정 근처 하나의 봉우리다."""
    _, density = fit_distribution("sleep_time", values(clock("23:50", "00:00", "00:10")))
    assert int(np.argmax(density)) in (95, 0)
    assert sum(density[-2:]) + sum(density[:2]) > 0.6               # 23:30~00:30
    assert sum(density[40:56]) < 1e-6                                # 10:00~14:00 은 비어 있다


def test_15_4_predictability_regular_vs_irregular():
    regular = calculate_predictability(
        fit_distribution("sleep_time", values(clock("23:00", "23:00", "23:15", "23:00", "23:15")))[1]
    )
    irregular = calculate_predictability(
        fit_distribution("sleep_time", values(clock("18:00", "22:00", "01:00", "04:00", "15:00")))[1]
    )
    assert regular > irregular
    assert regular > 0.5 and irregular < 0.2


def test_15_5_model_payload_has_sleep_and_wake():
    samples = clock("22:30", "23:00", "23:15", "23:30") + clock("06:30", "06:45", "07:00", "07:15", distribution="wake_time")
    payload = build_kde_payload(build_model(samples))
    validate_kde_payload(payload)
    assert payload["trained_at"] > 0 and payload["sample_days"] == 4
    assert {"sleep_time", "wake_time"} <= set(payload["distributions"])
    assert {"sleep_time", "wake_time"} <= set(payload["predictability"])
