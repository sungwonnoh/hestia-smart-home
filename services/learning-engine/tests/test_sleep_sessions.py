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
    merge_sessions,
    night_samples,
    samples_from_events,
    session_events,
    sessions_from_events,
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


# ============================================================ Context Engine sleep_start / sleep_end


def epoch(local: str) -> float:
    """로컬(KST) 시각 문자열 → epoch"""
    from sleep_sessions import KST
    return t(local).replace(tzinfo=KST).timestamp()


def ev(type_, local, **extra):
    """Context Engine t0 로그 형식"""
    return {"date": local[:10], "type": type_, "t0": epoch(local), "source": "sensor",
            "prompted": False, "duration_sec": 0, **extra}


def test_events_pair_into_sessions():
    sessions, dropped = sessions_from_events([
        ev("sleep_start", "2026-09-01 23:00", area="bedroom"),
        ev("sleep_end", "2026-09-02 07:00"),
    ])
    assert dropped == 0
    [s] = sessions
    assert (s.start, s.end, s.wake_observed) == (t("2026-09-01 23:00"), t("2026-09-02 07:00"), True)
    assert (s.area, s.source) == ("bedroom", "sensor")


def test_events_are_sorted_by_t0():
    """로그 순서가 섞여도 시각 순으로 짝짓는다."""
    sessions, _ = sessions_from_events([
        ev("sleep_end", "2026-09-02 07:00"),
        ev("sleep_start", "2026-09-01 23:00"),
    ])
    assert [(x.start.hour, x.end.hour) for x in sessions] == [(23, 7)]


def test_unmatched_events():
    sessions, dropped = sessions_from_events([
        ev("sleep_end", "2026-09-01 06:00"),               # start 없음 → 버림
        ev("sleep_start", "2026-09-01 22:00"),             # end 없이 다음 start → 기상 미관측
        ev("sleep_start", "2026-09-01 23:00"),
        ev("sleep_end", "2026-09-02 07:00"),
        ev("sleep_start", "2026-09-02 23:10"),             # 마지막 — 아직 자는 중
    ])
    assert dropped == 1
    assert [(x.start.strftime("%d %H:%M"), x.wake_observed, x.minutes) for x in sessions] == [
        ("01 22:00", False, 0), ("01 23:00", True, 480), ("02 23:10", False, 0),
    ]


def test_other_types_are_ignored():
    sessions, dropped = sessions_from_events([
        ev("meal", "2026-09-01 08:00"),
        ev("wake", "2026-09-01 07:00"),
        ev("sleep_start", "2026-09-01 23:00"),
        ev("sleep_end", "2026-09-02 07:00"),
    ])
    assert len(sessions) == 1 and dropped == 0


def test_unreadable_t0_is_dropped(caplog):
    with caplog.at_level(logging.WARNING):
        sessions, dropped = sessions_from_events([{"type": "sleep_start", "t0": "어제 밤"}])
    assert sessions == [] and dropped == 1
    assert "건너뜀" in caplog.text


def test_prompted_and_area_from_start():
    [s], _ = sessions_from_events([
        ev("sleep_start", "2026-09-01 14:00", area="living", prompted=True),
        ev("sleep_end", "2026-09-01 15:00"),
    ])
    assert (s.prompted, s.area) == (True, "living")


def test_events_to_night_samples():
    """Context Engine 이 수면(밤잠 + 낮잠)을 보내면 밤잠만 골라 학습한다."""
    series, classified = samples_from_events([
        ev("sleep_start", "2026-09-01 14:00", area="living"),
        ev("sleep_end", "2026-09-01 15:00"),
        ev("sleep_start", "2026-09-01 23:00", area="bedroom"),
        ev("sleep_end", "2026-09-02 02:00"),                # 화장실
        ev("sleep_start", "2026-09-02 02:10", area="bedroom"),
        ev("sleep_end", "2026-09-02 07:00"),
        ev("sleep_start", "2026-09-02 23:30", area="bedroom"),   # 기상 기록 없음
    ])
    assert [c.kind for c in classified] == [NAP, NIGHT, NIGHT]
    assert classified[1].session.parts == 2                 # 끊긴 수면 병합
    assert [hhmm(x) for x in series["sleep_time"]] == ["23:00", "23:30"]
    assert [hhmm(x) for x in series["wake_time"]] == ["07:00"]
    assert {x.source for x in series["sleep_time"]} == {"sensor"}


def test_export_and_reload_round_trip(tmp_path):
    original = classify_sessions([
        session("2026-09-01 14:00", "2026-09-01 15:00", area="living"),
        session("2026-09-01 23:00", "2026-09-02 07:00", area="bedroom"),
        session("2026-09-02 22:10", "2026-09-02 23:58", wake_observed=False),
    ])
    path = tmp_path / "t0.jsonl"
    write_records(session_events(original, "aruba"), path)

    lines = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()]
    assert [r["type"] for r in lines] == ["sleep_start", "sleep_end", "sleep_start", "sleep_end", "sleep_start"]
    assert set(lines[1]) == {"date", "type", "t0", "source", "prompted", "duration_sec"}
    assert lines[0]["area"] == "living" and "area" not in lines[4]
    assert lines[2]["t0"] == epoch("2026-09-01 23:00")

    series, reloaded = samples_from_events(lines)
    assert [c.kind for c in reloaded] == [c.kind for c in original]
    assert [hhmm(x) for x in series["sleep_time"]] == ["23:00", "22:10"]
    assert [hhmm(x) for x in series["wake_time"]] == ["07:00"]
    assert {x.source for x in series["sleep_time"]} == {"aruba"}


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


