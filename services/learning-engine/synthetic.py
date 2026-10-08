"""
KDE 로직 검증용 synthetic 표본 생성기.

실제 사용자 패턴을 주장하는 데이터가 아니다. 다음 용도로만 쓴다.

    - KDE fitting 검증
    - predictability 검증
    - regular / irregular pattern 비교
    - edge case 검증

hydration_lag 를 합성하는 이유:
CASAS Aruba 에는 수분 섭취 label 이 없어 (Phase 1 조사, label 11종)
"기상 → 첫 수분 섭취" 간격을 실제 데이터에서 얻을 수 없다.

production MQTT schema(hestia/log/t0 의 source enum 등)는 건드리지 않는다.
"""

from __future__ import annotations

import argparse
import math
from datetime import date, timedelta

import numpy as np

from samples import KdeSample


# 한 번에 다시 뽑는 최대 횟수. 범위 밖 값만 다시 뽑는데,
# 평균이 허용 범위에서 너무 멀면 끝나지 않으므로 상한을 둔다.
MAX_REDRAW_ROUNDS = 1000


def _check_finite(name: str, value: float) -> None:
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not math.isfinite(value)
    ):
        raise ValueError(f"{name}는 유한한 숫자여야 합니다: {value!r}")


def truncated_normal(
    rng: np.random.Generator,
    size: int,
    mean: float,
    std: float,
    low: float,
    high: float | None,
) -> np.ndarray:
    """
    [low, high] 범위의 정규분포 표본.

    범위 밖 값은 잘라 붙이지(clip) 않고 다시 뽑는다.
    clip 하면 경계에 표본이 몰려 KDE 에 가짜 봉우리가 생긴다.
    """

    upper = math.inf if high is None else high
    values = rng.normal(mean, std, size)

    for _ in range(MAX_REDRAW_ROUNDS):
        bad = (values < low) | (values > upper)

        if not bad.any():
            return values

        values[bad] = rng.normal(mean, std, int(bad.sum()))

    raise ValueError(
        f"허용 범위 [{low}, {high}] 안에서 표본을 만들 수 없습니다 "
        f"(mean={mean}, std={std})"
    )


def generate_hydration_lag(
    sample_days: int,
    mean_min: float,
    std_min: float,
    seed: int | None = None,
    max_min: float | None = None,
    start_date: date = date(2026, 1, 1),
) -> list[KdeSample]:
    """
    하루 한 건씩 "기상 → 첫 수분 섭취" 경과 분을 만든다.

    regular  pattern: std_min 을 작게 (예: mean 15, std 5)
    irregular pattern: std_min 을 크게

    - 음수 duration 은 만들지 않는다 (0 이상).
    - max_min 을 주면 그 이하로 제한한다.
      실제 Context Engine 은 기상 후 hydration_window_sec(현재 7200초) 안의
      급수만 기록하므로, 실제 로그를 흉내 내려면 max_min=120 을 주면 된다.
    - 같은 seed 면 같은 결과다.
    """

    if (
        not isinstance(sample_days, int)
        or isinstance(sample_days, bool)
        or sample_days < 1
    ):
        raise ValueError(f"sample_days는 1 이상의 정수여야 합니다: {sample_days!r}")

    _check_finite("mean_min", mean_min)
    _check_finite("std_min", std_min)

    if std_min < 0:
        raise ValueError(f"std_min은 0 이상이어야 합니다: {std_min}")

    if max_min is not None:
        _check_finite("max_min", max_min)

        if max_min <= 0:
            raise ValueError(f"max_min은 양수여야 합니다: {max_min}")

    rng = np.random.default_rng(seed)

    values = truncated_normal(
        rng,
        sample_days,
        mean_min,
        std_min,
        low=0.0,
        high=max_min,
    )

    # source="synthetic" 은 학습 입력 내부 표시일 뿐 t0 source enum 이 아니다.
    return [
        KdeSample(
            distribution="hydration_lag",
            value=float(v),
            date=(start_date + timedelta(days=i)).isoformat(),
            source="synthetic",
        )
        for i, v in enumerate(values)
    ]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="synthetic hydration_lag 생성")

    parser.add_argument("--days", type=int, default=60)
    parser.add_argument("--mean", type=float, default=15)
    parser.add_argument("--std", type=float, default=5)
    parser.add_argument("--max", type=float, default=None)
    parser.add_argument("--seed", type=int, default=42)

    args = parser.parse_args()

    samples = generate_hydration_lag(
        sample_days=args.days,
        mean_min=args.mean,
        std_min=args.std,
        seed=args.seed,
        max_min=args.max,
    )

    values = np.array([s.value for s in samples])

    for s in samples[:7]:
        print(f"  {s.date}: {s.value:5.1f} min")

    print(
        f"\n  {len(values)} days  "
        f"mean {values.mean():.1f}  "
        f"std {values.std():.1f}  "
        f"min {values.min():.1f}  "
        f"max {values.max():.1f}"
    )
