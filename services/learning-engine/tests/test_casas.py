"""여러 CASAS 데이터셋 (Aruba / Milan / Tulum2 / Cairo) 거주자별 sleep / wake proxy."""

from pathlib import Path

import pytest

import casas
from aruba import read_events
from casas import EVENT, SEGMENT, SPECS, SleepSpec, event_sessions, export_jsonl, extract_all, extract_person
from sleep_sessions import NIGHT, load_records, samples_from_records


RAW_DIR = Path(__file__).resolve().parents[3] / "data" / "raw" / "casas" / "aruba"


def write(tmp_path, name, lines):
    path = tmp_path / name
    path.write_text("\n".join(lines + ["2099-01-01 00:00:00.0\tT001\t20"]) + "\n", encoding="utf-8")
    return path


def lab(ts, label, kind, sep=" "):
    date, time = ts.split(" ")
    return f"{date}{sep}{time}\tM001\tON\t{label} {kind}"


def hhmm(sample):
    m = int(sample.value)
    return f"{m // 60:02d}:{m % 60:02d}"


# ============================================================ 설정


def test_specs_cover_four_datasets():
    assert {s.dataset for s in SPECS} == {"aruba", "milan", "tulum2", "cairo"}
    assert len({s.key for s in SPECS}) == len(SPECS) == 6
    assert {s.mode for s in SPECS} == {SEGMENT, EVENT}


def test_multi_resident_does_not_bridge_on_shared_toilet_label():
    """2인 가구의 화장실 라벨은 누구 것인지 모른다."""
    for spec in SPECS:
        if spec.dataset in ("tulum2", "cairo"):
            assert spec.toilet_label is None


# ============================================================ segment 방식 (Milan / Tulum2)


def test_milan_style_segments(tmp_path):
    path = write(tmp_path, "milan.txt", [
        lab("2009-10-15 13:00:00.000000", "Sleep", "begin"),          # 같은 밤 범위의 낮잠
        lab("2009-10-15 13:40:00.000000", "Sleep", "end"),
        lab("2009-10-16 00:08:50.000081", "Sleep", "begin"),
        lab("2009-10-16 03:55:50.000029", "Sleep", "end"),
        lab("2009-10-16 03:55:53.000080", "Bed_to_Toilet", "begin"),
        lab("2009-10-16 03:58:28.000002", "Bed_to_Toilet", "end"),
        lab("2009-10-16 03:58:44.000068", "Sleep", "begin"),
        lab("2009-10-16 08:40:01.000075", "Sleep", "end"),
    ])
    spec = next(s for s in SPECS if s.dataset == "milan")
    s = extract_person(spec, read_events(path))
    assert [hhmm(x) for x in s.sleep_time] == ["00:08"]
    assert [hhmm(x) for x in s.wake_time] == ["08:40"]
    assert len(s.naps) == 1 and s.naps[0].start.hour == 13
    assert s.main[0].parts == 2
    assert {x.source for x in s.sleep_time} == {"milan"}


def test_tulum2_style_two_residents(tmp_path):
    """날짜와 시각이 탭으로 나뉜 형식, 두 거주자 라벨이 섞여 있다."""
    path = write(tmp_path, "tulum2.txt", [
        lab("2009-09-25 23:35:20.013314", "R1_Sleeping_in_Bed", "begin", sep="\t"),
        lab("2009-09-26 01:20:05.044135", "R2_Sleeping_in_Bed", "begin", sep="\t"),
        lab("2009-09-26 07:50:00.000000", "R1_Sleeping_in_Bed", "end", sep="\t"),
        lab("2009-09-26 09:10:00.000000", "R2_Sleeping_in_Bed", "end", sep="\t"),
    ])
    log = read_events(path)
    r1, r2 = (extract_person(s, log) for s in SPECS if s.dataset == "tulum2")
    assert ([hhmm(x) for x in r1.sleep_time], [hhmm(x) for x in r1.wake_time]) == (["23:35"], ["07:50"])
    assert ([hhmm(x) for x in r2.sleep_time], [hhmm(x) for x in r2.wake_time]) == (["01:20"], ["09:10"])


def test_implausible_long_segment_is_dropped(tmp_path):
    path = write(tmp_path, "tulum2.txt", [
        lab("2009-10-02 00:13:48.084481", "R1_Sleeping_in_Bed", "begin", sep="\t"),
        lab("2009-10-03 08:24:11.024184", "R1_Sleeping_in_Bed", "end", sep="\t"),
    ])
    spec = next(s for s in SPECS if s.key == "tulum2/R1")
    s = extract_person(spec, read_events(path))
    assert s.sleep_time == [] and len(s.implausible) == 1


