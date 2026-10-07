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
    build_training_input,
    fit_kde,
    group_by_distribution,
    load_aruba_samples,
    load_t0_jsonl,
    parse_t0_line,
    sample_values,
    t0_to_minutes,
    time_to_minutes,
)


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


# ============================================================ type 분리


def test_type_maps_to_distribution():
    groups = group_by_distribution([
        sample(type="wake"),
        sample(type="meal"),
        sample(type="sleep"),
        sample(type="hydration"),
    ])
    assert set(groups) == {"wake_time", "meal_time", "sleep_time", "hydration_lag"}


def test_unknown_type_is_excluded_from_kde():
    """복약 등 KDE 대상이 아닌 type 은 파싱은 되지만 분포에는 들어가지 않는다."""
    groups = group_by_distribution([sample(type="medication"), sample(type="meal")])
    assert list(groups) == ["meal_time"]
    assert len(groups["meal_time"]) == 1


def test_prompted_sample_is_kept():
    """Phase 1 은 보존만 한다. 감쇠(Phase 10)도 삭제는 하지 않는다."""
    groups = group_by_distribution([sample(prompted=True), sample(prompted=False)])
    assert [s.prompted for s in groups["meal_time"]] == [True, False]


def test_source_and_duration_are_kept():
    groups = group_by_distribution([sample(source="diary", duration_sec=42.0)])
    s = groups["meal_time"][0]
    assert (s.source, s.duration_sec) == ("diary", 42.0)


# ============================================================ KDE 입력값


def test_t0_is_converted_to_kst_minutes():
    assert t0_to_minutes(MEAL["t0"]) == 9 * 60 + 20
    assert t0_to_minutes(WAKE["t0"]) == 9 * 60


def test_hydration_lag_uses_duration_not_t0():
    values = sample_values("hydration_lag", [parse_t0_line(json.dumps(HYDRATION))])
    assert values.tolist() == [11.0]       # 660초 = 기상 후 11분


def test_build_training_input_from_jsonl_only(tmp_path):
    """완료 조건: t0 JSONL 만으로 KDE 학습 입력을 만들 수 있다."""
    path = write_jsonl(tmp_path / "t0.jsonl", [MEAL, WAKE, HYDRATION])
    inputs = build_training_input(load_t0_jsonl(path))
    assert {k: v.tolist() for k, v in inputs.items()} == {
        "meal_time": [560.0],
        "wake_time": [540.0],
        "hydration_lag": [11.0],
    }


def test_build_model_from_t0_samples():
    days = [
        sample(date=f"2026-09-{d:02d}", t0=MEAL["t0"] + (d - 25) * 86400 + (d % 3) * 600)
        for d in range(1, 29)
    ]
    model = build_model(days + [sample(type="wake")])
    meal = model["distributions"]["meal_time"]
    assert model["sample_days"] == 28
    assert len(meal["density"]) == 96
    assert sum(meal["density"]) == pytest.approx(1.0)
    # 09:20~09:40 근처에 몰려 있어야 한다
    assert int(np.argmax(meal["density"])) in range(36, 39)


def test_build_model_without_enough_meal_samples_fails():
    with pytest.raises(ValueError):
        build_model([sample(type="wake"), sample()])


# ============================================================ Aruba 호환


def test_aruba_goes_through_same_pipeline():
    samples = load_aruba_samples(ARUBA)
    assert len(samples) == 212
    assert {s.type for s in samples} == {"meal"}
    assert {s.source for s in samples} == {"aruba"}
    assert not any(s.prompted for s in samples)


def test_aruba_minutes_are_unchanged():
    """CSV 시각 → KST epoch → 자정 기준 분 왕복이 원래 시각과 같아야 한다."""
    rows = ARUBA.read_text(encoding="utf-8").splitlines()[1:]
    expected = [time_to_minutes(r.split(",")[1]) for r in rows]
    actual = sample_values("meal_time", load_aruba_samples(ARUBA)).tolist()
    assert actual == pytest.approx(expected, abs=1e-6)


def test_aruba_model_regression():
    """기존(Phase 1 이전) build_model 결과와 같아야 한다."""
    rows = ARUBA.read_text(encoding="utf-8").splitlines()[1:]
    legacy = build_density(fit_kde(np.array([time_to_minutes(r.split(",")[1]) for r in rows])))

    model = build_model(load_aruba_samples(ARUBA))
    assert model["sample_days"] == 212
    assert model["distributions"]["meal_time"]["density"] == pytest.approx(legacy, abs=1e-12)


# ============================================================ Context Engine 계약


@pytest.mark.skipif(sys.version_info < (3, 11), reason="context-engine 은 Python 3.11+")
def test_context_engine_t0_log_is_readable(tmp_path):
    """Context Engine 의 FileT0Log 가 쓴 파일을 그대로 읽을 수 있어야 한다."""
    sys.path.insert(0, str(CONTEXT_ENGINE))
    try:
        from hestia_engine.fsm import FileT0Log, T0Entry
        from hestia_engine.replay import replay_file
    finally:
        sys.path.remove(str(CONTEXT_ENGINE))

    _, _, _, memory_log = replay_file(CONTEXT_ENGINE / "tests" / "data" / "morning.jsonl", echo=False)
    assert memory_log.entries

    path = tmp_path / "t0_log.jsonl"
    file_log = FileT0Log(path)
    for entry in memory_log.entries:
        file_log.write(entry)

    samples = load_t0_jsonl(path)
    assert [asdict(s) for s in samples] == [asdict(e) for e in memory_log.entries]

    inputs = build_training_input(samples)
    assert "wake_time" in inputs
    assert T0Entry.__dataclass_fields__.keys() == T0Sample.__dataclass_fields__.keys()
