from datetime import date

import numpy as np
import pytest

from synthetic import generate_hydration_lag, truncated_normal


def values(samples) -> np.ndarray:
    return np.array([s.value for s in samples])


# ============================================================ 기본


def test_spec_example():
    samples = generate_hydration_lag(sample_days=60, mean_min=15, std_min=5, seed=42)
    assert len(samples) == 60
    assert {s.distribution for s in samples} == {"hydration_lag"}
    assert values(samples).mean() == pytest.approx(15, abs=2)


def test_one_sample_per_day():
    samples = generate_hydration_lag(3, 15, 5, seed=1, start_date=date(2026, 9, 29))
    assert [s.date for s in samples] == ["2026-09-29", "2026-09-30", "2026-10-01"]


def test_same_seed_is_deterministic():
    a = generate_hydration_lag(60, 15, 5, seed=42)
    b = generate_hydration_lag(60, 15, 5, seed=42)
    assert a == b


def test_different_seed_differs():
    a = generate_hydration_lag(60, 15, 5, seed=42)
    b = generate_hydration_lag(60, 15, 5, seed=43)
    assert a != b


def test_zero_std_is_constant():
    assert set(values(generate_hydration_lag(10, 15, 0, seed=1))) == {15.0}


# ============================================================ 범위


def test_no_negative_lag():
    """평균이 0 근처면 정규분포 절반이 음수다 — 하나도 나오면 안 된다."""
    v = values(generate_hydration_lag(2000, 3, 10, seed=7))
    assert v.min() >= 0


def test_max_limit():
    v = values(generate_hydration_lag(2000, 100, 40, seed=7, max_min=120))
    assert v.max() <= 120


def test_out_of_range_is_redrawn_not_clipped():
    """clip 하면 경계에 표본이 몰려 KDE 에 가짜 봉우리가 생긴다."""
    v = values(generate_hydration_lag(2000, 3, 10, seed=7, max_min=120))
    assert (v == 0).sum() == 0
    assert (v == 120).sum() == 0


def test_impossible_range_raises():
    rng = np.random.default_rng(0)
    with pytest.raises(ValueError, match="범위"):
        truncated_normal(rng, 10, mean=1000, std=1, low=0, high=10)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"sample_days": 0},
        {"sample_days": -1},
        {"sample_days": 1.5},
        {"sample_days": True},
        {"std_min": -1},
        {"mean_min": float("nan")},
        {"std_min": float("inf")},
        {"max_min": 0},
        {"max_min": float("nan")},
    ],
)
def test_invalid_arguments(kwargs):
    args = {"sample_days": 10, "mean_min": 15, "std_min": 5, "seed": 1, **kwargs}
    with pytest.raises(ValueError):
        generate_hydration_lag(**args)


# ============================================================ regular / irregular


def test_regular_vs_irregular_spread():
    regular = values(generate_hydration_lag(200, 15, 5, seed=42, max_min=120))
    irregular = values(generate_hydration_lag(200, 15, 30, seed=42, max_min=120))
    assert regular.std() < irregular.std()
    # regular 은 평균 근처에 모여 있어야 한다
    assert np.mean(np.abs(regular - 15) <= 10) > 0.9
    assert np.mean(np.abs(irregular - 15) <= 10) < 0.6
