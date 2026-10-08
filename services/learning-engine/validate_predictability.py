"""
Predictability 재검증 (KDE_REFACTOR_TASKS_v2 Phase 7).

현재 모델과 같은 함수(fit_distribution / calculate_predictability)로
predictability = 1 - H / H_max 가 규칙성을 제대로 표현하는지 확인한다.

    1. time-of-day synthetic sweep (std 10 ~ 120분)
    2. 표본 수에 따른 변화
    3. Aruba 실제 데이터 (meal / breakfast / wake·sleep proxy)
    4. hydration_lag regular / irregular (synthetic)
    5. CASAS 거주자별 sleep / wake (Aruba / Milan / Tulum2 / Cairo, proxy)

seed를 고정하므로 같은 환경에서 다시 돌리면 같은 표가 나온다.
임계값(policy.toml)은 바꾸지 않는다. 결과는 판단 근거로만 쓴다.

실행 (저장소 루트에서):

    python3 services/learning-engine/validate_predictability.py \\
        --output services/learning-engine/results/predictability.md
"""

from __future__ import annotations

import argparse
import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from baseline import (
    DATA_PATH,
    HYDRATION_GRID_MAX_MIN,
    calculate_entropy,
    calculate_predictability,
    fit_distribution,
    grid_for,
    load_aruba_samples,
    unwrap_circular,
)
from samples import is_time_of_day, values
from synthetic import generate_hydration_lag


RAW_PATH = Path("data/raw/casas/aruba/aruba.txt")

# 기존 검증과 비교할 수 있도록 같은 조건을 쓴다.
SWEEP_STDS = (10, 30, 45, 60, 75, 90, 120)
SWEEP_CENTER = 7 * 60 + 30           # 07:30
SWEEP_DAYS = 212                     # Aruba breakfast 일수
SAMPLE_SIZES = (14, 30, 60, 212)     # 14 = activity.baseline.min_sample_days
SAMPLE_SIZE_STDS = (30, 60)
HYDRATION_DAYS = 60
HYDRATION_CASES = (
    ("regular", 15, 5),
    ("irregular", 15, 30),
)
DEFAULT_SEEDS = 20


@dataclass(frozen=True)
class Measure:
    """한 번의 fitting 결과"""

    actual_std: float
    entropy: float
    predictability: float


@dataclass(frozen=True)
class Row:
    label: str
    n: int
    target_std: float | None
    actual_std: float
    actual_std_sd: float
    entropy: float
    predictability: float
    predictability_sd: float
    h_max: float


def measure(distribution: str, data: np.ndarray) -> Measure:
    """
    distribution 하나를 현재 모델과 같은 방식으로 fitting한다.
    actual std 는 시각 분포면 원을 펼친 값의 std 다.
    """

    _, density = fit_distribution(distribution, data)

    spread = unwrap_circular(data) if is_time_of_day(distribution) else data

    return Measure(
        actual_std=float(np.std(spread)),
        entropy=calculate_entropy(density),
        predictability=calculate_predictability(density),
    )


def summarize(
    label: str,
    distribution: str,
    runs: list[Measure],
    n: int,
    target_std: float | None,
) -> Row:
    return Row(
        label=label,
        n=n,
        target_std=target_std,
        actual_std=float(np.mean([r.actual_std for r in runs])),
        actual_std_sd=float(np.std([r.actual_std for r in runs])),
        entropy=float(np.mean([r.entropy for r in runs])),
        predictability=float(np.mean([r.predictability for r in runs])),
        predictability_sd=float(np.std([r.predictability for r in runs])),
        h_max=math.log(grid_for(distribution).size),
    )


def clock_samples(center: float, std: float, n: int, seed: int) -> np.ndarray:
    """하루 위 정규분포 표본. 범위를 넘으면 자르지 않고 원을 따라 넘어간다."""

    return np.random.default_rng(seed).normal(center, std, n) % 1440


# ==================================================================== 실험


def time_of_day_sweep(
    seeds: int = DEFAULT_SEEDS,
    center: float = SWEEP_CENTER,
    n: int = SWEEP_DAYS,
) -> list[Row]:
    rows = []

    for std in SWEEP_STDS:
        runs = [
            measure("meal_time", clock_samples(center, std, n, seed))
            for seed in range(seeds)
        ]
        rows.append(summarize(f"std {std}", "meal_time", runs, n, std))

    return rows


def sample_size_sweep(seeds: int = DEFAULT_SEEDS) -> list[Row]:
    rows = []

    for std in SAMPLE_SIZE_STDS:
        for n in SAMPLE_SIZES:
            runs = [
                measure("meal_time", clock_samples(SWEEP_CENTER, std, n, seed))
                for seed in range(seeds)
            ]
            rows.append(summarize(f"std {std}, n {n}", "meal_time", runs, n, std))

    return rows


