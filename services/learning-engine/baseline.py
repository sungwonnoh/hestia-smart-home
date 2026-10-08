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
import weighting
from samples import KdeSample
from weighting import ColdStart, SampleWeighting
from sleep_sessions import SLEEP_EVENT_TYPES, samples_from_events


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

# hydration_lag 격자: 기상 후 0분부터 5분 간격 (MQTT 명세).
# 끝값은 명세에 없다. Context Engine 은 기상 후 hydration_window_sec(7200초) 안의
# 급수만 t0 로 남기므로 실제 hydration_lag 는 120분을 넘지 않는다.
HYDRATION_GRID_STEP = 5
HYDRATION_GRID_MAX_MIN = 120

# gaussian_kde 가 계산 가능한 최소 표본 수 (수학적 하한).
# 학습 신뢰도를 위한 최소 일수는 실험 없이 정하지 않는다.
MIN_SAMPLES = 2


@dataclass(frozen=True)
class Grid:
    """density 배열의 격자. 각 칸의 값은 칸 중앙에서 계산한다."""

    grid_min: float
    grid_step: float
    size: int

    def centers(self) -> np.ndarray:
        return (
            self.grid_min
            + np.arange(self.size) * self.grid_step
            + self.grid_step / 2
        )

    def to_payload(self) -> dict:
        return {
            "grid_min": self.grid_min,
            "grid_step": self.grid_step,
        }


TIME_OF_DAY_GRID = Grid(GRID_MIN, GRID_STEP, GRID_SIZE)


