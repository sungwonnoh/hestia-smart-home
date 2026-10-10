import csv
import logging
from datetime import datetime
from pathlib import Path

import pytest

from aruba import (
    ActivityEvent,
    clock_minutes,
    count_labels,
    extract_meal,
    meal_label_begins,
    meal_records,
    meal_sessions,
    extract_samples,
    extract_sleep,
    pair_segments,
    parse_line,
    parse_ts,
    read_events,
)


REPO = Path(__file__).resolve().parents[3]
RAW = REPO / "data" / "raw" / "casas" / "aruba" / "aruba.txt"     # gitignore — 로컬에만 있다
PROCESSED = REPO / "data" / "processed" / "aruba"

needs_raw = pytest.mark.skipif(not RAW.exists(), reason="Aruba 원본 없음 (data/raw 는 gitignore)")


def label(ts: str, name: str, kind: str) -> str:
    """원본과 같은 탭 구분 라벨 줄"""
    return f"{ts}\tM003\tON\t{name}\t{kind}"


def sensor(ts: str) -> str:
    return f"{ts}\tT002\t21.5"


def write(tmp_path: Path, *lines: str, tail: bool = True) -> Path:
    """tail=True 면 마지막에 센서 줄을 붙인다 — 라벨이 파일 끝에 걸리지 않도록."""
    if tail:
        lines = (*lines, sensor("2099-01-01 00:00:00.000000"))
    path = tmp_path / "aruba.txt"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def sleep_lines(begin: str, end: str) -> list[str]:
    return [label(begin, "Sleeping", "begin"), label(end, "Sleeping", "end")]


def toilet_lines(begin: str, end: str) -> list[str]:
    return [label(begin, "Bed_to_Toilet", "begin"), label(end, "Bed_to_Toilet", "end")]


def ev(ts: str, name: str, kind: str, line_no: int) -> ActivityEvent:
    return ActivityEvent(parse_ts(*ts.split()), name, kind, line_no)


def hhmm(sample) -> str:
    m = int(sample.value)
    return f"{m // 60:02d}:{m % 60:02d}"


