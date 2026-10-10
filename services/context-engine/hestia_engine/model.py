"""학습 모델 보관과 분포 조회."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from .messages import ModelMessage
from .timeutil import MINUTES_PER_DAY

log = logging.getLogger(__name__)

MODEL_NAMES = ("kde", "hmm", "classifier")

# 명세의 KDE 분포 넷
DISTRIBUTIONS = ("wake_time", "sleep_time", "meal_time", "hydration_lag")

# 자정이 아닌 다른 시점을 기준으로 하는 분포. 격자 길이 검사에서 뺀다.
RELATIVE_DISTRIBUTIONS = frozenset({"hydration_lag"})


class ModelError(ValueError):
    """모델 페이로드가 명세를 만족하지 않음."""


# ==================================================================== 끼니 구간

@dataclass(frozen=True, slots=True)
class MealPeak:
    """meal_time 의 끼니 구간. - 배치가 봉우리를 찾아 보냄"""

    center: int                      # 식사가 가장 몰린 시각 (자정 기준 분)
    from_: int                       # 구간 시작. 포함
    to: int                          # 구간 끝. 미포함
    predictability: float | None     # 그 구간만으로 다시 그린 KDE. 2개 미만이면 None
    days_ratio: float                # 그 구간에 식사가 있었던 날 / sample_days
    meals_per_day: float             # 그 구간 식사 수 / sample_days

    def contains(self, minutes: int) -> bool:
        """자정을 넘는 구간은 from > to 로 표현된다."""
        if self.from_ <= self.to:
            return self.from_ <= minutes < self.to
        return minutes >= self.from_ or minutes < self.to

    def bins(self, grid_step: int, n: int) -> list[int]:
        """이 구간에 속하는 density 칸 번호들. 순환을 편다.
           from 부터 시작해 to 까지, 자정을 넘으면 0으로 돌아간다.
        """
        out: list[int] = []
        i = self.from_ // grid_step
        for _ in range(n):
            m = (i % n) * grid_step
            if not self.contains(m):
                break
            out.append(i % n)
            i += 1
        return out


# ==================================================================== 보관

class ModelStore:
    """최신 모델을 메모리에 보관한다.
        모델의 갱신 주기
        kde         하루 1회
        hmm         주 1회
        classifier  개입 발생 시
    """

    def __init__(self) -> None:
        self._models: dict[str, ModelMessage] = {}

    def apply(self, msg: ModelMessage) -> bool:
        """수신한 모델을 보관한다. 검증에 실패하면 교체하지 않는다. (깨진 모델로 교체하지 않고, 직전 모델을 유지)
        """
        if msg.name not in MODEL_NAMES:
            log.warning("알 수 없는 모델 이름: %s", msg.name)
            return False

        try:
            if msg.name == "kde":
                validate_kde(msg.payload)
        except ModelError as exc:
            log.warning("모델 교체 거부 — %s. 직전 모델을 유지한다.", exc)
            return False

        if msg.name == "kde":
            self._drop_bad_peaks(msg.payload)

        self._models[msg.name] = msg
        log.info("모델 갱신: %s trained_at=%s", msg.name, msg.trained_at)
        return True

    @staticmethod
    def _drop_bad_peaks(payload: dict[str, Any]) -> None:
        """깨진 peaks 는 버리되 분포는 쓴다.
        """
        for name, dist in payload.get("distributions", {}).items():
            if "peaks" not in dist:
                continue
            try:
                validate_peaks(name, dist["peaks"])
            except ModelError as exc:
                log.warning("peaks 버림 — %s. 분포는 유지한다.", exc)
                del dist["peaks"]

    def get(self, name: str) -> ModelMessage | None:
        return self._models.get(name)

    def has(self, name: str) -> bool:
        return name in self._models

    def trained_at(self, name: str) -> float | None:        # 해당 모델이 학습된 시각(epoch 초)를 반환
        msg = self._models.get(name)
        return msg.trained_at if msg else None

    def distribution(self, name: str) -> dict[str, Any] | None:
        """KDE 의 분포 하나. 모델이 없거나 그 분포가 없으면 None."""
        kde = self._models.get("kde")
        if kde is None:
            return None
        return kde.payload.get("distributions", {}).get(name)

    def sample_days(self) -> int | None:
        """학습에 쓰인 일수. 적으면 콜드스타트 블렌딩 비중이 높다."""
        kde = self._models.get("kde")
        return None if kde is None else kde.payload.get("sample_days")

    def peaks(self, name: str = "meal_time") -> tuple[MealPeak, ...] | None:
        """끼니 구간들. 분포가 없거나 peaks 필드가 없으면 None.
           빈 튜플은 '판단할 끼니가 없다' 는 의미 — 봉우리가 전부 걸러진 경우
        """
        dist = self.distribution(name)
        if dist is None or "peaks" not in dist:
            return None
        return tuple(
            MealPeak(
                center=int(p["center"]),
                from_=int(p["from"]),
                to=int(p["to"]),
                predictability=(
                    None if p.get("predictability") is None
                    else float(p["predictability"])
                ),
                days_ratio=float(p.get("days_ratio", 0.0)),
                meals_per_day=float(p.get("meals_per_day", 0.0)),
            )
            for p in dist["peaks"]
        )

    def __repr__(self) -> str:
        return f"ModelStore({', '.join(sorted(self._models)) or 'empty'})"


# ==================================================================== 검증


def validate_kde(payload: dict[str, Any]) -> None:
    """hestia/model/kde 의 distributions 를 검증한다.
    """
    dists = payload.get("distributions")        # 페이로드의 "distributions" 필드의 목록을 딕셔너리로 가짐
    if not isinstance(dists, dict) or not dists:
        raise ModelError("distributions 가 비었거나 객체가 아님")

    for name, dist in dists.items():        #name 예: "wake_time", "sleep_time", "meal_time", "hydration_lag"
        validate_distribution(name, dist)

    pred = payload.get("predictability")
    if pred is not None and not isinstance(pred, dict):
        raise ModelError("predictability 는 객체여야 함")


def validate_distribution(name: str, dist: Any) -> None:
    """격자 정의(distributions)와 density 배열 길이가 일치하는지 본다.
    """
    if not isinstance(dist, dict):
        raise ModelError(f"{name}: 객체가 아님")

    for field in ("grid_min", "grid_step", "density"):
        if field not in dist:
            raise ModelError(f"{name}.{field} 누락")

    step = dist["grid_step"]
    if not isinstance(step, (int, float)) or step <= 0:
        raise ModelError(f"{name}.grid_step 은 양수여야 함: {step!r}")

    density = dist["density"]
    if not isinstance(density, list) or not density:
        raise ModelError(f"{name}.density 가 비었거나 배열이 아님")

    if any(not isinstance(v, (int, float)) for v in density):
        raise ModelError(f"{name}.density 에 숫자가 아닌 값이 있음")

    if any(v < 0 for v in density):
        raise ModelError(f"{name}.density 에 음수가 있음")

    # 자정 기준 분포는 하루를 덮어야 한다.
    # hydration_lag 처럼 기준점이 다른 것은 제외한다.
    if name not in RELATIVE_DISTRIBUTIONS:
        expected = int(MINUTES_PER_DAY // step)
        if len(density) != expected:
            raise ModelError(
                f"{name}.density 길이 불일치: {len(density)}칸, "
                f"grid_step={step} 이면 {expected}칸이어야 함"
            )

    total = sum(density)
    if total <= 0:
        raise ModelError(f"{name}.density 합이 0 이하")


def validate_peaks(name: str, peaks: Any) -> None:
    """끼니 구간. 하루를 다 덮지 않아도 되지만 겹치면 안 된다."""
    if not isinstance(peaks, list):
        raise ModelError(f"{name}.peaks 는 배열이어야 함")

    seen: list[MealPeak] = []
    for i, p in enumerate(peaks):
        if not isinstance(p, dict):
            raise ModelError(f"{name}.peaks[{i}] 는 객체여야 함")

        for field in ("center", "from", "to"):
            v = p.get(field)
            if not isinstance(v, (int, float)) or not 0 <= v < 1440:
                raise ModelError(f"{name}.peaks[{i}].{field} 범위 밖: {v!r}")

        peak = MealPeak(
            center=int(p["center"]), from_=int(p["from"]), to=int(p["to"]),
            predictability=None, days_ratio=0.0, meals_per_day=0.0,
        )
        if not peak.contains(peak.center):
            raise ModelError(
                f"{name}.peaks[{i}]: center 가 구간 밖 "
                f"({peak.center} not in [{peak.from_}, {peak.to}))"
            )

        pred = p.get("predictability")
        if pred is not None and not (
            isinstance(pred, (int, float)) and 0.0 <= pred <= 1.0
        ):
            raise ModelError(f"{name}.peaks[{i}].predictability 범위 밖: {pred!r}")

        ratio = p.get("days_ratio", 0.0)
        if not isinstance(ratio, (int, float)) or not 0.0 <= ratio <= 1.0:
            raise ModelError(f"{name}.peaks[{i}].days_ratio 범위 밖: {ratio!r}")

        mpd = p.get("meals_per_day", 0.0)
        if not isinstance(mpd, (int, float)) or mpd < 0:
            raise ModelError(f"{name}.peaks[{i}].meals_per_day 범위 밖: {mpd!r}")

        for other in seen:
            if _overlaps(peak, other):
                raise ModelError(f"{name}.peaks[{i}] 가 앞 구간과 겹침")
        seen.append(peak)


def _overlaps(a: MealPeak, b: MealPeak) -> bool:
    """15분 격자를 훑어 두 구간이 같은 칸을 차지하는지 본다."""
    return any(a.contains(m) and b.contains(m) for m in range(0, 1440, 15))


# ==================================================================== 조회


def grid_slot(dist: dict[str, Any], minutes: float) -> int:
    """현재 시간(분) → density 배열 인덱스(배열의 몇 번째 칸인지)
       density 배열은 시간대별 확률
       경계는 내림으로 처리 (grid_step=15 일 때 585분(09:45)은 38번이 아니라 39번)
    """
    step = float(dist["grid_step"])     # "grid_step": 15라면, 한 칸이 15분
    start = float(dist.get("grid_min", 0))
    index = int((minutes - start) // step)      #현재 시간이 배열의 몇 번째 칸인지
    return max(0, min(index, len(dist["density"]) - 1))     #density 배열의 범위를 넘어가지 않게 자르기


def tail_probability(
    dist: dict[str, Any],
    minutes: float,
    *,
    window_min: float | None = None,
    wrap: bool = False,
) -> float:
    """그 시각 이후에 활동이 일어날 확률.

    density 는 합이 1 로 정규화되어 들어온다 (baseline.py 가 보장, 단순 누적합이 곧 확률)

    window_min — 적분 범위를 제한한다. 다봉은 peak_tail이 다룬다.

    wrap — 자정을 넘는 분포용. 
        sleep_time 은 23:40 과 00:20 이 같은
        봉우리인데 배열 양 끝으로 갈라진다. 
        다만 한 바퀴를 전부 더하면 합이 1 이라 무의미하므로, 봉우리 기준 반 바퀴를 보는 식의 설계가 필요하다. sleep_time 도입 시 채운다.
    """
    density = dist["density"]
    start = grid_slot(dist, minutes)        # minutes는 조회할 시각(자정 기준 분)

    if window_min is not None:              # window_min은 적분 범위 제한(분)
        step = float(dist["grid_step"])
        span = max(1, int(window_min // step))
        end = min(len(density), start + span)
        return float(sum(density[start:end]))

    if wrap:                                # wrap은 순환 처리 여부(True, False)
        raise NotImplementedError(
            "순환 꼬리확률은 sleep_time 도입 시 구현한다. "
            "봉우리 기준 구간을 정하는 설계가 선행되어야 한다."
        )

    return float(sum(density[start:]))


def peak_tail(dist: dict[str, Any], peak: MealPeak, minutes: float) -> float:
    """끼니 구간 안에서의 꼬리확률.

       분모가 그 구간 전체이므로 빈도는 지워진다. 가끔 먹는 끼니를 거르는 것은 배치의 days_ratio 가 한다.
    """
    density = dist["density"]
    step = int(dist["grid_step"])
    bins = peak.bins(step, len(density))
    if not bins:
        return 1.0

    total = sum(density[i] for i in bins)
    if total <= 0:
        return 1.0

    now_bin = grid_slot(dist, minutes)
    # 순환 구간에서는 인덱스 비교가 아니라 구간 안 순서를 봐야 한다
    try:
        pos = bins.index(now_bin)
    except ValueError:
        return 1.0                      # 구간 밖

    return float(sum(density[i] for i in bins[pos:]) / total)


def percentile(dist: dict[str, Any], minutes: float) -> float:
    """그 시각까지 누적된 확률. tail_probability 의 여집합이다.

        tail       "이 시각이 이상하게 늦은가"      → 개입 판정
        percentile "평소 활동 시간대인가"           → activity 점수 보조
    """
    return 1.0 - tail_probability(dist, minutes)


def predictability(store: ModelStore, name: str) -> float | None:
    """그 분포의 예측 가능성. 배치가 계산한 값을 읽는다.

    1 - H/H_max 로 정규화되어 있으나 실질 범위가 0~0.4 에 눌려 있다
    (H_max 가 하루 전체 균등분포 기준이라 비교가 느슨하다).
    임계값은 실측 기준으로 잡아야 한다 — Aruba 212일이 0.330 이다.

    명세: predictability 가 낮은 항목은 개입 임계값을 넓히거나 개입을 생략한다.
    """
    kde = store.get("kde")
    if kde is None:
        return None
    value = kde.payload.get("predictability", {}).get(name)
    return None if value is None else float(value)


def query(store: ModelStore, name: str, minutes: float) -> dict[str, Any] | None:
    """분포 하나에 대한 조회 결과를 묶어 돌려준다.
       모델이 없거나 그 분포가 없으면 None — 호출부가 "모른다" 와 "정상이다" 를 구별
    """
    dist = store.distribution(name)
    if dist is None:
        return None

    tail = tail_probability(dist, minutes)
    return {
        "tail_probability": tail,
        "percentile": 1.0 - tail,
        "predictability": predictability(store, name),
        "sample_days": store.sample_days(),
        "trained_at": store.trained_at("kde"),
    }

