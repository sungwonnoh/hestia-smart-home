import json
import logging
import sys
from dataclasses import asdict
from pathlib import Path

import numpy as np
import pytest

from baseline import (
    T0Sample,
    build_density,
    build_model,
    fit_kde,
    load_aruba_samples,
    load_t0_jsonl,
    parse_t0_line,
    parse_t0_record,
    t0_to_kde_sample,
    t0_to_kde_samples,
    t0_to_minutes,
    time_to_minutes,
)
from samples import KdeSample, build_training_input, values


REPO = Path(__file__).resolve().parents[3]
ARUBA = REPO / "data" / "processed" / "aruba" / "breakfast_preparation.csv"
CONTEXT_ENGINE = REPO / "services" / "context-engine"

# KDE_REFACTOR_TASKS.md 의 입력 예시 (2026-09-25 KST)
MEAL = {"date": "2026-09-25", "type": "meal", "t0": 1790295600.0,      # 09:20
        "source": "sensor", "prompted": False, "duration_sec": 1500.0}
WAKE = {"date": "2026-09-25", "type": "wake", "t0": 1790294400.0,      # 09:00
        "source": "sensor", "prompted": False, "duration_sec": 0.0}
HYDRATION = {"date": "2026-09-25", "type": "hydration", "t0": 1790295060.0,   # 09:11
             "source": "sensor", "prompted": False, "duration_sec": 660.0}


def write_jsonl(path: Path, lines) -> Path:
    path.write_text(
        "\n".join(x if isinstance(x, str) else json.dumps(x) for x in lines) + "\n",
        encoding="utf-8",
    )
    return path


def sample(**changes) -> T0Sample:
    return T0Sample(**{**MEAL, **changes})


# ============================================================ JSONL 파싱


def test_parse_valid_line_preserves_all_fields():
    s = parse_t0_line(json.dumps({**MEAL, "source": "diary", "prompted": True}))
    assert s == T0Sample("2026-09-25", "meal", 1790295600.0, "diary", True, 1500.0)


def test_load_valid_jsonl(tmp_path):
    path = write_jsonl(tmp_path / "t0.jsonl", [MEAL, WAKE, HYDRATION])
    samples = load_t0_jsonl(path)
    assert [s.type for s in samples] == ["meal", "wake", "hydration"]
    assert samples[2].duration_sec == 660.0


def test_int_t0_is_accepted():
    assert parse_t0_line(json.dumps({**MEAL, "t0": 1790295600})).t0 == 1790295600.0


def test_malformed_json_is_skipped(tmp_path, caplog):
    """로그 한 줄이 깨졌다고 전체 학습이 멈추면 안 된다."""
    path = write_jsonl(tmp_path / "t0.jsonl", [MEAL, '{"date": "2026-09-26", "type"', WAKE])
    with caplog.at_level(logging.WARNING):
        samples = load_t0_jsonl(path)
    assert [s.type for s in samples] == ["meal", "wake"]
    assert "t0.jsonl:2" in caplog.text


def test_blank_lines_are_ignored(tmp_path, caplog):
    path = write_jsonl(tmp_path / "t0.jsonl", [MEAL, "", "   ", WAKE])
    with caplog.at_level(logging.WARNING):
        assert len(load_t0_jsonl(path)) == 2
    assert caplog.text == ""


@pytest.mark.parametrize(
    "field", ["date", "type", "t0", "source", "prompted", "duration_sec"]
)
def test_missing_required_field_is_rejected(field):
    obj = {k: v for k, v in MEAL.items() if k != field}
    with pytest.raises(ValueError, match=field):
        parse_t0_line(json.dumps(obj))


def test_missing_t0_is_skipped(tmp_path, caplog):
    no_t0 = {k: v for k, v in MEAL.items() if k != "t0"}
    path = write_jsonl(tmp_path / "t0.jsonl", [no_t0, WAKE])
    with caplog.at_level(logging.WARNING):
        samples = load_t0_jsonl(path)
    assert [s.type for s in samples] == ["wake"]
    assert "t0" in caplog.text