# ============================================================ awake_areas 로 수면 잇기


def night_with_wake(gap_end, awake_areas, extra=()):
    """23:00 잠듦 → 02:00 깸 → gap_end 다시 잠 (awake_areas) → 07:00 기상"""
    start2 = ev("sleep_start", gap_end, area="bedroom")
    if awake_areas != "missing":
        start2["awake_areas"] = awake_areas
    return [
        ev("sleep_start", "2026-09-01 23:00", area="bedroom", awake_areas=[]),
        ev("sleep_end", "2026-09-02 02:00"),
        start2,
        ev("sleep_end", "2026-09-02 07:00"),
        *extra,
    ]


@pytest.mark.parametrize("awake_areas", [[], ["bathroom"]])
def test_long_wake_in_bed_or_bathroom_is_same_night(awake_areas):
    """새벽에 2시간 깨어 있었어도 침실·화장실만 있었으면 하룻밤이다 (CASAS Milan 사례)."""
    series, classified = samples_from_events(night_with_wake("2026-09-02 04:00", awake_areas))
    assert [c.kind for c in classified] == [NIGHT]
    assert classified[0].session.parts == 2
    assert [hhmm(x) for x in series["sleep_time"]] == ["23:00"]
    assert [hhmm(x) for x in series["wake_time"]] == ["07:00"]


@pytest.mark.parametrize("awake_areas", [["kitchen"], ["bathroom", "living"], "missing", None, "x"])
def test_got_up_or_unknown_falls_back_to_gap(awake_areas):
    """실제로 일어나 활동했거나(주방·거실) 모르면 간격 규칙(15분) — 2시간이면 따로."""
    series, classified = samples_from_events(night_with_wake("2026-09-02 03:30", awake_areas))
    assert len(classified) == 2
    # 하룻밤이 쪼개져 더 긴 뒤쪽(03:30~07:00)이 밤잠이 된다 — 취침 시각이 03:30 으로 틀어지는 경우
    assert [hhmm(x) for x in series["sleep_time"]] == ["03:30"]
    assert classified[0].session.awake_areas is None or classified[0].session.awake_areas == ()


def test_short_gap_still_merges_even_after_kitchen():
    """잇는 방향으로만 쓴다 — 주방에 다녀왔어도 15분 이내면 같은 수면 (새벽 물 마시기)."""
    _, classified = samples_from_events(night_with_wake("2026-09-02 02:10", ["kitchen"]))
    assert [c.kind for c in classified] == [NIGHT] and classified[0].session.parts == 2


def test_nap_after_real_wake_is_not_merged():
    """아침에 일어나 주방에 갔다가 낮잠 — 밤잠에 붙지 않는다."""
    events = [
        ev("sleep_start", "2026-09-01 23:00", area="bedroom", awake_areas=[]),
        ev("sleep_end", "2026-09-02 07:00"),
        ev("sleep_start", "2026-09-02 14:00", area="bedroom", awake_areas=["kitchen", "living"]),
        ev("sleep_end", "2026-09-02 15:00"),
        ev("sleep_start", "2026-09-02 23:00", area="bedroom", awake_areas=["kitchen"]),
        ev("sleep_end", "2026-09-03 07:00"),
    ]
    _, classified = samples_from_events(events)
    assert [c.kind for c in classified] == [NIGHT, NAP, NIGHT]


def test_awake_areas_parsed_and_kept_on_session():
    sessions, _ = sessions_from_events([
        ev("sleep_start", "2026-09-01 23:00", area="bedroom", awake_areas=["bathroom"]),
        ev("sleep_end", "2026-09-02 07:00"),
    ])
    assert sessions[0].awake_areas == ("bathroom",)


def test_resume_areas_configurable():
    events = night_with_wake("2026-09-02 04:00", ["living"])
    assert len(samples_from_events(events)[1]) == 2
    merged = merge_sessions(sessions_from_events(events)[0], resume_areas=frozenset({"bathroom", "living"}))
    assert len(merged) == 1
    off = merge_sessions(sessions_from_events(night_with_wake("2026-09-02 04:00", []))[0], resume_areas=None)
    assert len(off) == 2


def test_casas_sessions_have_unknown_awake_areas():
    """CASAS 에는 이 정보가 없다 — 기존 결과(간격 + 화장실 라벨)가 바뀌지 않는다."""
    s = session("2026-09-01 23:00", "2026-09-02 02:00")
    assert s.awake_areas is None
