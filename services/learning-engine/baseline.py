from __future__ import annotations

from pathlib import Path
import argparse
import csv
import json
import logging
import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import numpy as np
from scipy.stats import gaussian_kde


log = logging.getLogger(__name__)


DATA_PATH = Path(
    "data/processed/aruba/breakfast_preparation.csv"
)

# Context Engine(timeutil.KST)과 같은 기준.
# t0 epoch를 자정 기준 분으로 바꿀 때 사용한다.
KST = timezone(timedelta(hours=9))

# t0 JSONL의 type → KDE distribution 이름
TYPE_TO_DISTRIBUTION = {
    "wake": "wake_time",
    "meal": "meal_time",
    "sleep": "sleep_time",
    "hydration": "hydration_lag",
}

# 시각(자정 기준 분)이 아니라 경과 시간(duration_sec)을 쓰는 distribution
DURATION_DISTRIBUTIONS = {"hydration_lag"}

# Context Engine T0Entry와 같은 필드
REQUIRED_FIELDS = (
    "date",
    "type",
    "t0",
    "source",
    "prompted",
    "duration_sec",
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


@dataclass(frozen=True)
class T0Sample:
    """
    KDE 학습 입력 한 건.
    Context Engine이 남기는 t0 로그(T0Entry) 한 줄과 같은 구조다.
    """

    date: str
    type: str
    t0: float
    source: str
    prompted: bool
    duration_sec: float


def _is_number(value) -> bool:
    # bool은 int의 하위 타입이므로 따로 제외한다.
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def parse_t0_line(line: str) -> T0Sample:
    """
    t0 JSONL 한 줄을 T0Sample로 변환한다.
    형식이 잘못되면 ValueError를 발생시킨다.
    """

    try:
        obj = json.loads(line)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"잘못된 JSON: {exc}"
        ) from exc

    if not isinstance(obj, dict):
        raise ValueError(
            "JSON object가 아닙니다."
        )

    missing = [
        key
        for key in REQUIRED_FIELDS
        if key not in obj
    ]

    if missing:
        raise ValueError(
            f"필수 필드 누락: {', '.join(missing)}"
        )

    for key in ("date", "type", "source"):
        if not isinstance(obj[key], str):
            raise ValueError(
                f"{key}는 문자열이어야 합니다."
            )

    if not _is_number(obj["t0"]):
        raise ValueError(
            "t0는 epoch seconds 숫자여야 합니다."
        )

    if not isinstance(obj["prompted"], bool):
        raise ValueError(
            "prompted는 bool이어야 합니다."
        )

    if (
        not _is_number(obj["duration_sec"])
        or obj["duration_sec"] < 0
    ):
        raise ValueError(
            "duration_sec는 0 이상의 숫자여야 합니다."
        )

    return T0Sample(
        date=obj["date"],
        type=obj["type"],
        t0=float(obj["t0"]),
        source=obj["source"],
        prompted=obj["prompted"],
        duration_sec=float(obj["duration_sec"]),
    )