# ============================================================ event 방식 (Cairo)


def cairo_lines():
    return [
        lab("2009-06-10 05:46:00.080752", "R1_Wake", "begin"),        # 앞선 취침 없음 → 버림
        lab("2009-06-10 05:51:39.024456", "R1_Wake", "end"),
        lab("2009-06-10 20:54:09.002574", "R1_Sleep", "begin"),
        lab("2009-06-10 21:21:20.060191", "R1_Sleep", "end"),
        lab("2009-06-11 05:29:15.024179", "R1_Wake", "begin"),
        lab("2009-06-11 05:46:06.002588", "R1_Wake", "end"),
        lab("2009-06-11 21:52:34.016084", "R1_Sleep", "begin"),       # 기상 기록 없이 끝남
        lab("2009-06-11 22:12:27.023479", "R1_Sleep", "end"),
    ]


def test_cairo_sleep_activity_to_next_wake(tmp_path):
    spec = next(s for s in SPECS if s.key == "cairo/R1")
    s = extract_person(spec, read_events(write(tmp_path, "cairo.txt", cairo_lines())))
    assert [hhmm(x) for x in s.sleep_time] == ["20:54", "21:52"]
    assert [hhmm(x) for x in s.wake_time] == ["05:29"]
    assert s.dropped_segments == 1
    assert s.main[0].minutes == pytest.approx(8 * 60 + 35, abs=1)


def test_event_sessions_repeated_bedtime():
    """기상 없이 취침이 두 번이면 앞의 것은 길이 0 — 같은 밤의 진짜 수면에 밀린다."""
    from aruba import ActivityEvent, parse_ts

    def ev(ts, label):
        return ActivityEvent(parse_ts(*ts.split()), label, "begin", 0)

    sessions, dropped = event_sessions(
        [ev("2009-06-10 20:00:00", "R1_Sleep"), ev("2009-06-10 21:30:00", "R1_Sleep"),
         ev("2009-06-11 06:00:00", "R1_Wake")],
        "R1_Sleep", "R1_Wake",
    )
    assert dropped == 0
    assert [(s.minutes, s.wake_observed) for s in sessions] == [(0, False), (510, True)]


def test_unknown_mode(tmp_path):
    log = read_events(write(tmp_path, "x.txt", []))
    with pytest.raises(ValueError):
        extract_person(SleepSpec("x", "R1", "x.txt", "weird", "Sleep"), log)


# ============================================================ 내보내기


def test_export_and_relearn(tmp_path):
    raw = tmp_path / "raw"
    raw.mkdir()
    write(raw, "cairo.txt", cairo_lines())
    [path] = [p for p in export_jsonl(tmp_path / "out", raw) if "cairo_R1" in p.name]
    series, classified = samples_from_records(load_records(path))
    assert [c.kind for c in classified] == [NIGHT, NIGHT]
    assert [hhmm(x) for x in series["sleep_time"]] == ["20:54", "21:52"]
    assert [hhmm(x) for x in series["wake_time"]] == ["05:29"]


def test_missing_raw_files_are_skipped(tmp_path):
    assert extract_all(tmp_path) == []


# ============================================================ 실제 원본


needs_raw = pytest.mark.skipif(
    not all((RAW_DIR / f).exists() for f in ("aruba.txt", "milan.txt", "tulum2.txt", "cairo.txt")),
    reason="CASAS 원본 없음 (data/raw 는 gitignore)",
)


@pytest.fixture(scope="module")
def real():
    return {spec.key: (spec, s) for spec, s in extract_all(RAW_DIR)}


@needs_raw
def test_real_counts(real):
    nights = {k: len(s.main) for k, (_, s) in real.items()}
    assert nights == {
        "aruba/R1": 220, "milan/R1": 56, "tulum2/R1": 126, "tulum2/R2": 127, "cairo/R1": 50, "cairo/R2": 52,
    }


@needs_raw
def test_real_sessions_are_plausible(real):
    for key, (_, s) in real.items():
        assert all(x.minutes < 24 * 60 for x in s.main), key
        hours = [x.start.hour for x in s.main]
        assert sum(h >= 19 or h <= 3 for h in hours) / len(hours) > 0.95, key


@needs_raw
def test_real_predictability_range(real):
    for key, (spec, s) in real.items():
        r = casas.person_summary(spec, s)
        assert 0.25 < r["sleep_time"] < 0.6, key
        assert 0.25 < r["wake_time"] < 0.6, key
