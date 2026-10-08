"""v2 Phase 11 — recent weighting / prompted attenuation / cold start blending."""

import math
from datetime import date, timedelta

import numpy as np
import pytest

from baseline import build_model, fit_distribution
from kde_fixtures import four_distributions
from model_payload import build_kde_payload, validate_kde_payload
from samples import KdeSample, values
from weighting import (
    ColdStart,
    SampleWeighting,
    blend,
    blend_alpha,
    effective_samples,
    recency_weights,
    sample_ages,
    sample_weights,
)


START = date(2026, 9, 1)


def meal(day, minute, prompted=False, jitter=0.0):
    return KdeSample("meal_time", minute + jitter, (START + timedelta(days=day)).isoformat(), "sensor", prompted)


def drifting_breakfast():
    """명세 예: week1 07:00 → week2 07:10 → week3 07:30 → week4 08:00"""
    rng = np.random.default_rng(0)
    centers = [7 * 60, 7 * 60 + 10, 7 * 60 + 30, 8 * 60]
    return [
        meal(week * 7 + d, centers[week], jitter=float(rng.normal(0, 5)))
        for week in range(4)
        for d in range(7)
    ]


def mean_minute(density):
    """격자 density 의 평균 시각 (자정에서 먼 아침 분포용)"""
    centers = np.arange(96) * 15 + 7.5
    return float(np.dot(centers, density))


def density_of(samples, **weighting):
    model = build_model(samples, sample_weighting=SampleWeighting(**weighting) if weighting else None)
    return model["distributions"]["meal_time"]["density"], model


# ============================================================ 설정


def test_defaults_are_off():
    assert SampleWeighting().enabled is False
    assert sample_weights(drifting_breakfast(), None) is None
    assert sample_weights(drifting_breakfast(), SampleWeighting()) is None


def test_off_is_identical_to_unweighted():
    """꺼져 있으면 기존 결과와 완전히 같다 — 머지해도 동작이 바뀌지 않는다."""
    base = build_model(four_distributions())
    off = build_model(four_distributions(), sample_weighting=SampleWeighting(), cold_start=None)
    assert off["distributions"] == base["distributions"]
    assert off["predictability"] == base["predictability"]


@pytest.mark.parametrize("kwargs", [
    {"recent_lambda": -0.1}, {"recent_lambda": math.nan}, {"recent_lambda": True},
    {"prompted_weight": 0}, {"prompted_weight": 1.5}, {"prompted_weight": -0.2}, {"prompted_weight": math.inf},
])
def test_invalid_weighting(kwargs):
    with pytest.raises(ValueError):
        SampleWeighting(**kwargs)


# ============================================================ recent weighting


def test_sample_age():
    samples = [meal(0, 420), meal(10, 420), meal(27, 420)]
    assert sample_ages(samples).tolist() == [27, 17, 0]
    assert sample_ages(samples, reference=START + timedelta(days=30)).tolist() == [30, 20, 3]
    # 기준일보다 뒤 표본은 0일
    assert sample_ages(samples, reference=START + timedelta(days=5)).tolist() == [5, 0, 0]


def test_exponential_decay():
    w = recency_weights(np.array([0, 1, 10]), 0.1)
    assert w.tolist() == pytest.approx([1.0, math.exp(-0.1), math.exp(-1.0)])


def test_lambda_zero_equals_unweighted():
    plain, _ = density_of(drifting_breakfast())
    zero, _ = density_of(drifting_breakfast(), recent_lambda=0.0)
    assert zero == pytest.approx(plain, abs=1e-12)


def test_recent_shift():
    """recent weighting 을 켜면 density 가 최근 패턴(08:00) 쪽으로 이동한다."""
    plain, _ = density_of(drifting_breakfast())
    mild, _ = density_of(drifting_breakfast(), recent_lambda=0.05)
    strong, _ = density_of(drifting_breakfast(), recent_lambda=0.2)

    assert mean_minute(plain) < mean_minute(mild) < mean_minute(strong)
    assert mean_minute(strong) > 7 * 60 + 40
    assert sum(strong[32:33]) > sum(plain[32:33])           # 08:00~08:15 칸


def test_effective_samples_shrink_with_lambda():
    samples = drifting_breakfast()
    n_mild = effective_samples(sample_weights(samples, SampleWeighting(recent_lambda=0.05)))
    n_strong = effective_samples(sample_weights(samples, SampleWeighting(recent_lambda=0.2)))
    assert len(samples) > n_mild > n_strong
    assert effective_samples(np.ones(10)) == pytest.approx(10)


def test_weighting_meta():
    _, model = density_of(drifting_breakfast(), recent_lambda=0.1)
    w = model["meta"]["meal_time"]["weighting"]
    assert w["recent_lambda"] == 0.1 and w["prompted_weight"] is None
    assert 0 < w["effective_samples"] < 28


# ============================================================ prompted attenuation


def prompted_mix():
    """자연 식사 08:00 / 유도된 식사 10:00"""
    rng = np.random.default_rng(1)
    natural = [meal(d, 8 * 60, jitter=float(rng.normal(0, 10))) for d in range(20)]
    prompted = [meal(d, 10 * 60, prompted=True, jitter=float(rng.normal(0, 10))) for d in range(20)]
    return natural, prompted


def test_prompted_false_is_not_changed():
    natural, _ = prompted_mix()
    plain, _ = density_of(natural)
    attenuated, _ = density_of(natural, prompted_weight=0.2)
    assert attenuated == pytest.approx(plain, abs=1e-12)