def load_t0_jsonl(path: Path) -> list[T0Sample]:
    """
    Context Engine Replay / FileT0Log가 생성한
    t0 JSONL을 한 줄씩 읽는다.

    잘못된 줄은 경고만 남기고 건너뛴다.
    로그 한 줄이 깨졌다고 전체 학습이 멈추면 안 된다.
    """

    samples = []

    with open(path, "r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, start=1):
            line = line.strip()

            if not line:
                continue

            try:
                samples.append(
                    parse_t0_line(line)
                )
            except ValueError as exc:
                log.warning(
                    "t0 JSONL %s:%d 건너뜀 — %s",
                    path,
                    line_no,
                    exc,
                )

    return samples


def load_aruba_samples(
    path: Path = DATA_PATH,
    type_: str = "meal",
) -> list[T0Sample]:
    """
    Aruba CSV(date,time,activity)를 t0 JSONL과 같은
    T0Sample로 변환한다.

    Aruba 시각을 KST 로컬 시각으로 간주해 epoch로 바꾸므로
    다시 자정 기준 분으로 바꾸면 원래 시각과 같다.
    """

    samples = []

    with open(path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)

        for row in reader:
            local = datetime.strptime(
                f"{row['date']} {row['time']}",
                "%Y-%m-%d %H:%M:%S.%f",
            ).replace(tzinfo=KST)

            samples.append(
                T0Sample(
                    date=row["date"],
                    type=type_,
                    t0=local.timestamp(),
                    source="aruba",
                    prompted=False,
                    duration_sec=0.0,
                )
            )

    return samples


def t0_to_minutes(t0: float) -> float:
    """
    epoch seconds → KST 자정 기준 분
    """

    t = datetime.fromtimestamp(t0, KST)

    return (
        t.hour * 60
        + t.minute
        + t.second / 60
        + t.microsecond / 60_000_000
    )


def group_by_distribution(
    samples: list[T0Sample],
) -> dict[str, list[T0Sample]]:
    """
    type 기준으로 distribution별 sample을 나눈다.
    알 수 없는 type(예: medication)은 KDE 대상이 아니므로 제외한다.
    """

    groups: dict[str, list[T0Sample]] = {}
    unknown: set[str] = set()

    for sample in samples:
        name = TYPE_TO_DISTRIBUTION.get(sample.type)

        if name is None:
            unknown.add(sample.type)
            continue

        groups.setdefault(name, []).append(sample)

    if unknown:
        log.info(
            "KDE 대상이 아닌 type 제외: %s",
            ", ".join(sorted(unknown)),
        )

    return groups


def sample_values(
    distribution: str,
    samples: list[T0Sample],
) -> np.ndarray:
    """
    distribution에 맞는 KDE 입력값(분 단위)을 만든다.

    - wake/meal/sleep_time: t0의 KST 자정 기준 분
    - hydration_lag: duration_sec(기상 후 경과 시간)를 분으로 변환
    """

    if distribution in DURATION_DISTRIBUTIONS:
        return np.array(
            [s.duration_sec / 60 for s in samples]
        )

    return np.array(
        [t0_to_minutes(s.t0) for s in samples]
    )


def build_training_input(
    samples: list[T0Sample],
) -> dict[str, np.ndarray]:
    """
    T0Sample 목록 → distribution별 KDE 입력값
    """

    return {
        name: sample_values(name, group)
        for name, group in group_by_distribution(
            samples
        ).items()
    }


def load_times() -> np.ndarray:
    """
    breakfast_preparation.csv의 시각을
    자정 기준 분(minute) 단위로 읽는다.
    """

    return sample_values(
        "meal_time",
        load_aruba_samples(),
    )


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


def build_model(
    samples: list[T0Sample] | None = None,
) -> dict:
    """
    MQTT payload에 들어갈 KDE 모델 부분을 생성한다.

    samples가 없으면 기존 Aruba breakfast 데이터를 사용한다.
    TODO(Phase 2): meal_time 외 distribution 생성
    """

    if samples is None:
        samples = load_aruba_samples()

    meal_samples = group_by_distribution(
        samples
    ).get("meal_time", [])

    times = sample_values(
        "meal_time",
        meal_samples,
    )

    kde = fit_kde(times)

    density = build_density(kde)

    predictability = (
        calculate_predictability(density)
    )

    return {
        "sample_days": len(
            {s.date for s in meal_samples}
        ),
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
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--t0-jsonl",
        type=Path,
        help="Context Engine t0 JSONL 경로 (없으면 Aruba CSV 사용)",
    )

    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)

    model = build_model(
        load_t0_jsonl(args.t0_jsonl)
        if args.t0_jsonl
        else None
    )

    print(
        json.dumps(
            model,
            indent=2,
            ensure_ascii=False,
        )
    )
    