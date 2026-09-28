from pathlib import Path
import csv
import json
from datetime import datetime

import numpy as np
from scipy.stats import gaussian_kde


DATA_PATH = Path(
    "data/processed/aruba/breakfast_preparation.csv"
)

# MQTT 명세:
# 자정 기준, 15분 간격
GRID_MIN = 0
GRID_STEP = 15
GRID_SIZE = 24 * 60 // GRID_STEP  # 96칸


def time_to_minutes(time_str: str) -> float:
    """
    07:30:00 -> 450분
    """
    t = datetime.strptime(
        time_str,
        "%H:%M:%S.%f",
    )

    return (
        t.hour * 60
        + t.minute
        + t.second / 60
        + t.microsecond / 60_000_000
    )


def load_times() -> np.ndarray:
    """
    breakfast_preparation.csv의 시각을
    자정 기준 분(minute) 단위로 읽는다.
    """

    times = []

    with open(DATA_PATH, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)

        for row in reader:
            times.append(
                time_to_minutes(row["time"])
            )

    return np.array(times)


def fit_kde(times: np.ndarray):
    if len(times) < 2:
        raise ValueError(
            "KDE 계산을 위한 데이터가 부족합니다."
        )

    return gaussian_kde(times)


def build_density(kde) -> list[float]:
    """
    하루를 15분 단위 96칸으로 나누고
    각 칸의 KDE density를 계산한다.

    최종 배열의 합은 1.0이 되도록 정규화한다.
    """

    # 각 bin 중앙 시각
    grid = (
        np.arange(GRID_SIZE) * GRID_STEP
        + GRID_STEP / 2
    )

    density = kde(grid)

    density_sum = density.sum()

    if density_sum == 0:
        raise ValueError(
            "KDE density 합이 0입니다."
        )

    normalized_density = (
        density / density_sum
    )

    return normalized_density.tolist()


def calculate_predictability(
    density: list[float],
) -> float:
    """
    정규화된 density 배열의 entropy를 이용해
    predictability를 계산한다.

    1에 가까울수록 규칙적,
    0에 가까울수록 불규칙.
    """

    probability = np.array(density)

    probability = probability[
        probability > 0
    ]

    entropy = -np.sum(
        probability * np.log(probability)
    )

    max_entropy = np.log(GRID_SIZE)

    normalized_entropy = (
        entropy / max_entropy
    )

    predictability = (
        1 - normalized_entropy
    )

    return float(predictability)


def build_model() -> dict:
    """
    MQTT payload에 들어갈 KDE 모델 부분을 생성한다.
    """

    times = load_times()

    kde = fit_kde(times)

    density = build_density(kde)

    predictability = (
        calculate_predictability(density)
    )

    return {
        "sample_days": len(times),
        "distributions": {
            "meal_time": {
                "grid_min": GRID_MIN,
                "grid_step": GRID_STEP,
                "density": density,
            }
        },
        "predictability": {
            "meal_time": predictability,
        },
    }


if __name__ == "__main__":
    model = build_model()

    print(
        json.dumps(
            model,
            indent=2,
            ensure_ascii=False,
        )
    )
    