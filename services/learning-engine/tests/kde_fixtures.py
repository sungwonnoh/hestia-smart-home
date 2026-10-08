"""여러 테스트가 같이 쓰는 KDE 입력 fixture."""

import numpy as np

from baseline import parse_t0_record
from samples import KdeSample
from synthetic import generate_hydration_lag


# 2026-09-01 00:00 KST
DAY0 = 1788188400.0


def clock_samples(distribution, center, spread, n=40, seed=0):
    rng = np.random.default_rng(seed)
    return [
        KdeSample(distribution, float(v % 1440), f"2026-09-{1 + i % 28:02d}", "sensor")
        for i, v in enumerate(rng.normal(center, spread, n))
    ]


def four_distributions():
    return (
        clock_samples("wake_time", 7 * 60, 20, seed=1)
        + clock_samples("sleep_time", 23 * 60 + 30, 30, seed=2)
        + clock_samples("meal_time", 12 * 60, 60, seed=3)
        + generate_hydration_lag(40, 15, 5, seed=4, max_min=120)
    )


def t0_record(day, type_, minute_of_day, duration_sec=0.0, prompted=False, **extra):
    """Context Engine t0 레코드 (KST). extra 로 MQTT 봉투 필드를 붙인다."""
    return {
        **extra,
        "date": f"2026-09-{day + 1:02d}",
        "type": type_,
        "t0": DAY0 + day * 86400 + minute_of_day * 60,
        "source": "sensor",
        "prompted": prompted,
        "duration_sec": duration_sec,
    }


def t0_days(days=21):
    """기상·급수·식사가 매일 조금씩 다른 t0 로그"""
    rng = np.random.default_rng(7)
    records = []
    for day in range(days):
        wake = 7 * 60 + float(rng.normal(0, 15))
        lag = abs(float(rng.normal(12, 4)))
        records += [
            t0_record(day, "wake", wake),
            t0_record(day, "hydration", wake + lag, duration_sec=lag * 60),
            t0_record(day, "meal", 8 * 60 + float(rng.normal(0, 20)), duration_sec=1500),
        ]
    return [parse_t0_record(r) for r in records]
