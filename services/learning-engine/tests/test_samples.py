import json
import math

import numpy as np
import pytest

import samples
from aruba import extract_samples
from baseline import fit_kde, parse_t0_line, t0_to_kde_samples
from samples import KdeSample, build_training_input, group, is_time_of_day, sample_days, values
from synthetic import generate_hydration_lag


def k(distribution="meal_time", value=560.0, date="2026-09-25", **kw) -> KdeSample:
    return KdeSample(distribution, value, date, kw.pop("source", "sensor"), **kw)


# ============================================================ 종류


def test_four_distributions_and_kinds():
    assert set(samples.DISTRIBUTION_KIND) == {"wake_time", "sleep_time", "meal_time", "hydration_lag"}
    assert [is_time_of_day(n) for n in ("wake_time", "sleep_time", "meal_time")] == [True] * 3
    assert is_time_of_day("hydration_lag") is False


# ============================================================ 검증


def test_defaults():
    s = k()
    assert (s.prompted, s.proxy) == (False, False)


def test_unknown_distribution_is_rejected():
    with pytest.raises(ValueError, match="distribution"):
        k(distribution="medication_time")


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf, None, "560", True])
def test_invalid_value_is_rejected(value):
    with pytest.raises(ValueError):
        k(value=value)


@pytest.mark.parametrize("value", [0.0, 1439.999])
def test_time_of_day_range_accepts(value):
    assert k(value=value).value == value


@pytest.mark.parametrize("value", [-0.001, 1440.0, 1500.0])
def test_time_of_day_range_rejects(value):
    with pytest.raises(ValueError, match="1440"):
        k(value=value)


def test_elapsed_is_not_bounded_by_day():
    """hydration_lag 은 하루 시각이 아니다 — 1440 제한을 적용하지 않는다."""
    assert k("hydration_lag", 2000.0).value == 2000.0
    assert k("hydration_lag", 0.0).value == 0.0


def test_negative_elapsed_is_rejected():
    with pytest.raises(ValueError, match="0 이상"):
        k("hydration_lag", -1.0)


# ============================================================ 묶기


def test_group_keeps_order():
    items = [k("meal_time", 1.0), k("wake_time", 2.0), k("meal_time", 3.0)]
    g = group(items)
    assert list(g) == ["meal_time", "wake_time"]
    assert [s.value for s in g["meal_time"]] == [1.0, 3.0]


def test_values_and_sample_days():
    items = [k(value=1.0, date="2026-09-25"), k(value=2.0, date="2026-09-25"), k(value=3.0, date="2026-09-26")]
    assert values(items).tolist() == [1.0, 2.0, 3.0]
    assert values([]).shape == (0,)
    assert sample_days(items) == 2


# ============================================================ 출처 무관


def test_all_sources_feed_the_same_fit(tmp_path):
    """Aruba / synthetic / t0 가 같은 KdeSample 로 모여 같은 fit_kde 를 탄다."""
    aruba_txt = tmp_path / "aruba.txt"
    lines = []
    for day in range(4, 9):
        lines += [
            f"2010-11-{day:02d} 22:{day * 5:02d}:00.0\tM003\tON\tSleeping\tbegin",
            f"2010-11-{day + 1:02d} 06:{day * 5:02d}:00.0\tM003\tOFF\tSleeping\tend",
            f"2010-11-{day + 1:02d} 07:{day * 5:02d}:00.0\tM018\tON\tMeal_Preparation\tbegin",
        ]
    aruba_txt.write_text("\n".join(lines + ["2010-11-10 00:00:00.0\tT002\t21"]) + "\n", encoding="utf-8")
    from_aruba = [s for series in extract_samples(aruba_txt).values() for s in series]

    from_synthetic = generate_hydration_lag(30, 15, 5, seed=42)

    t0_lines = [
        {"date": f"2026-09-{d:02d}", "type": "wake", "t0": 1790294400.0 + (d - 25) * 86400 + d * 60,
         "source": "sensor", "prompted": False, "duration_sec": 0.0}
        for d in range(20, 26)
    ]
    from_t0 = t0_to_kde_samples([parse_t0_line(json.dumps(x)) for x in t0_lines])

    everything = from_aruba + from_synthetic + from_t0
    assert all(isinstance(s, KdeSample) for s in everything)

    inputs = build_training_input(everything)
    assert set(inputs) == {"meal_time", "sleep_time", "wake_time", "hydration_lag"}
    assert len(inputs["wake_time"]) == 5 + 6          # aruba proxy + t0

    for name, arr in inputs.items():
        kde = fit_kde(arr)
        assert np.isfinite(kde(arr)).all(), name


def test_sources_are_marked():
    from_synthetic = generate_hydration_lag(3, 15, 5, seed=1)
    assert {(s.source, s.proxy, s.prompted) for s in from_synthetic} == {("synthetic", False, False)}
