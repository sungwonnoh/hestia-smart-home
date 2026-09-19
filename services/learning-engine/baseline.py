from pathlib import Path
import csv
from datetime import datetime

import numpy as np
from scipy.stats import gaussian_kde


DATA_PATH = Path(
    "data/processed/aruba/breakfast_preparation.csv"
)

BREAKFAST_START = 5.0
BREAKFAST_END = 11.0

TAIL_THRESHOLD = 0.05
PREDICTABILITY_THRESHOLD = 0.05


def time_to_float(time_str):
    """
    예:
    07:30:00 -> 7.5
    08:15:00 -> 8.25
    """

    t = datetime.strptime(
        time_str,
        "%H:%M:%S.%f",
    )

    return (
        t.hour
        + t.minute / 60
        + t.second / 3600
        + t.microsecond / 3_600_000_000
    )


def float_to_time(value):
    """
    7.5 -> 07:30
    """

    hour = int(value)
    minute = int(round((value - hour) * 60))

    if minute == 60:
        hour += 1
        minute = 0

    return f"{hour:02d}:{minute:02d}"


def load_times():
    """
    breakfast_preparation.csv에서
    시간 데이터만 읽어 float 배열로 반환
    """

    times = []

    with open(DATA_PATH, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)

        for row in reader:
            times.append(
                time_to_float(row["time"])
            )

    return np.array(times)


def calculate_predictability(kde):
    """
    KDE 분포의 entropy를 이용해
    생활 패턴의 규칙성을 계산한다.

    값이 높을수록 규칙적
    값이 낮을수록 불규칙
    """

    grid = np.linspace(
        BREAKFAST_START,
        BREAKFAST_END,
        300,
    )

    density = kde(grid)

    # density를 확률분포 형태로 정규화
    probability = density / density.sum()

    # log(0) 방지
    probability = probability[
        probability > 0
    ]

    entropy = -np.sum(
        probability * np.log(probability)
    )

    max_entropy = np.log(len(grid))

    normalized_entropy = (
        entropy / max_entropy
    )

    predictability = (
        1 - normalized_entropy
    )

    return predictability


def calculate_tail_probability(
    kde,
    query_time,
):
    """
    query_time보다 늦게
    Breakfast Preparation이 발생할
    KDE 확률을 계산한다.
    """

    total_probability = (
        kde.integrate_box_1d(
            BREAKFAST_START,
            BREAKFAST_END,
        )
    )

    tail_probability = (
        kde.integrate_box_1d(
            query_time,
            BREAKFAST_END,
        )
    )

    if total_probability == 0:
        return 0.0

    return (
        tail_probability
        / total_probability
    )


def parse_query_time(query_time_str):
    """
    "09:40" -> 9.666...
    """

    t = datetime.strptime(
        query_time_str,
        "%H:%M",
    )

    return (
        t.hour
        + t.minute / 60
    )


def make_decision(
    tail_probability,
    predictability,
):
    """
    개입 조건:

    1. 현재 시각이 평소 패턴의
       상위 5%보다 늦음

    AND

    2. 사용자 패턴이 충분히 규칙적임
    """

    time_anomaly = (
        tail_probability
        < TAIL_THRESHOLD
    )

    pattern_reliable = (
        predictability
        >= PREDICTABILITY_THRESHOLD
    )

    if (
        time_anomaly
        and pattern_reliable
    ):
        decision = "INTERVENE"
    else:
        decision = "NORMAL"

    return (
        decision,
        time_anomaly,
        pattern_reliable,
    )


def analyze(query_time_str):
    times = load_times()

    if len(times) < 2:
        raise ValueError(
            "KDE 계산을 위한 데이터가 부족합니다."
        )

    query_time = parse_query_time(
        query_time_str
    )

    if not (
        BREAKFAST_START
        <= query_time
        <= BREAKFAST_END
    ):
        raise ValueError(
            f"테스트 시각은 "
            f"{BREAKFAST_START:02.0f}:00~"
            f"{BREAKFAST_END:02.0f}:00 "
            f"범위여야 합니다."
        )

    # KDE 생성
    kde = gaussian_kde(times)

    # 기본 통계
    mean = np.mean(times)

    std_hours = np.std(times)

    std_minutes = (
        std_hours * 60
    )

    # KDE 기반 이상도
    tail_probability = (
        calculate_tail_probability(
            kde,
            query_time,
        )
    )

    # 규칙성
    predictability = (
        calculate_predictability(
            kde
        )
    )

    # 최종 판단
    (
        decision,
        time_anomaly,
        pattern_reliable,
    ) = make_decision(
        tail_probability,
        predictability,
    )

    print()
    print(
        "===== HESTIA KDE BASELINE ====="
    )
    print()

    print(
        f"Observed days       : "
        f"{len(times)}"
    )

    print(
        f"Mean                : "
        f"{float_to_time(mean)}"
    )

    print(
        f"Std                 : "
        f"{std_minutes:.1f} min"
    )

    print()

    print(
        f"Query               : "
        f"{query_time_str}"
    )

    print(
        f"Tail probability    : "
        f"{tail_probability * 100:.2f}%"
    )

    print(
        f"Predictability      : "
        f"{predictability:.3f}"
    )

    print()

    print(
        f"Tail threshold      : "
        f"{TAIL_THRESHOLD:.2f}"
    )

    print(
        f"Predict threshold   : "
        f"{PREDICTABILITY_THRESHOLD:.2f}"
    )

    print()

    print(
        f"Time anomaly        : "
        f"{'YES' if time_anomaly else 'NO'}"
    )

    print(
        f"Pattern reliable    : "
        f"{'YES' if pattern_reliable else 'NO'}"
    )

    print()

    print(
        f"Decision            : "
        f"{decision}"
    )

    print()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description=(
            "HESTIA KDE-based "
            "personal baseline analyzer"
        )
    )

    parser.add_argument(
        "--time",
        required=True,
        help=(
            "테스트 시각 "
            "(HH:MM, 예: 09:40)"
        ),
    )

    args = parser.parse_args()

    analyze(args.time)