def hour(sample) -> int:
    return int(sample.value // 60)


# ============================================================ 파싱


def test_parse_label_line():
    e = parse_line(label("2010-11-04 00:03:50.209589", "Sleeping", "begin"), 7)
    assert e == ev("2010-11-04 00:03:50.209589", "Sleeping", "begin", 7)


def test_parse_short_fraction():
    """원본에 소수점 자릿수가 짧은 줄이 있다 (2010-11-13 08:57:07.95895)."""
    assert parse_ts("2010-11-13", "08:57:07.95895") == datetime(2010, 11, 13, 8, 57, 7, 958950)


def test_parse_line_without_fraction():
    """원본에는 소수점 초가 없는 줄도 섞여 있다."""
    e = parse_line(label("2011-01-29 09:22:15", "Meal_Preparation", "begin"))
    assert e.ts == datetime(2011, 1, 29, 9, 22, 15)


@pytest.mark.parametrize("line", [sensor("2010-11-04 00:03:57.399391"), "", "   "])
def test_sensor_and_blank_lines_are_not_events(line):
    assert parse_line(line) is None


@pytest.mark.parametrize(
    "line",
    [
        label("2010-11-0X 07:00:00.000000", "Meal_Preparation", "begin"),
        label("2010-11-04 25:00:00.000000", "Meal_Preparation", "begin"),
        label("2010-11-04 07:00:00.000000", "Meal_Preparation", "start"),
        "2010-11-04 07:00:00.000000\tM018\tON\tMeal_Preparation",
        "2010-11-04 07:00:00.000000\tM018\tON\tMeal_Preparation\tbegin\textra",
    ],
)
def test_malformed_label_line_is_rejected(line):
    with pytest.raises(ValueError):
        parse_line(line)


def test_read_events_skips_malformed_rows(tmp_path, caplog):
    path = write(
        tmp_path,
        label("2010-11-04 07:00:00.000000", "Meal_Preparation", "begin"),
        label("2010-11-0X 08:00:00.000000", "Meal_Preparation", "begin"),
        sensor("2010-11-04 08:30:00.000000"),
        label("2010-11-04 09:00:00.000000", "Meal_Preparation", "begin"),
    )
    with caplog.at_level(logging.WARNING):
        log = read_events(path)
    assert [e.ts.hour for e in log.events] == [7, 9]
    assert "aruba.txt:2" in caplog.text
    assert log.last_line_no == 5


def test_read_events_filters_labels(tmp_path):
    path = write(
        tmp_path,
        label("2010-11-04 07:00:00.000000", "Meal_Preparation", "begin"),
        label("2010-11-04 08:00:00.000000", "Relax", "begin"),
    )
    assert [e.label for e in read_events(path, {"Relax"}).events] == ["Relax"]


def test_count_labels():
    events = [
        ev("2010-11-04 07:00:00", "Meal_Preparation", "begin", 1),
        ev("2010-11-04 07:10:00", "Meal_Preparation", "end", 2),
        ev("2010-11-04 08:00:00", "Meal_Preparation", "begin", 3),
    ]
    assert count_labels(events)[("Meal_Preparation", "begin")] == 2


# ============================================================ begin/end 짝


def test_pair_segments():
    events = [
        ev("2010-11-04 23:00:00", "Sleeping", "begin", 1),
        ev("2010-11-04 23:10:00", "Relax", "begin", 2),
        ev("2010-11-05 06:00:00", "Sleeping", "end", 3),
    ]
    segments, dropped = pair_segments(events, "Sleeping")
    assert dropped == 0
    assert len(segments) == 1
    assert segments[0].minutes == 7 * 60


def test_unmatched_begin_and_end_are_dropped():
    events = [
        ev("2010-11-04 01:00:00", "Sleeping", "end", 1),       # 짝 없는 end
        ev("2010-11-04 22:00:00", "Sleeping", "begin", 2),     # 다음 begin 에 밀림
        ev("2010-11-04 23:00:00", "Sleeping", "begin", 3),
        ev("2010-11-05 06:00:00", "Sleeping", "end", 4),
        ev("2010-11-05 23:00:00", "Sleeping", "begin", 5),     # 파일 끝까지 end 없음
    ]
    segments, dropped = pair_segments(events, "Sleeping")
    assert dropped == 3
    assert [s.begin.line_no for s in segments] == [3]


def test_reversed_segment_is_dropped_not_corrected(caplog):
    """원본 2011-05-23: 날짜가 넘어가지 않고 찍혀 end 가 begin 보다 이르다.
       실제 날짜를 확정할 수 없으므로 보정하지 않고 버린다."""
    events = [
        ev("2011-05-23 21:44:59.759837", "Sleeping", "begin", 1571258),
        ev("2011-05-23 02:43:44.902903", "Sleeping", "end", 1571331),
    ]
    with caplog.at_level(logging.WARNING):
        segments, dropped = pair_segments(events, "Sleeping")
    assert (segments, dropped) == ([], 1)
    assert "역전" in caplog.text


# ============================================================ 수면 병합


def episodes_of(tmp_path, *lines, **kwargs):
    """병합 후 수면 목록 (밤잠 + 낮잠)"""
    return extract_sleep(read_events(write(tmp_path, *lines)), **kwargs).episodes


def test_toilet_trip_merges_sleep(tmp_path):
    """화장실 다녀온 뒤 다시 잔 시각을 취침 시각으로 쓰면 안 된다."""
    episodes = episodes_of(
        tmp_path,
        *sleep_lines("2010-11-04 23:00:00.0", "2010-11-05 02:00:00.0"),
        *toilet_lines("2010-11-05 02:00:05.0", "2010-11-05 02:30:00.0"),
        *sleep_lines("2010-11-05 02:40:00.0", "2010-11-05 06:30:00.0"),
    )
    assert len(episodes) == 1
    assert episodes[0].start.hour == 23
    assert episodes[0].end.hour == 6
    assert episodes[0].parts == 2


def test_short_gap_merges_without_toilet(tmp_path):
    episodes = episodes_of(
        tmp_path,
        *sleep_lines("2010-11-05 00:02:00.0", "2010-11-05 00:34:00.0"),
        *sleep_lines("2010-11-05 00:36:00.0", "2010-11-05 07:34:00.0"),
    )
    assert len(episodes) == 1


def test_long_gap_without_toilet_splits(tmp_path):
    episodes = episodes_of(
        tmp_path,
        *sleep_lines("2010-11-05 14:00:00.0", "2010-11-05 15:00:00.0"),
        *sleep_lines("2010-11-05 23:00:00.0", "2010-11-06 07:00:00.0"),
    )
    assert len(episodes) == 2


def test_merge_gap_is_configurable(tmp_path):
    lines = (
        *sleep_lines("2010-11-05 00:00:00.0", "2010-11-05 01:00:00.0"),
        *sleep_lines("2010-11-05 01:10:00.0", "2010-11-05 07:00:00.0"),
    )
    assert len(episodes_of(tmp_path, *lines, merge_gap_min=15)) == 1
    assert len(episodes_of(tmp_path, *lines, merge_gap_min=5)) == 2


# ============================================================ 밤 / 날짜 경계


def test_night_date_boundary(tmp_path):
    """23:50 과 00:10 취침은 같은 밤(전날)으로 묶인다."""
    path = write(
        tmp_path,
        *sleep_lines("2010-11-04 23:50:00.0", "2010-11-05 06:00:00.0"),
        *sleep_lines("2010-11-06 00:10:00.0", "2010-11-06 07:00:00.0"),
    )
    s = extract_sleep(read_events(path))
    assert [(x.date, hhmm(x)) for x in s.sleep_time] == [
        ("2010-11-04", "23:50"),
        ("2010-11-05", "00:10"),
    ]
    assert [(x.date, hhmm(x)) for x in s.wake_time] == [
        ("2010-11-05", "06:00"),
        ("2010-11-06", "07:00"),
    ]


def test_nap_is_not_main_sleep(tmp_path):
    path = write(
        tmp_path,
        *sleep_lines("2010-11-05 14:00:00.0", "2010-11-05 15:30:00.0"),     # 낮잠
        *sleep_lines("2010-11-05 22:30:00.0", "2010-11-06 06:30:00.0"),
    )
    s = extract_sleep(read_events(path))
    assert len(s.episodes) == 2
    assert len(s.naps) == 1 and s.naps[0].start.hour == 14
    assert [hhmm(x) for x in s.sleep_time] == ["22:30"]
    assert [hhmm(x) for x in s.wake_time] == ["06:30"]


def test_truncated_last_night_has_no_wake(tmp_path):
    """원본 마지막 Sleeping end 는 파일 마지막 줄(기록 종료)에 붙어 있다."""
    path = write(
        tmp_path,
        *sleep_lines("2010-11-04 23:00:00.0", "2010-11-05 06:00:00.0"),
        *sleep_lines("2010-11-05 22:10:00.0", "2010-11-05 23:58:00.0"),
        tail=False,
    )
    s = extract_sleep(read_events(path))
    assert [hhmm(x) for x in s.sleep_time] == ["23:00", "22:10"]
    assert [hhmm(x) for x in s.wake_time] == ["06:00"]


def test_sleep_samples_are_marked_as_proxy(tmp_path):
    path = write(tmp_path, *sleep_lines("2010-11-04 23:00:00.0", "2010-11-05 06:00:00.0"))
    s = extract_sleep(read_events(path))
    assert all(x.proxy for x in s.sleep_time + s.wake_time)
    assert {x.distribution for x in s.sleep_time} == {"sleep_time"}
    assert {x.distribution for x in s.wake_time} == {"wake_time"}


# ============================================================ 식사


def test_meal_label_begins_uses_begin_only(tmp_path):
    """이전 방식 — 라벨 begin 전부 (회귀 비교용)."""
    path = write(
        tmp_path,
        label("2010-11-04 08:11:09.966157", "Meal_Preparation", "begin"),
        label("2010-11-04 08:30:00.000000", "Meal_Preparation", "end"),
        label("2010-11-04 18:00:00", "Meal_Preparation", "begin"),
    )
    meals = meal_label_begins(read_events(path))
    assert [(m.date, hhmm(m)) for m in meals] == [("2010-11-04", "08:11"), ("2010-11-04", "18:00")]
    assert meals[0].value == pytest.approx(8 * 60 + 11 + 9.966157 / 60)
    assert not any(m.proxy for m in meals)
    assert {(m.source, m.prompted) for m in meals} == {("aruba", False)}


def prep(begin, end):
    return [label(begin, "Meal_Preparation", "begin"), label(end, "Meal_Preparation", "end")]


def eat(begin, end):
    return [label(begin, "Eating", "begin"), label(end, "Eating", "end")]


def sessions_of(tmp_path, *lines):
    return meal_sessions(read_events(write(tmp_path, *lines)).events)


def test_cook_then_eat_is_one_meal(tmp_path):
    """조리 → 10분 안에 먹기 = 한 끼. t0 = 조리 시작, eat_t0 = 먹기 시작."""
    [m] = sessions_of(
        tmp_path,
        *prep("2010-11-04 09:20:00.0", "2010-11-04 09:35:00.0"),
        *eat("2010-11-04 09:40:00.0", "2010-11-04 10:00:00.0"),
    )
    assert (m.start.strftime("%H:%M"), m.eat_start.strftime("%H:%M"), m.end.strftime("%H:%M")) == ("09:20", "09:40", "10:00")


def test_fragmented_labels_are_one_meal(tmp_path):
    """CASAS 는 한 끼에 조리 라벨이 여러 번 찍힌다 — 10분 안에 이어지면 한 끼."""
    sessions = sessions_of(
        tmp_path,
        *prep("2010-11-04 18:00:00.0", "2010-11-04 18:02:00.0"),
        *prep("2010-11-04 18:05:00.0", "2010-11-04 18:07:00.0"),
        *prep("2010-11-04 18:15:00.0", "2010-11-04 18:20:00.0"),
    )
    assert len(sessions) == 1 and sessions[0].eat_start is None


def test_eat_only_meal(tmp_path):
    """시리얼처럼 조리 없이 먹은 끼니도 식사다 — t0 == eat_t0."""
    [m] = sessions_of(tmp_path, *eat("2010-11-04 08:10:00.0", "2010-11-04 08:25:00.0"))
    assert m.start == m.eat_start


def test_separate_meals_and_short_ones(tmp_path):
    sessions = sessions_of(
        tmp_path,
        *prep("2010-11-04 08:00:00.0", "2010-11-04 08:10:00.0"),
        *prep("2010-11-04 08:30:00.0", "2010-11-04 08:31:00.0"),     # 20분 뒤 1분 — 새 묶음, 2분 미만이라 버림
        *prep("2010-11-04 12:00:00.0", "2010-11-04 12:20:00.0"),
    )
    assert [m.start.strftime("%H:%M") for m in sessions] == ["08:00", "12:00"]


def test_session_timeout_starts_new_meal(tmp_path):
    lines = []
    for h in range(9, 13):                                           # 9~12시 5분마다 이어지는 조리
        for m in range(0, 60, 5):
            lines += prep(f"2010-11-04 {h:02d}:{m:02d}:00.0", f"2010-11-04 {h:02d}:{m:02d}:30.0")
    sessions = sessions_of(tmp_path, *lines)
    assert len(sessions) >= 2
    assert all((s.end - s.start).total_seconds() <= 7200 for s in sessions)


def test_extract_meal_sources(tmp_path):
    path = write(
        tmp_path,
        *prep("2010-11-04 09:20:00.0", "2010-11-04 09:35:00.0"),
        *eat("2010-11-04 09:40:00.0", "2010-11-04 10:00:00.0"),
        *prep("2010-11-04 18:00:00.0", "2010-11-04 18:30:00.0"),     # 먹기 라벨 없음 → eat_t0 모름
    )
    log = read_events(path)
    assert [hhmm(m) for m in extract_meal(log)] == ["09:20", "18:00"]
    assert [hhmm(m) for m in extract_meal(log, "eat_t0")] == ["09:40"]
    with pytest.raises(ValueError):
        extract_meal(log, "eaten")


def test_meal_records_omit_unknown_eat_t0(tmp_path):
    """먹기 시각을 모르는 묶음은 eat_t0 필드를 넣지 않는다 (null = 안 먹음과 구별)."""
    sessions = sessions_of(
        tmp_path,
        *prep("2010-11-04 09:20:00.0", "2010-11-04 09:35:00.0"),
        *eat("2010-11-04 09:40:00.0", "2010-11-04 10:00:00.0"),
        *prep("2010-11-04 18:00:00.0", "2010-11-04 18:30:00.0"),
    )
    records = meal_records(sessions, "aruba")
    assert records[0]["eat_t0"] - records[0]["t0"] == 20 * 60
    assert records[0]["duration_sec"] == 40 * 60
    assert "eat_t0" not in records[1]
    assert {r["type"] for r in records} == {"meal"}


def test_extract_samples_has_three_series(tmp_path):
    path = write(
        tmp_path,
        *sleep_lines("2010-11-04 23:00:00.0", "2010-11-05 06:00:00.0"),
        *prep("2010-11-05 07:00:00.0", "2010-11-05 07:20:00.0"),
    )
    samples = extract_samples(path)
    assert {k: len(v) for k, v in samples.items()} == {"meal_time": 1, "sleep_time": 1, "wake_time": 1}


# ============================================================ 실제 Aruba 원본


def read_csv_ts(row) -> datetime:
    # 원본 소수점 자릿수가 줄마다 달라(.95895 등) 문자열이 아니라 시각으로 비교한다
    return parse_ts(row["date"], row["time"])


@pytest.fixture(scope="module")
def raw_log():
    return read_events(RAW)


@needs_raw
def test_real_labels_exist(raw_log):
    counts = count_labels(raw_log.events)
    assert counts[("Meal_Preparation", "begin")] == 1606
    assert counts[("Sleeping", "begin")] == counts[("Sleeping", "end")] == 401
    assert counts[("Bed_to_Toilet", "begin")] == 157


@needs_raw
def test_real_meal_matches_existing_parser(raw_log):
    """scripts/datasets/parse_aruba.py 결과(meal_preparation.csv)와 같은 집합."""
    with open(PROCESSED / "meal_preparation.csv", encoding="utf-8") as f:
        expected = sorted((r["date"], clock_minutes(read_csv_ts(r))) for r in csv.DictReader(f))

    actual = sorted((m.date, m.value) for m in meal_label_begins(raw_log))
    assert [d for d, _ in actual] == [d for d, _ in expected]
    assert [v for _, v in actual] == pytest.approx([v for _, v in expected], abs=1e-9)


@needs_raw
def test_real_meal_reproduces_breakfast(raw_log):
    """05:00~11:00 날짜별 첫 이벤트 → 기존 breakfast_preparation.csv 212일."""
    with open(PROCESSED / "breakfast_preparation.csv", encoding="utf-8") as f:
        expected = sorted((r["date"], clock_minutes(read_csv_ts(r))) for r in csv.DictReader(f))

    first = {}
    for m in sorted(meal_label_begins(raw_log), key=lambda m: (m.date, m.value)):
        if 5 <= hour(m) < 11:
            first.setdefault(m.date, m.value)
    actual = sorted(first.items())
    assert [d for d, _ in actual] == [d for d, _ in expected]
    assert [v for _, v in actual] == pytest.approx([v for _, v in expected], abs=1e-9)


@needs_raw
def test_real_sleep_wake_proxy(raw_log):
    s = extract_sleep(raw_log)
    assert s.dropped_segments == 1                  # 2011-05-23 시각 역전
    assert len(s.sleep_time) == 220
    assert len(s.wake_time) == 219                  # 마지막 밤은 기록 종료로 잘림
    # 취침은 20~03시, 기상은 03~10시 안에 있어야 한다
    assert all(hour(x) >= 20 or hour(x) <= 3 for x in s.sleep_time)
    assert all(3 <= hour(x) <= 10 for x in s.wake_time)