@pytest.mark.parametrize(
    "changes",
    [
        {"t0": "1790295600"},
        {"t0": True},
        {"t0": None},
        {"prompted": "false"},
        {"prompted": 0},
        {"duration_sec": -1.0},
        {"duration_sec": "660"},
        {"type": 1},
        {"date": None},
    ],
)
def test_wrong_field_type_is_rejected(changes):
    with pytest.raises(ValueError):
        parse_t0_line(json.dumps({**MEAL, **changes}))


def test_non_finite_t0_is_rejected():
    with pytest.raises(ValueError):
        parse_t0_line(json.dumps(MEAL).replace("1790295600.0", "NaN"))


def test_non_object_line_is_rejected():
    with pytest.raises(ValueError):
        parse_t0_line(json.dumps([MEAL]))


# ============================================================ t0 adapter


def test_type_maps_to_distribution():
    converted = t0_to_kde_samples([
        sample(type="wake"),
        sample(type="meal"),
        sample(type="hydration"),
    ])
    assert [k.distribution for k in converted] == ["wake_time", "meal_time", "hydration_lag"]


def test_sleep_t0_is_not_mapped():
    """현재 t0 명세에 sleep type 이 없다. 합의 전에는 학습에 넣지 않는다."""
    assert t0_to_kde_sample(sample(type="sleep")) is None
    assert [k.distribution for k in t0_to_kde_samples([sample(type="sleep"), sample()])] == ["meal_time"]


def test_unknown_type_is_excluded_from_kde(caplog):
    """복약 등 KDE 대상이 아닌 type 은 파싱은 되지만 분포에는 들어가지 않는다."""
    with caplog.at_level(logging.INFO):
        converted = t0_to_kde_samples([sample(type="medication"), sample(type="meal")])
    assert [k.distribution for k in converted] == ["meal_time"]
    assert "medication" in caplog.text


def test_prompted_and_source_are_kept():
    """보존만 한다. 감쇠(Phase 11)도 삭제는 하지 않는다."""
    converted = t0_to_kde_samples([sample(prompted=True, source="diary"), sample()])
    assert [(k.prompted, k.source) for k in converted] == [(True, "diary"), (False, "sensor")]
    assert not any(k.proxy for k in converted)


def test_unconvertible_t0_is_skipped(caplog):
    """parse 는 통과했지만 시각으로 바꿀 수 없는 t0 — 학습 전체가 멈추면 안 된다."""
    with caplog.at_level(logging.WARNING):
        converted = t0_to_kde_samples([sample(t0=1e20), sample()])
    assert len(converted) == 1
    assert "변환 실패" in caplog.text


# ============================================================ KDE 입력값


def test_t0_is_converted_to_kst_minutes():
    assert t0_to_minutes(MEAL["t0"]) == 9 * 60 + 20
    assert t0_to_minutes(WAKE["t0"]) == 9 * 60


def test_hydration_lag_uses_duration_not_t0():
    k = t0_to_kde_sample(parse_t0_line(json.dumps(HYDRATION)))
    assert (k.distribution, k.value) == ("hydration_lag", 11.0)    # 660초 = 기상 후 11분


def test_build_training_input_from_jsonl_only(tmp_path):
    """Gate D: t0 JSONL 만으로 KDE 학습 입력을 만들 수 있다."""
    path = write_jsonl(tmp_path / "t0.jsonl", [MEAL, WAKE, HYDRATION])
    inputs = build_training_input(t0_to_kde_samples(load_t0_jsonl(path)))
    assert {k: v.tolist() for k, v in inputs.items()} == {
        "meal_time": [560.0],
        "wake_time": [540.0],
        "hydration_lag": [11.0],
    }


def t0_meal_days():
    return [
        sample(date=f"2026-09-{d:02d}", t0=MEAL["t0"] + (d - 25) * 86400 + (d % 3) * 600)
        for d in range(1, 29)
    ]


def test_build_model_from_t0_samples():
    model = build_model(t0_to_kde_samples(t0_meal_days() + [sample(type="wake")]))
    meal = model["distributions"]["meal_time"]
    assert model["sample_days"] == 28
    assert len(meal["density"]) == 96
    assert sum(meal["density"]) == pytest.approx(1.0)
    # 09:20~09:40 근처에 몰려 있어야 한다
    assert int(np.argmax(meal["density"])) in range(36, 39)