def midnight_check(seeds: int = DEFAULT_SEEDS) -> list[tuple[int, float, float]]:
    """같은 std 를 07:30 과 00:00 중심으로 — circular 이면 같아야 한다."""

    out = []

    for std in (30, 60):
        day, night = (
            np.mean([
                measure("meal_time", clock_samples(center, std, SWEEP_DAYS, seed)).predictability
                for seed in range(seeds)
            ])
            for center in (SWEEP_CENTER, 0)
        )
        out.append((std, float(day), float(night)))

    return out


def aruba_rows(
    raw_path: Path | None = RAW_PATH,
    breakfast_path: Path = DATA_PATH,
) -> tuple[list[Row], str | None]:
    """
    Aruba 실제 데이터. 원본(aruba.txt)은 gitignore라 없으면 breakfast만 계산한다.
    """

    rows = []
    breakfast = values(load_aruba_samples(breakfast_path))
    rows.append(
        summarize(
            "meal_time — breakfast (05~11시 첫 식사)",
            "meal_time",
            [measure("meal_time", breakfast)],
            len(breakfast),
            None,
        )
    )

    if raw_path is None or not raw_path.exists():
        return rows, f"Aruba 원본 없음 ({raw_path}) — meal 전체 / wake / sleep 생략"

    from aruba import extract_samples

    series = extract_samples(raw_path)

    for name, label in (
        ("meal_time", "meal_time — 전체 Meal_Preparation"),
        ("wake_time", "wake_time (proxy)"),
        ("sleep_time", "sleep_time (proxy)"),
    ):
        data = values(series[name])
        rows.append(summarize(label, name, [measure(name, data)], len(data), None))

    return rows, None


def hydration_rows(seeds: int = DEFAULT_SEEDS) -> list[Row]:
    rows = []

    for label, mean, std in HYDRATION_CASES:
        runs = []

        for seed in range(seeds):
            data = values(
                generate_hydration_lag(
                    HYDRATION_DAYS, mean, std, seed=seed, max_min=HYDRATION_GRID_MAX_MIN,
                )
            )
            runs.append(measure("hydration_lag", data))

        rows.append(
            summarize(f"{label} (mean {mean})", "hydration_lag", runs, HYDRATION_DAYS, std)
        )

    return rows


def casas_rows(raw_dir: Path | None) -> tuple[list[dict], str | None]:
    """
    CASAS 거주자별 실제 sleep / wake predictability.
    한 사람(Aruba)만으로는 규칙적 / 불규칙한 사람의 차이를 실제 데이터로 볼 수 없다.
    """

    if raw_dir is None:
        return [], "CASAS 원본 경로 없음 — 생략"

    import casas

    found = casas.extract_all(raw_dir)

    if not found:
        return [], f"CASAS 원본 없음 ({raw_dir}) — 생략"

    rows = []

    for spec, sleep in found:
        r = casas.person_summary(spec, sleep)

        for name, samples in (("sleep_time", sleep.sleep_time), ("wake_time", sleep.wake_time)):
            r[f"{name}_std"] = float(np.std(unwrap_circular(values(samples)))) if samples else None

        r["note"] = spec.note
        rows.append(r)

    return rows, None


