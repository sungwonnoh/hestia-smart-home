"""v2 Phase 5 — 4개 distribution 공통 KDE."""

import math
from pathlib import Path

import numpy as np
import pytest

from aruba import extract_samples
from baseline import (
    HYDRATION_GRID_MAX_MIN,
    InsufficientSamples,
    build_density,
    build_model,
    calculate_predictability,
    fit_distribution,
    fit_kde,
    grid_for,
)
from samples import KdeSample
from synthetic import generate_hydration_lag


RAW = Path(__file__).resolve().parents[3] / "data" / "raw" / "casas" / "aruba" / "aruba.txt"


def around(distribution, center, spread, n=60, seed=0, date_offset=0):
    """center 근처 정규분포 표본 (time_of_day 는 하루 안으로 접는다)."""
    rng = np.random.default_rng(seed)
    out = []
    for i, v in enumerate(rng.normal(center, spread, n)):
        if distribution != "hydration_lag":
            v = v % 1440
        out.append(KdeSample(distribution, float(abs(v)), f"2026-{1 + (i + date_offset) // 28:02d}-{1 + (i + date_offset) % 28:02d}", "sensor"))
    return out


def four_distributions():
    return (
        around("wake_time", 7 * 60, 20, seed=1)
        + around("sleep_time", 22 * 60, 20, seed=2)
        + around("meal_time", 12 * 60, 60, seed=3)
        + generate_hydration_lag(60, 15, 5, seed=4, max_min=120)
    )


# ============================================================ 격자


@pytest.mark.parametrize("name", ["wake_time", "sleep_time", "meal_time"])
def test_time_of_day_grid(name):
    g = grid_for(name)
    assert (g.grid_min, g.grid_step, g.size) == (0, 15, 96)
    assert g.centers()[0] == 7.5
    assert g.centers()[-1] == 1440 - 7.5


def test_hydration_grid():
    """1440/grid_step 이 아니다 — 기상 후 경과 분 격자."""
    g = grid_for("hydration_lag")
    assert (g.grid_min, g.grid_step) == (0, 5)
    assert g.size == HYDRATION_GRID_MAX_MIN // 5 == 24
    assert grid_for("hydration_lag", hydration_max_min=60).size == 12


# ============================================================ fitting


@pytest.mark.parametrize("name", ["wake_time", "sleep_time", "meal_time", "hydration_lag"])
def test_each_distribution_density_sums_to_one(name):
    items = [s for s in four_distributions() if s.distribution == name]
    grid, density = fit_distribution(name, np.array([s.value for s in items]))
    assert len(density) == grid.size
    assert sum(density) == pytest.approx(1.0)
    assert min(density) >= 0


def test_density_peaks_near_data():
    _, density = fit_distribution("wake_time", np.array([s.value for s in around("wake_time", 7 * 60, 20)]))
    assert int(np.argmax(density)) in (27, 28)          # 06:45~07:15


def test_hydration_density_peaks_near_mean():
    values = np.array([s.value for s in generate_hydration_lag(200, 15, 5, seed=1, max_min=120)])
    _, density = fit_distribution("hydration_lag", values)
    assert int(np.argmax(density)) in (2, 3)              # 10~20분


@pytest.mark.parametrize("values", [[], [420.0]])
def test_too_few_samples(values):
    with pytest.raises(InsufficientSamples, match="부족"):
        fit_kde(np.array(values))


def test_identical_values_cannot_fit():
    with pytest.raises(InsufficientSamples, match="분산"):
        fit_kde(np.array([420.0, 420.0, 420.0]))


@pytest.mark.parametrize("bad", [math.nan, math.inf])
def test_nan_inf_rejected(bad):
    with pytest.raises(ValueError, match="NaN"):
        fit_kde(np.array([400.0, 420.0, bad]))


def test_samples_far_outside_grid():
    """격자 안 density 가 0 이면 정규화할 수 없다."""
    with pytest.raises(InsufficientSamples, match="합이 0"):
        fit_distribution("hydration_lag", np.array([900.0, 905.0, 910.0]))


