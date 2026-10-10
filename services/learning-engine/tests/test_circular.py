"""v2 Phase 6 — circular KDE (wake / sleep / meal)."""

from pathlib import Path

import numpy as np
import pytest

from aruba import extract_samples
from baseline import (
    build_density,
    build_model,
    fit_distribution,
    fit_kde,
    grid_for,
    unwrap_circular,
)


RAW = Path(__file__).resolve().parents[3] / "data" / "raw" / "casas" / "aruba" / "aruba.txt"


def normal_on_clock(center, spread, n=200, seed=0):
    return np.random.default_rng(seed).normal(center, spread, n) % 1440


def linear_density(name, values):
    """circular 이전(v2 Phase 5) 방식 — 비교용"""
    return build_density(fit_kde(values), grid_for(name))


# ============================================================ 펼치기


def test_unwrap_across_midnight():
    """23:50 과 00:10 은 1420분이 아니라 20분 떨어져 있다."""
    assert unwrap_circular(np.array([1430.0, 10.0])).tolist() == [1430.0, 1450.0]


def test_unwrap_keeps_daytime_values():
    assert unwrap_circular(np.array([400.0, 420.0, 440.0])).tolist() == [400.0, 420.0, 440.0]


def test_unwrap_cuts_at_largest_gap():
    """22시~01시 취침은 이어 붙이고, 빈 낮 시간대에서 자른다."""
    out = unwrap_circular(np.array([22 * 60.0, 23 * 60.0, 30.0, 60.0]))
    assert out.tolist() == [1320.0, 1380.0, 1470.0, 1500.0]
    assert np.ptp(out) == 180


# ============================================================ 자정 경계


def test_2350_and_0010_form_one_peak():
    _, density = fit_distribution("sleep_time", np.array([1430.0, 10.0, 1435.0, 5.0]))
    peak = int(np.argmax(density))
    assert peak in (95, 0)
    # 자정 ±30분(4칸)에 대부분의 확률이 있어야 한다
    near = sum(density[-2:]) + sum(density[:2])
    assert near > 0.6


def test_midnight_is_continuous():
    """자정 양쪽 칸이 거의 같아야 한다 — 배열 끝과 처음이 이웃이다."""
    values = normal_on_clock(0, 30)
    _, density = fit_distribution("sleep_time", values)
    assert density[95] == pytest.approx(density[0], rel=0.15)
    assert int(np.argmax(density)) in (94, 95, 0, 1)


def test_linear_kde_breaks_at_midnight():
    """비교: 직선 KDE 는 같은 표본을 양 끝 두 봉우리로 가른다."""
    values = normal_on_clock(0, 30)
    _, circular = fit_distribution("sleep_time", values)
    linear = linear_density("sleep_time", values)
    middle = slice(40, 56)                      # 10:00~14:00 — 표본이 전혀 없는 낮
    assert sum(linear[middle]) > 10 * sum(circular[middle])


def test_rotation_invariance():
    """같은 모양을 하루 중 어디로 옮겨도 density 는 그만큼 회전할 뿐이다."""
    values = normal_on_clock(0, 30, seed=3)
    _, at_midnight = fit_distribution("sleep_time", values)
    _, at_noon = fit_distribution("sleep_time", (values + 720) % 1440)
    assert np.roll(at_midnight, 48).tolist() == pytest.approx(at_noon, abs=1e-12)


# ============================================================ regression


@pytest.mark.parametrize("name", ["wake_time", "meal_time"])
def test_daytime_matches_linear(name):
    """자정에서 먼 낮 데이터는 직선 KDE 결과와 같아야 한다."""
    values = normal_on_clock(12 * 60, 60, seed=5)
    _, circular = fit_distribution(name, values)
    assert circular == pytest.approx(linear_density(name, values), abs=1e-12)


@pytest.mark.parametrize("center", [0, 6 * 60, 23 * 60 + 50])
def test_normalization_kept(center):
    _, density = fit_distribution("wake_time", normal_on_clock(center, 45))
    assert sum(density) == pytest.approx(1.0)
    assert min(density) >= 0


# ============================================================ Aruba


@pytest.mark.skipif(not RAW.exists(), reason="Aruba 원본 없음 (data/raw 는 gitignore)")
def test_aruba_sleep_is_one_peak_around_midnight():
    samples = [s for series in extract_samples(RAW).values() for s in series]
    model = build_model(samples)
    sleep = model["distributions"]["sleep_time"]["density"]
    assert int(np.argmax(sleep)) in (93, 94, 95, 0, 1, 2)
    assert sleep[95] == pytest.approx(sleep[0], rel=0.2)

    linear = linear_density("sleep_time", np.array([s.value for s in samples if s.distribution == "sleep_time"]))
    from baseline import calculate_predictability
    assert model["predictability"]["sleep_time"] > calculate_predictability(linear)