def test_prompted_true_is_attenuated_not_removed():
    natural, prompted = prompted_mix()
    full, _ = density_of(natural + prompted)
    attenuated, model = density_of(natural + prompted, prompted_weight=0.2)
    removed, _ = density_of(natural)

    at_10 = slice(40, 41)                          # 10:00~10:15
    assert sum(attenuated[at_10]) < sum(full[at_10])        # 줄었고
    assert sum(attenuated[at_10]) > sum(removed[at_10])     # 사라지지는 않았다
    assert model["meta"]["meal_time"]["prompted"] == 20


def test_weight_one_equals_off():
    natural, prompted = prompted_mix()
    plain, _ = density_of(natural + prompted)
    one, _ = density_of(natural + prompted, prompted_weight=1.0)
    assert one == pytest.approx(plain, abs=1e-12)


def test_recent_and_prompted_combine():
    samples = [meal(0, 420, prompted=True), meal(10, 430)]
    w = sample_weights(samples, SampleWeighting(recent_lambda=0.1, prompted_weight=0.5))
    assert w.tolist() == pytest.approx([math.exp(-1.0) * 0.5, 1.0])


def test_weighted_circular_still_normalized():
    sleep = [KdeSample("sleep_time", v % 1440, (START + timedelta(days=i)).isoformat(), "sensor", i % 3 == 0)
             for i, v in enumerate(np.random.default_rng(2).normal(0, 30, 30))]
    _, density = fit_distribution(
        "sleep_time", values(sleep), weights=sample_weights(sleep, SampleWeighting(0.05, 0.3))
    )
    assert sum(density) == pytest.approx(1.0)
    assert int(np.argmax(density)) in (94, 95, 0, 1)


@pytest.mark.parametrize("weights", [np.ones(3), np.array([1.0, 0.0, 1.0, 1.0]), np.array([1, math.nan, 1, 1])])
def test_invalid_weights_rejected(weights):
    with pytest.raises(ValueError):
        fit_distribution("meal_time", np.array([400.0, 420.0, 440.0, 460.0]), weights=weights)


# ============================================================ cold start


def test_alpha_function():
    assert blend_alpha(0, 14) == 0
    assert blend_alpha(14, 14) == 0.5
    alphas = [blend_alpha(d, 14) for d in (0, 7, 14, 30, 90, 365)]
    assert alphas == sorted(alphas)
    assert alphas[-1] < 1
    with pytest.raises(ValueError):
        blend_alpha(-1, 14)
    with pytest.raises(ValueError):
        blend_alpha(5, 0)


def test_cold_start_requires_half_days():
    with pytest.raises(ValueError):
        ColdStart(half_days=0)


def test_blend_extremes():
    personal = [0.0, 1.0, 0.0, 0.0]
    prior = [1.0, 1.0, 1.0, 1.0]                   # 정규화되지 않은 prior 도 받는다
    assert blend(personal, prior, 1.0) == pytest.approx(personal)
    assert blend(personal, prior, 0.0) == pytest.approx([0.25] * 4)
    assert sum(blend(personal, prior, 0.3)) == pytest.approx(1.0)


@pytest.mark.parametrize("prior", [[0.5] * 3, [-1.0, 1, 1, 1], [0.0] * 4, [math.nan, 1, 1, 1]])
def test_invalid_prior(prior):
    with pytest.raises(ValueError):
        blend([0.25] * 4, prior, 0.5)


def test_cold_start_moves_toward_personal_with_more_days():
    uniform = [1 / 96] * 96
    distances = []
    for days in (3, 7, 14, 28):
        samples = drifting_breakfast()[:days]
        personal = build_model(samples)["distributions"]["meal_time"]["density"]
        blended = build_model(samples, cold_start=ColdStart(14, {"meal_time": uniform}))
        density = blended["distributions"]["meal_time"]["density"]
        distances.append(float(np.abs(np.array(density) - np.array(personal)).sum()))
        assert blended["meta"]["meal_time"]["cold_start"]["alpha"] == pytest.approx(days / (days + 14), abs=1e-6)
    assert distances == sorted(distances, reverse=True)


def test_cold_start_lowers_overconfidence():
    """데이터가 적을 때 prior 와 섞으면 덜 확신한다 (predictability 하락)."""
    samples = drifting_breakfast()[:5]
    alone = build_model(samples)["predictability"]["meal_time"]
    blended = build_model(samples, cold_start=ColdStart(14, {"meal_time": [1 / 96] * 96}))
    assert blended["predictability"]["meal_time"] < alone


def test_missing_prior_falls_back_to_personal():
    plain = build_model(four_distributions())
    blended = build_model(four_distributions(), cold_start=ColdStart(14, {"meal_time": [1 / 96] * 96}))
    assert blended["distributions"]["wake_time"] == plain["distributions"]["wake_time"]
    assert blended["meta"]["wake_time"]["cold_start"] == {"alpha": 1.0, "prior": False}
    assert blended["meta"]["meal_time"]["cold_start"]["prior"] is True


def test_hydration_prior_uses_its_own_grid():
    with pytest.raises(ValueError, match="길이"):
        build_model(four_distributions(), cold_start=ColdStart(14, {"hydration_lag": [1 / 96] * 96}))
    model = build_model(four_distributions(), cold_start=ColdStart(14, {"hydration_lag": [1 / 24] * 24}))
    assert len(model["distributions"]["hydration_lag"]["density"]) == 24


# ============================================================ payload


def test_all_features_still_produce_valid_payload():
    samples = four_distributions() + [meal(d, 600, prompted=True) for d in range(5)]
    model = build_model(
        samples,
        sample_weighting=SampleWeighting(recent_lambda=0.05, prompted_weight=0.3),
        cold_start=ColdStart(14, {"meal_time": [1 / 96] * 96, "hydration_lag": [1 / 24] * 24}),
    )
    payload = build_kde_payload(model)
    validate_kde_payload(payload, require_all=True)
    assert "meta" not in payload