def test_build_density_default_is_time_of_day():
    density = build_density(fit_kde(np.array([400.0, 420.0, 440.0])))
    assert len(density) == 96


# ============================================================ predictability


def test_predictability_uses_grid_size():
    """H_max 는 그 격자의 칸 수 기준 — hydration 24칸도 0~1 이다."""
    assert calculate_predictability([1 / 24] * 24) == pytest.approx(0.0)
    assert calculate_predictability([1 / 96] * 96) == pytest.approx(0.0)
    assert calculate_predictability([1.0] + [0.0] * 23) == pytest.approx(1.0)


# ============================================================ build_model


def test_build_model_four_distributions():
    model = build_model(four_distributions())
    assert list(model["distributions"]) == ["wake_time", "sleep_time", "meal_time", "hydration_lag"]
    assert set(model["predictability"]) == set(model["distributions"])
    assert model["skipped"] == {}
    assert model["distributions"]["hydration_lag"]["grid_step"] == 5
    assert len(model["distributions"]["hydration_lag"]["density"]) == 24
    for name, dist in model["distributions"].items():
        assert sum(dist["density"]) == pytest.approx(1.0), name
        assert 0 <= model["predictability"][name] <= 1


def test_build_model_metadata():
    model = build_model(four_distributions())
    assert model["meta"]["hydration_lag"] == {
        "samples": 60, "sample_days": 60, "sources": ["synthetic"], "proxy": False,
    }
    assert model["meta"]["wake_time"]["sources"] == ["sensor"]


def test_missing_distribution_is_skipped_not_empty():
    """빈 density 를 보내면 Context Engine 이 payload 전체를 거부한다 — 키를 뺀다."""
    model = build_model(around("meal_time", 12 * 60, 60) + around("wake_time", 420, 20)[:1])
    assert list(model["distributions"]) == ["meal_time"]
    assert model["skipped"]["sleep_time"] == "표본 없음"
    assert "부족" in model["skipped"]["wake_time"]
    assert "wake_time" not in model["predictability"]


def test_no_trainable_distribution_raises():
    with pytest.raises(InsufficientSamples):
        build_model(around("wake_time", 420, 20)[:1])


def test_sample_days_counts_used_samples_only():
    """학습에서 빠진 distribution 의 날짜는 세지 않는다."""
    meal = around("meal_time", 720, 60, n=10)
    lonely = around("wake_time", 420, 20, n=1, date_offset=100)
    assert build_model(meal + lonely)["sample_days"] == 10


# ============================================================ Gate A / B


def test_gate_b_regular_vs_irregular_hydration():
    """Gate B: regular / irregular 에 따라 density 와 predictability 가 달라진다."""
    regular = build_model(generate_hydration_lag(120, 15, 5, seed=42, max_min=120))
    irregular = build_model(generate_hydration_lag(120, 15, 30, seed=42, max_min=120))
    p_regular = regular["predictability"]["hydration_lag"]
    p_irregular = irregular["predictability"]["hydration_lag"]
    assert p_regular > p_irregular
    assert max(regular["distributions"]["hydration_lag"]["density"]) > max(
        irregular["distributions"]["hydration_lag"]["density"]
    )


@pytest.mark.skipif(not RAW.exists(), reason="Aruba 원본 없음 (data/raw 는 gitignore)")
def test_gate_a_aruba_meal_sleep_wake():
    """Gate A: Aruba 원본 → meal / sleep(proxy) / wake(proxy) KDE"""
    samples = [s for series in extract_samples(RAW).values() for s in series]
    model = build_model(samples)
    assert list(model["distributions"]) == ["wake_time", "sleep_time", "meal_time"]
    assert model["skipped"] == {"hydration_lag": "표본 없음"}
    assert model["meta"]["sleep_time"]["proxy"] is True
    assert model["meta"]["meal_time"]["samples"] == 1606