def grid_for(
    distribution: str,
    hydration_max_min: float = HYDRATION_GRID_MAX_MIN,
) -> Grid:
    if kde_samples.is_time_of_day(distribution):
        return TIME_OF_DAY_GRID

    return Grid(
        0,
        HYDRATION_GRID_STEP,
        int(hydration_max_min // HYDRATION_GRID_STEP),
    )


# 시각 분포의 주기 (분). 23:50과 00:10은 20분 차이다.
PERIOD_MIN = kde_samples.MINUTES_PER_DAY

# circular density를 계산할 때 더하는 주기 범위.
# 펼친 표본은 [0, 2880) 안에 있고, 앞뒤 한 바퀴씩 여유를 둔다.
WRAP_SHIFTS = range(-2, 4)


def unwrap_circular(
    values: np.ndarray,
    period: float = PERIOD_MIN,
) -> np.ndarray:
    """
    원 위의 시각을 끊김 없는 직선 값으로 펼친다.

    표본 사이 가장 큰 빈 구간을 자르는 곳으로 삼는다.
    23:00 / 00:30 취침은 1380 / 1470 이 되어 90분 차이로 이어진다.
    자정에서 자르면 bandwidth가 1440분짜리 분산으로 계산된다.
    """

    x = np.mod(np.asarray(values, dtype=float), period)
    ordered = np.sort(x)

    gaps = np.diff(
        np.append(ordered, ordered[0] + period)
    )
    cut = ordered[(int(np.argmax(gaps)) + 1) % len(ordered)]

    return np.where(x >= cut, x, x + period)


class InsufficientSamples(ValueError):
    """이 distribution은 KDE를 계산할 수 없다. 모델에서 빼고 이유를 남긴다."""


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

    return parse_t0_record(obj)


def parse_t0_record(obj) -> T0Sample:
    """
    t0 레코드(dict) 한 건을 T0Sample로 변환한다.

    Context Engine FileT0Log 한 줄과 hestia/log/t0 MQTT payload를 모두 받는다.
    MQTT 봉투 필드(version / sent_ts / src_id 등)는 KDE 입력이 아니므로 무시한다.

    sleep_start / sleep_end 는 duration_sec 이 없거나 null 이어도 된다 (0 으로 본다).
    수면 길이는 짝지은 sleep_end - sleep_start 로 계산하므로 이 값을 쓰지 않는다.
    """

    if not isinstance(obj, dict):
        raise ValueError(
            "JSON object가 아닙니다."
        )

    if obj.get("type") in SLEEP_EVENT_TYPES and obj.get("duration_sec") is None:
        obj = {**obj, "duration_sec": 0.0}

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


def load_t0_records(path: Path) -> list[dict]:
    """
    Context Engine Replay / FileT0Log가 생성한
    t0 JSONL을 한 줄씩 읽어 검증을 통과한 레코드(dict)를 돌려준다.

    dict 그대로 두는 이유: sleep_start 의 area 처럼 T0Sample 에 없는 필드가 있다.

    잘못된 줄은 경고만 남기고 건너뛴다.
    로그 한 줄이 깨졌다고 전체 학습이 멈추면 안 된다.
    """

    records = []

    with open(path, "r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, start=1):
            line = line.strip()

            if not line:
                continue

            try:
                obj = json.loads(line)
                parse_t0_record(obj)
            except json.JSONDecodeError as exc:
                log.warning("t0 JSONL %s:%d 건너뜀 — 잘못된 JSON: %s", path, line_no, exc)
                continue
            except ValueError as exc:
                log.warning("t0 JSONL %s:%d 건너뜀 — %s", path, line_no, exc)
                continue

            records.append(obj)

    return records


def load_t0_jsonl(path: Path) -> list[T0Sample]:
    """t0 JSONL → T0Sample 목록 (잘못된 줄은 건너뜀)"""

    return [parse_t0_record(r) for r in load_t0_records(path)]


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
    sleep_start / sleep_end 는 짝지어야 하는 이벤트라 여기서 다루지 않는다
    (t0_records_to_kde_samples 가 sleep_sessions 로 따로 처리).
    변환할 수 없는 값(예: 범위를 벗어난 t0)은 경고 후 건너뛴다.
    """

    out = []
    unknown: set[str] = set()

    for sample in samples:
        if sample.type in SLEEP_EVENT_TYPES:
            continue

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


def t0_records_to_kde_samples(records: list[dict]) -> list[KdeSample]:
    """
    t0 로그 전체 → KdeSample.

        meal / hydration          t0 adapter (t0_to_kde_samples)
        sleep_start / sleep_end   짝지어 수면 → 밤잠 / 낮잠 구분 → 밤잠만 sleep_time / wake_time

    wake_time 은 한 곳에서만 얻는다 (같은 기상을 두 번 학습하지 않도록).
      - 기상이 관측된 밤잠이 있으면 그 sleep_end 를 쓰고 wake 레코드는 쓰지 않는다
        (낮잠에서 깬 것은 제외되고, Context Engine wake 의 기상 인정 시간대 제한도 받지 않는다)
      - 없으면 wake 레코드를 쓴다 (이전과 같음)
    """

    samples = t0_to_kde_samples([parse_t0_record(r) for r in records])
    sleep_events = [r for r in records if r.get("type") in SLEEP_EVENT_TYPES]

    if not sleep_events:
        return samples

    series, _ = samples_from_events(sleep_events)

    if series["wake_time"]:
        dropped = sum(s.distribution == "wake_time" for s in samples)
        samples = [s for s in samples if s.distribution != "wake_time"]
        if dropped:
            log.info("wake_time 은 밤잠의 sleep_end 로 학습 — wake 레코드 %d건 미사용", dropped)

    return samples + series["sleep_time"] + series["wake_time"]


def load_times() -> np.ndarray:
    """
    breakfast_preparation.csv의 시각을
    자정 기준 분(minute) 단위로 읽는다.
    """

    return kde_samples.values(
        load_aruba_samples()
    )


def fit_kde(
    times: np.ndarray,
    weights: np.ndarray | None = None,
):
    """
    weights 가 없으면 기존 무가중치 KDE 다.
    weights 가 있으면 bandwidth 도 유효 표본 수 기준으로 계산된다 (scipy).
    """

    times = np.asarray(times, dtype=float)

    if len(times) < MIN_SAMPLES:
        raise InsufficientSamples(
            f"KDE 계산을 위한 데이터가 부족합니다 ({len(times)}개)."
        )

    if not np.isfinite(times).all():
        raise ValueError(
            "KDE 입력에 NaN/inf가 있습니다."
        )

    if weights is not None:
        weights = np.asarray(weights, dtype=float)

        if weights.shape != times.shape:
            raise ValueError("weights 길이가 표본과 다릅니다.")

        if not np.isfinite(weights).all() or (weights <= 0).any():
            raise ValueError("weights는 양수 유한 값이어야 합니다.")

    try:
        return gaussian_kde(times, weights=weights)
    except np.linalg.LinAlgError as exc:
        # 값이 전부 같으면 분산이 0이라 bandwidth를 정할 수 없다.
        raise InsufficientSamples(
            "KDE 입력의 분산이 0입니다 (모든 값이 같음)."
        ) from exc


def build_density(
    kde,
    grid: Grid = TIME_OF_DAY_GRID,
    period: float | None = None,
) -> list[float]:
    """
    격자 각 칸 중앙에서 KDE density를 계산한다.
    기본은 하루를 15분 단위 96칸으로 나눈 격자다.

    period를 주면 원(circular) 위의 density다.
    각 칸에 한 바퀴씩 옮긴 위치의 density를 더해 자정에서 끊기지 않게 한다.

    최종 배열의 합은 1.0이 되도록 정규화한다.
    """

    centers = grid.centers()

    if period is None:
        density = kde(centers)
    else:
        density = sum(
            kde(centers + k * period)
            for k in WRAP_SHIFTS
        )

    density_sum = density.sum()

    if not np.isfinite(density_sum) or density_sum <= 0:
        raise InsufficientSamples(
            "격자 범위 안의 KDE density 합이 0입니다."
        )

    normalized_density = (
        density / density_sum
    )

    return normalized_density.tolist()


def fit_distribution(
    distribution: str,
    values: np.ndarray,
    hydration_max_min: float = HYDRATION_GRID_MAX_MIN,
    weights: np.ndarray | None = None,
) -> tuple[Grid, list[float]]:
    """
    distribution 하나의 공통 KDE fitting.
    출처와 무관하게 숫자 배열만 받는다.

    - time_of_day (wake/sleep/meal): circular KDE. 1440분 주기.
    - elapsed (hydration_lag): 직선 KDE. 경과 시간은 이어지지 않는다.

    여기서 다루는 것은 fitting의 circularity뿐이다.
    개입용 tail probability의 circular 정의는 Context Engine
    model.tail_probability(wrap=...)의 미결 사항으로 남긴다.
    """

    grid = grid_for(distribution, hydration_max_min)
    values = np.asarray(values, dtype=float)

    if not kde_samples.is_time_of_day(distribution):
        return grid, build_density(fit_kde(values, weights), grid)

    # 펼치기 전에 개수·NaN 검증을 먼저 받는다.
    if len(values) < MIN_SAMPLES or not np.isfinite(values).all():
        fit_kde(values, weights)

    # 펼쳐도 표본 순서는 그대로라 weights 를 그대로 쓴다.
    density = build_density(
        fit_kde(unwrap_circular(values), weights),
        grid,
        period=PERIOD_MIN,
    )

    return grid, density


def calculate_entropy(
    density: list[float],
) -> float:
    """정규화된 density 배열의 Shannon entropy (nats)"""

    probability = np.array(density)

    probability = probability[
        probability > 0
    ]

    return float(
        -np.sum(probability * np.log(probability))
    )


def calculate_predictability(
    density: list[float],
) -> float:
    """
    정규화된 density 배열의 entropy를 이용해
    predictability를 계산한다.

        predictability = 1 - H / H_max

    H_max는 그 격자의 칸 수 기준이다 (시각 분포 96칸, hydration 24칸).
    칸 수가 다르면 H_max가 달라 distribution 간 값을 직접 비교할 수 없다.

    1에 가까울수록 규칙적,
    0에 가까울수록 불규칙.
    """

    entropy = calculate_entropy(density)

    max_entropy = np.log(len(density))

    normalized_entropy = (
        entropy / max_entropy
    )

    predictability = (
        1 - normalized_entropy
    )

    return float(predictability)


def build_model(
    samples: list[KdeSample] | None = None,
    hydration_max_min: float = HYDRATION_GRID_MAX_MIN,
    sample_weighting: SampleWeighting | None = None,
    cold_start: ColdStart | None = None,
) -> dict:
    """
    MQTT payload에 들어갈 KDE 모델 부분을 생성한다.

    samples는 출처(Aruba / synthetic / t0)와 무관한 공통 KdeSample이다.
    없으면 기존 Aruba breakfast 데이터(meal_time만)를 사용한다.

    표본이 없거나 KDE를 계산할 수 없는 distribution은 빼고 skipped에 이유를 남긴다.
    빈 density를 보내면 Context Engine이 payload 전체를 거부하기 때문이다.

    반환값 중 sample_days / distributions / predictability가 payload에 들어간다.
    meta / skipped는 검증·보고용이며 MQTT로 나가지 않는다.

    v2 Phase 11 (모두 기본 꺼짐):
      sample_weighting  recent weighting / prompted attenuation
      cold_start        prior와 섞기. prior가 없는 distribution은 개인 분포 그대로
    predictability는 최종(섞은 뒤) density로 계산한다.
    """

    if samples is None:
        samples = load_aruba_samples()

    groups = kde_samples.group(samples)

    distributions = {}
    predictability = {}
    meta = {}
    skipped = {}
    used: list[KdeSample] = []

    for name in kde_samples.DISTRIBUTION_KIND:
        items = groups.get(name, [])

        if not items:
            skipped[name] = "표본 없음"
            continue

        weights = weighting.sample_weights(items, sample_weighting)

        try:
            grid, density = fit_distribution(
                name,
                kde_samples.values(items),
                hydration_max_min,
                weights,
            )
        except InsufficientSamples as exc:
            skipped[name] = str(exc)
            continue

        days = kde_samples.sample_days(items)
        info = {
            "samples": len(items),
            "sample_days": days,
            "sources": sorted({s.source for s in items}),
            "proxy": any(s.proxy for s in items),
            "prompted": sum(s.prompted for s in items),
        }

        if weights is not None:
            info["weighting"] = {
                "recent_lambda": sample_weighting.recent_lambda,
                "prompted_weight": sample_weighting.prompted_weight,
                "effective_samples": round(weighting.effective_samples(weights), 3),
            }

        if cold_start is not None:
            prior = cold_start.priors.get(name)

            if prior is None:
                info["cold_start"] = {"alpha": 1.0, "prior": False}
            else:
                alpha = weighting.blend_alpha(days, cold_start.half_days)
                density = weighting.blend(density, prior, alpha)
                info["cold_start"] = {"alpha": round(alpha, 6), "prior": True}

        distributions[name] = {
            **grid.to_payload(),
            "density": density,
        }
        predictability[name] = calculate_predictability(density)
        meta[name] = info
        used += items

    for name, reason in skipped.items():
        log.info("%s 제외 — %s", name, reason)

    if not distributions:
        raise InsufficientSamples(
            f"학습 가능한 distribution이 없습니다: {skipped}"
        )

    return {
        "sample_days": kde_samples.sample_days(used),
        "distributions": distributions,
        "predictability": predictability,
        "meta": meta,
        "skipped": skipped,
    }


def add_input_arguments(parser: argparse.ArgumentParser) -> None:
    """학습 입력 CLI 옵션. baseline / model_payload / mqtt_publisher가 공유한다."""

    parser.add_argument(
        "--t0-jsonl",
        type=Path,
        help="Context Engine t0 JSONL 경로 (meal / wake / hydration / sleep_start / sleep_end)",
    )
    parser.add_argument(
        "--aruba-raw",
        type=Path,
        help="Aruba 원본(aruba.txt) — meal_time / sleep_time·wake_time(proxy)",
    )
    parser.add_argument(
        "--synthetic-hydration",
        action="store_true",
        help="검증용 synthetic hydration_lag 추가",
    )
    parser.add_argument("--synthetic-days", type=int, default=60)
    parser.add_argument("--synthetic-mean", type=float, default=15)
    parser.add_argument("--synthetic-std", type=float, default=5)
    parser.add_argument("--synthetic-seed", type=int, default=42)


def samples_from_args(args) -> list[KdeSample] | None:
    """
    CLI 입력 조합. 아무 옵션도 없으면 None(기존 Aruba breakfast)이다.
    --aruba-raw / --synthetic-hydration 은 개발·검증용이다.
    """

    collected: list[KdeSample] = []

    if args.t0_jsonl:
        collected += t0_records_to_kde_samples(load_t0_records(args.t0_jsonl))

    if args.aruba_raw:
        from aruba import extract_samples

        for series in extract_samples(args.aruba_raw).values():
            collected += series


    if args.synthetic_hydration:
        from synthetic import generate_hydration_lag

        collected += generate_hydration_lag(
            sample_days=args.synthetic_days,
            mean_min=args.synthetic_mean,
            std_min=args.synthetic_std,
            seed=args.synthetic_seed,
            max_min=HYDRATION_GRID_MAX_MIN,
        )

    return collected or None


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    add_input_arguments(parser)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)

    model = build_model(samples_from_args(args))

    print(
        json.dumps(
            model,
            indent=2,
            ensure_ascii=False,
        )
    )
