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

import samples as kde_samples
from samples import KdeSample


log = logging.getLogger(__name__)


DATA_PATH = Path(
    "data/processed/aruba/breakfast_preparation.csv"
)

# Context Engine(timeutil.KST)과 같은 기준.
# t0 epoch를 자정 기준 분으로 바꿀 때 사용한다.
KST = timezone(timedelta(hours=9))

# t0 JSONL의 type → KDE distribution 이름
# 현재 hestia/log/t0 명세의 type은 meal / wake / hydration 뿐이다.
# sleep t0는 팀 합의 전이므로 매핑하지 않는다 (sleep_time은 Aruba proxy로 학습).
TYPE_TO_DISTRIBUTION = {
    "wake": "wake_time",
    "meal": "meal_time",
    "hydration": "hydration_lag",
}

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
    t0 로그 원본 레코드 한 건.
    Context Engine이 남기는 t0 로그(T0Entry) 한 줄과 같은 구조다.
    KDE 입력으로 쓸 때는 t0_to_kde_samples()로 KdeSample로 바꾼다.
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
) -> list[KdeSample]:
    """
    Aruba breakfast CSV(date,time,activity) → meal_time KdeSample
    """

    samples = []

    with open(path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)

        for row in reader:
            samples.append(
                KdeSample(
                    distribution="meal_time",
                    value=time_to_minutes(row["time"]),
                    date=row["date"],
                    source="aruba",
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


def t0_to_kde_sample(sample: T0Sample) -> KdeSample | None:
    """
    t0 레코드 한 건 → KdeSample
    KDE 대상이 아닌 type이면 None.

    - meal / wake: t0의 KST 자정 기준 분
    - hydration  : duration_sec(기상 후 경과 시간)를 분으로 변환
    """

    distribution = TYPE_TO_DISTRIBUTION.get(sample.type)

    if distribution is None:
        return None

    if kde_samples.is_time_of_day(distribution):
        value = t0_to_minutes(sample.t0)
    else:
        value = sample.duration_sec / 60

    return KdeSample(
        distribution=distribution,
        value=value,
        date=sample.date,
        source=sample.source,
        prompted=sample.prompted,
    )


def t0_to_kde_samples(samples: list[T0Sample]) -> list[KdeSample]:
    """
    t0 레코드 목록 → KdeSample 목록

    알 수 없는 type(예: medication, 명세에 없는 sleep)은 KDE 대상이 아니므로 제외한다.
    변환할 수 없는 값(예: 범위를 벗어난 t0)은 경고 후 건너뛴다.
    """

    out = []
    unknown: set[str] = set()

    for sample in samples:
        try:
            converted = t0_to_kde_sample(sample)
        except (ValueError, OverflowError, OSError) as exc:
            log.warning("t0 레코드 변환 실패 — %s: %s", sample, exc)
            continue

        if converted is None:
            unknown.add(sample.type)
            continue

        out.append(converted)

    if unknown:
        log.info(
            "KDE 대상이 아닌 type 제외: %s",
            ", ".join(sorted(unknown)),
        )

    return out


def load_times() -> np.ndarray:
    """
    breakfast_preparation.csv의 시각을
    자정 기준 분(minute) 단위로 읽는다.
    """

    return kde_samples.values(
        load_aruba_samples()
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
    samples: list[KdeSample] | None = None,
) -> dict:
    """
    MQTT payload에 들어갈 KDE 모델 부분을 생성한다.

    samples는 출처(Aruba / synthetic / t0)와 무관한 공통 KdeSample이다.
    없으면 기존 Aruba breakfast 데이터를 사용한다.
    TODO(v2 Phase 5): meal_time 외 distribution 생성
    """

    if samples is None:
        samples = load_aruba_samples()

    meal_samples = kde_samples.group(
        samples
    ).get("meal_time", [])

    times = kde_samples.values(meal_samples)

    kde = fit_kde(times)

    density = build_density(kde)

    predictability = (
        calculate_predictability(density)
    )

    return {
        "sample_days": kde_samples.sample_days(
            meal_samples
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
        t0_to_kde_samples(load_t0_jsonl(args.t0_jsonl))
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
    