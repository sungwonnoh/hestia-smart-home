"""
KDE 공통 학습 입력 계층.

KDE fitting 은 데이터 출처를 모른다. 출처별 adapter 가 KdeSample 을 만들고,
KDE 는 distribution 별 숫자 배열만 받는다.

    Aruba parser      (aruba.py)      ┐
    Synthetic fixture (synthetic.py)  ├─→ KdeSample ─→ values() ─→ fit_kde(...)
    t0 adapter        (baseline.py)   ┘

distribution 은 두 종류다. 같은 숫자라도 처리가 다르다.

    time_of_day : wake_time / sleep_time / meal_time
                  자정 기준 분, [0, 1440). 자정에서 이어진다 (circular, Phase 6).
    elapsed     : hydration_lag
                  기준 사건(기상) 이후 경과 분, 0 이상. 이어지지 않는다.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np


MINUTES_PER_DAY = 1440

TIME_OF_DAY = "time_of_day"
ELAPSED = "elapsed"

# hestia/model/kde 명세의 distribution 4종
DISTRIBUTION_KIND = {
    "wake_time": TIME_OF_DAY,
    "sleep_time": TIME_OF_DAY,
    "meal_time": TIME_OF_DAY,
    "hydration_lag": ELAPSED,
}


def is_time_of_day(distribution: str) -> bool:
    return DISTRIBUTION_KIND[distribution] == TIME_OF_DAY


@dataclass(frozen=True)
class KdeSample:
    """
    KDE 학습 표본 한 건.

    source 는 학습 입력 내부의 출처 표시다 (aruba / synthetic / sensor / diary).
    hestia/log/t0 의 source enum 과 별개이며 MQTT 로 나가지 않는다.

    proxy 는 HESTIA 가 실제로 측정한 값이 아니라 다른 데이터로 대신한 값이다
    (예: Aruba Sleeping 으로 만든 sleep_time).
    """

    distribution: str
    value: float
    date: str
    source: str
    prompted: bool = False
    proxy: bool = False

    def __post_init__(self) -> None:
        kind = DISTRIBUTION_KIND.get(self.distribution)

        if kind is None:
            raise ValueError(f"알 수 없는 distribution: {self.distribution!r}")

        if (
            not isinstance(self.value, (int, float))
            or isinstance(self.value, bool)
            or not math.isfinite(self.value)
        ):
            raise ValueError(f"{self.distribution}: value는 유한한 숫자여야 합니다: {self.value!r}")

        if kind == TIME_OF_DAY and not 0 <= self.value < MINUTES_PER_DAY:
            raise ValueError(f"{self.distribution}: 자정 기준 분은 [0, 1440) 이어야 합니다: {self.value}")

        if kind == ELAPSED and self.value < 0:
            raise ValueError(f"{self.distribution}: 경과 분은 0 이상이어야 합니다: {self.value}")


def group(samples: list[KdeSample]) -> dict[str, list[KdeSample]]:
    """distribution별로 나눈다. 순서는 입력 순서를 유지한다."""

    groups: dict[str, list[KdeSample]] = {}

    for sample in samples:
        groups.setdefault(sample.distribution, []).append(sample)

    return groups


def values(samples: list[KdeSample]) -> np.ndarray:
    return np.array([s.value for s in samples], dtype=float)


def sample_days(samples: list[KdeSample]) -> int:
    return len({s.date for s in samples})


def build_training_input(samples: list[KdeSample]) -> dict[str, np.ndarray]:
    """KdeSample 목록 → distribution별 KDE 입력값"""

    return {
        name: values(items)
        for name, items in group(samples).items()
    }
