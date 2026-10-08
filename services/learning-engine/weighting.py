"""
KDE 고도화 (KDE_REFACTOR_TASKS_v2 Phase 11).

    Recent Weighting      weight(age) = exp(-lambda * age_days)
    Prompted Attenuation  prompted 표본만 weight * prompted_weight (0 < w <= 1)
    Cold Start Blending   final = alpha * personal + (1 - alpha) * prior

모든 값은 설정으로 받고 기본은 꺼져 있다.
lambda / prompted_weight / half_days 의 실제 값은 실험으로 정한다 — 여기서 정하지 않는다.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date
from typing import Mapping, Sequence

import numpy as np

from samples import KdeSample


def _finite(value) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


# ==================================================================== 표본 가중치


@dataclass(frozen=True)
class SampleWeighting:
    """
    recent_lambda
        하루당 감쇠율. None 이면 끈다. 0 이면 모든 표본이 같은 가중치다.
    prompted_weight
        HESTIA 가 유도한 표본(prompted=True)의 가중치. None 이면 끈다.
        완전히 빼지 않으므로 0 은 허용하지 않는다 (0 < w <= 1).
    reference_date
        age 기준일. None 이면 distribution 별 가장 최근 표본 날짜다.
    """

    recent_lambda: float | None = None
    prompted_weight: float | None = None
    reference_date: date | None = None

    def __post_init__(self) -> None:
        if self.recent_lambda is not None:
            if not _finite(self.recent_lambda) or self.recent_lambda < 0:
                raise ValueError(f"recent_lambda는 0 이상의 숫자여야 합니다: {self.recent_lambda!r}")

        if self.prompted_weight is not None:
            if not _finite(self.prompted_weight) or not 0 < self.prompted_weight <= 1:
                raise ValueError(
                    f"prompted_weight는 0 < w <= 1 이어야 합니다 (prompted 표본을 완전히 빼지 않음): "
                    f"{self.prompted_weight!r}"
                )

    @property
    def enabled(self) -> bool:
        return self.recent_lambda is not None or self.prompted_weight is not None


def sample_ages(
    samples: list[KdeSample],
    reference: date | None = None,
) -> np.ndarray:
    """
    기준일로부터 며칠 전 표본인지. 기준일보다 뒤의 표본은 0일로 본다.
    """

    dates = [date.fromisoformat(s.date) for s in samples]
    ref = reference or max(dates)

    return np.array(
        [max(0, (ref - d).days) for d in dates],
        dtype=float,
    )


def recency_weights(ages: np.ndarray, recent_lambda: float) -> np.ndarray:
    return np.exp(-recent_lambda * np.asarray(ages, dtype=float))


def sample_weights(
    samples: list[KdeSample],
    weighting: SampleWeighting | None,
) -> np.ndarray | None:
    """
    표본별 KDE 가중치. 꺼져 있으면 None (기존 무가중치 KDE 그대로).
    """

    if weighting is None or not weighting.enabled:
        return None

    weights = np.ones(len(samples))

    if weighting.recent_lambda is not None:
        weights *= recency_weights(
            sample_ages(samples, weighting.reference_date),
            weighting.recent_lambda,
        )

    if weighting.prompted_weight is not None:
        weights *= np.array(
            [weighting.prompted_weight if s.prompted else 1.0 for s in samples]
        )

    return weights


def effective_samples(weights: np.ndarray) -> float:
    """가중 표본의 유효 개수 (Kish). gaussian_kde 의 bandwidth 도 이 값을 쓴다."""

    w = np.asarray(weights, dtype=float)

    return float(w.sum() ** 2 / np.sum(w ** 2))


# ==================================================================== cold start


@dataclass(frozen=True)
class ColdStart:
    """
    priors
        distribution 이름 → 같은 격자의 prior density.
        생활시간조사 등 실제 prior 데이터는 외부에서 주입한다 (여기서 만들지 않음).
        prior 가 없는 distribution 은 개인 분포를 그대로 쓴다.
    half_days
        alpha 가 0.5 가 되는 sample_days. 값은 실험으로 정한다.
    """

    half_days: float
    priors: Mapping[str, Sequence[float]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not _finite(self.half_days) or self.half_days <= 0:
            raise ValueError(f"half_days는 양수여야 합니다: {self.half_days!r}")


def blend_alpha(sample_days: int, half_days: float) -> float:
    """
    개인 분포 비중. 0일 → 0, half_days → 0.5, 많을수록 1 에 가까워진다.

        alpha = sample_days / (sample_days + half_days)
    """

    if sample_days < 0:
        raise ValueError(f"sample_days는 0 이상이어야 합니다: {sample_days}")

    if not _finite(half_days) or half_days <= 0:
        raise ValueError(f"half_days는 양수여야 합니다: {half_days!r}")

    return sample_days / (sample_days + half_days)


def normalize_prior(prior: Sequence[float], size: int) -> np.ndarray:
    p = np.asarray(prior, dtype=float)

    if p.shape != (size,):
        raise ValueError(f"prior 길이는 격자와 같아야 합니다: {p.shape} != ({size},)")

    if not np.isfinite(p).all() or (p < 0).any() or p.sum() <= 0:
        raise ValueError("prior는 합이 양수인 0 이상 유한 값이어야 합니다.")

    return p / p.sum()


def blend(
    personal: Sequence[float],
    prior: Sequence[float],
    alpha: float,
) -> list[float]:
    """alpha * personal + (1 - alpha) * prior, 합 1 로 정규화"""

    if not 0 <= alpha <= 1:
        raise ValueError(f"alpha는 0~1이어야 합니다: {alpha}")

    p = np.asarray(personal, dtype=float)
    q = normalize_prior(prior, len(p))

    mixed = alpha * p + (1 - alpha) * q

    return (mixed / mixed.sum()).tolist()