def test_build_model_does_not_depend_on_source():
    """같은 값이면 출처(t0 / aruba / synthetic)가 달라도 같은 모델이다."""
    from_t0 = t0_to_kde_samples(t0_meal_days())
    relabeled = [
        KdeSample(k.distribution, k.value, k.date, source="aruba")
        for k in from_t0
    ]
    a, b = build_model(from_t0), build_model(relabeled)
    for key in ("sample_days", "distributions", "predictability"):     # payload 에 들어가는 부분
        assert a[key] == b[key]
    assert a["meta"]["meal_time"]["sources"] == ["sensor"]
    assert b["meta"]["meal_time"]["sources"] == ["aruba"]


def test_build_model_without_enough_meal_samples_fails():
    with pytest.raises(ValueError):
        build_model(t0_to_kde_samples([sample(type="wake"), sample()]))


# ============================================================ Aruba 호환


def test_aruba_csv_becomes_common_samples():
    samples = load_aruba_samples(ARUBA)
    assert len(samples) == 212
    assert {s.distribution for s in samples} == {"meal_time"}
    assert {s.source for s in samples} == {"aruba"}
    assert not any(s.prompted for s in samples)


def test_aruba_minutes_are_unchanged():
    rows = ARUBA.read_text(encoding="utf-8").splitlines()[1:]
    expected = [time_to_minutes(r.split(",")[1]) for r in rows]
    assert values(load_aruba_samples(ARUBA)).tolist() == expected


def test_aruba_model_regression():
    """기존(Phase 1 이전) 직선 KDE 결과와 같아야 한다.
       아침 표본은 자정에서 멀어 circular 보정(v2 Phase 6)의 영향이 1e-30 수준이다."""
    rows = ARUBA.read_text(encoding="utf-8").splitlines()[1:]
    legacy = build_density(fit_kde(np.array([time_to_minutes(r.split(",")[1]) for r in rows])))

    model = build_model(load_aruba_samples(ARUBA))
    assert model["sample_days"] == 212
    assert model["distributions"]["meal_time"]["density"] == pytest.approx(legacy, abs=1e-15)


# ============================================================ Context Engine 계약


@pytest.mark.skipif(sys.version_info < (3, 11), reason="context-engine 은 Python 3.11+")
def test_context_engine_t0_log_is_readable(tmp_path):
    """Context Engine 의 FileT0Log 가 쓴 파일을 그대로 읽어 학습까지 할 수 있어야 한다."""
    sys.path.insert(0, str(CONTEXT_ENGINE))
    try:
        from hestia_engine.fsm import FileT0Log, T0Entry
        from hestia_engine.replay import replay_file
    finally:
        sys.path.remove(str(CONTEXT_ENGINE))

    from baseline import load_t0_records, t0_records_to_kde_samples

    _, _, _, memory_log = replay_file(CONTEXT_ENGINE / "tests" / "data" / "morning.jsonl", echo=False)
    assert memory_log.entries

    path = tmp_path / "t0_log.jsonl"
    file_log = FileT0Log(path)
    for entry in memory_log.entries:
        file_log.write(entry)

    # T0Sample 의 필드는 모두 T0Entry 에 있어야 한다 (T0Entry 는 수면 전용 필드가 더 있다)
    core = list(T0Sample.__dataclass_fields__)
    assert set(core) <= set(T0Entry.__dataclass_fields__)

    records = load_t0_records(path)
    assert len(records) == len(memory_log.entries)

    for record, entry in zip(records, memory_log.entries):
        assert asdict(parse_t0_record(record)) == {k: getattr(entry, k) for k in core}
        if entry.type == "sleep_start":
            assert record["area"] == entry.area            # 수면 필드를 잃지 않는다

    # 기상은 Context Engine 이 더 이상 wake 로 남기지 않는다 — sleep_end 에서 온다
    inputs = build_training_input(t0_records_to_kde_samples(records))
    assert {"sleep_time", "wake_time"} <= set(inputs)