def casas_table(rows: list[dict]) -> str:
    out = [
        "| 거주자 | 밤 | 낮잠 | 제외 (≥24h) | 수면 중앙값 (h) | sleep 실제 std (분) | sleep_time | wake 실제 std (분) | wake_time |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]

    def f(v, digits):
        return "—" if v is None else f"{v:.{digits}f}"

    for r in rows:
        out.append(
            f"| {r['key']} | {r['nights']} | {r['naps']} | {r['implausible']} | {r['median_hours']:.1f} "
            f"| {f(r['sleep_time_std'], 1)} | {f(r['sleep_time'], 3)} "
            f"| {f(r['wake_time_std'], 1)} | {f(r['wake_time'], 3)} |"
        )

    return "\n".join(out)


# ==================================================================== 출력


def _fmt_sd(mean: float, sd: float, digits: int) -> str:
    if sd == 0:
        return f"{mean:.{digits}f}"
    return f"{mean:.{digits}f} ± {sd:.{digits}f}"


def table(rows: list[Row], show_target: bool = True) -> str:
    head = "| 조건 | n | 지정 std | 실제 std (분) | entropy (nats) | predictability |"
    line = "|---|---:|---:|---:|---:|---:|"

    if not show_target:
        head = "| 데이터 | n | 실제 std (분) | entropy (nats) | predictability |"
        line = "|---|---:|---:|---:|---:|"

    out = [head, line]

    for r in rows:
        cells = [r.label, str(r.n)]
        if show_target:
            cells.append("—" if r.target_std is None else f"{r.target_std:g}")
        cells += [
            _fmt_sd(r.actual_std, r.actual_std_sd, 1),
            f"{r.entropy:.3f}",
            _fmt_sd(r.predictability, r.predictability_sd, 3),
        ]
        out.append("| " + " | ".join(cells) + " |")

    return "\n".join(out)


def render(
    seeds: int = DEFAULT_SEEDS,
    raw_path: Path | None = RAW_PATH,
    breakfast_path: Path = DATA_PATH,
) -> str:
    """raw_path 와 같은 디렉터리의 다른 CASAS 원본도 쓴다 (5절)."""
    sweep = time_of_day_sweep(seeds)
    sizes = sample_size_sweep(seeds)
    midnight = midnight_check(seeds)
    aruba, aruba_note = aruba_rows(raw_path, breakfast_path)
    people, casas_note = casas_rows(raw_path.parent if raw_path is not None else None)
    hydration = hydration_rows(seeds)

    h_day = math.log(grid_for("meal_time").size)
    h_hyd = math.log(grid_for("hydration_lag").size)

    parts = [
        "# Predictability 검증 결과",
        "",
        "`validate_predictability.py` 로 생성한 파일이다. 직접 고치지 말고 스크립트를 다시 실행한다.",
        "",
        "- 정의: `predictability = 1 - H / H_max`",
        f"- 시각 분포: 15분 96칸, circular KDE, H_max = ln 96 = {h_day:.3f}",
        f"- hydration_lag: 5분 {grid_for('hydration_lag').size}칸 (0~{HYDRATION_GRID_MAX_MIN}분), "
        f"직선 KDE, H_max = ln {grid_for('hydration_lag').size} = {h_hyd:.3f}",
        f"- synthetic 은 seed 0~{seeds - 1} ({seeds}회) 평균 ± 표준편차",
        "- bandwidth 는 gaussian_kde 기본값(Scott)",
        "",
        "## 1. 시각 분포 synthetic sweep",
        "",
        f"중심 07:30, n = {SWEEP_DAYS}. 범위를 넘는 값은 원을 따라 넘어간다.",
        "",
        table(sweep),
        "",
        "### 자정 중심 비교",
        "",
        "같은 std 를 00:00 중심으로 만들면 circular KDE 에서는 07:30 중심과 같아야 한다.",
        "",
        "| 지정 std | 07:30 중심 | 00:00 중심 |",
        "|---:|---:|---:|",
        *[f"| {std} | {day:.3f} | {night:.3f} |" for std, day, night in midnight],
        "",
        "## 2. 표본 수에 따른 변화",
        "",
        "Scott bandwidth 는 n 이 작을수록 넓어진다. 같은 규칙성이라도 학습 초기에는 predictability 가 낮게 나온다.",
        "",
        table(sizes),
        "",
        "## 3. Aruba 실제 데이터",
        "",
        "wake_time / sleep_time 은 Aruba `Sleeping` 라벨로 만든 proxy 다 (HESTIA t0 아님).",
        "",
        table(aruba, show_target=False),
        "",
    ]

    if aruba_note:
        parts += [f"> {aruba_note}", ""]

    parts += [
        "## 4. hydration_lag regular / irregular (synthetic)",
        "",
        f"n = {HYDRATION_DAYS}, 0~{HYDRATION_GRID_MAX_MIN}분 밖은 재추출. "
        "재추출로 분포가 비대칭이 되어 실제 std 가 지정 std 와 다르다.",
        "",
        table(hydration),
        "",
        "> hydration_lag 는 칸 수(24)가 시각 분포(96)와 달라 H_max 가 다르다. "
        "predictability 값을 시각 분포와 직접 비교하지 않는다.",
        "",
        "## 5. CASAS 거주자별 sleep / wake (proxy)",
        "",
        "Aruba 한 사람만으로는 규칙적 / 불규칙한 사람의 차이를 실제 데이터로 볼 수 없어 "
        "다른 CASAS 데이터셋의 거주자를 더했다. 밤마다 가장 긴 수면을 밤잠으로 골라 "
        "취침(sleep_time) / 기상(wake_time) 을 학습한다. 24시간 이상 수면은 기록 오류로 제외.",
        "",
    ]

    if casas_note:
        parts += [f"> {casas_note}", ""]
    else:
        parts += [
            casas_table(people),
            "",
            "- Tulum2 / Cairo 는 2인 가구다. 거주자별 라벨을 따로 쓴다.",
            "- Cairo 의 `R1_Sleep` / `R1_Wake` 는 잠자리에 드는 / 일어나는 짧은 활동이다. "
            "취침 활동 시작 ~ 다음 기상 활동 시작을 수면으로 해석했다.",
            "- 실제 std 는 원을 펼친 값의 표준편차다. 1절 synthetic 표와 같은 기준으로 비교할 수 있다.",
            "",
        ]

    return "\n".join(parts)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="predictability 재검증")
    parser.add_argument("--seeds", type=int, default=DEFAULT_SEEDS)
    parser.add_argument("--raw", type=Path, default=RAW_PATH, help="Aruba 원본 경로")
    parser.add_argument("--output", type=Path, help="markdown 저장 경로 (없으면 화면 출력)")
    args = parser.parse_args()

    text = render(args.seeds, args.raw)

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
        print(f"[DONE] {args.output}")
    else:
        print(text)
