"""개입 판정.

명세: 단순히 시간이 늦다는 이유만으로 개입하지 않고,
     사용자의 패턴 자체가 충분히 규칙적인 경우에만 개입 후보로 판단한다.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from .clock import Clock
from .config import Config
from .context import AwayContext, OccupancyContext, SuppressionContext
from .model import MealPeak, ModelStore, peak_tail, query
from .timeutil import minutes_since_midnight

log = logging.getLogger(__name__)


# 왜 개입하지 않았는지를 설명한다. 대시보드가 이것을 보여준다.
NO_MODEL = "NO_KDE_MODEL"           #모델 없음
NOT_HOME = "NOT_HOME"               #사용자 외출중
MULTI_OCCUPANT = "MULTI_OCCUPANT"   #집안에 사람 여러명
ALREADY_DONE = "ALREADY_DONE"       #이미 해당 활동 함
SUPPRESSED = "SUPPRESSED"           #쿨다운/프로브 등으로 억제 중
TIME_NORMAL = "TIME_NORMAL"         #평소 시각임 (꼬리확률이 임계 위)
PATTERN_UNRELIABLE = "PATTERN_UNRELIABLE"   #사용자의 패턴이 불규칙함
ANOMALY = "TIME_ANOMALY"            #이때만 개입 후보 (유일하게 candidate=True)


@dataclass(frozen=True, slots=True)
class Decision:
    """개입 판정 결과."""

    candidate: bool
    reason: str
    kind: str                                   # meal / wake / hydration
    tail_probability: float | None = None
    predictability: float | None = None
    confidence: float = 0.0
    factors: dict[str, Any] = field(default_factory=dict)

    def payload(self) -> dict[str, Any]:
        return {
            "candidate": self.candidate,
            "reason": self.reason,
            "kind": self.kind,
            "tail_probability": self.tail_probability,
            "predictability": self.predictability,
            "confidence": round(self.confidence, 3),
            "factors": dict(self.factors),
        }


class InterventionPolicy:       # 개입 관련 정책
    """분포와 context 를 읽어 개입 여부를 정한다."""

    def __init__(self, clock: Clock, config: Config, store: ModelStore) -> None:
        self._clock = clock
        self._config = config
        self._store = store

    @property
    def store(self) -> ModelStore:
        """시나리오가 peaks 를 읽는다."""
        return self._store

    def evaluate_meal(
        self,
        away: AwayContext,
        occupancy: OccupancyContext,
        suppression: SuppressionContext,
        meal_done: bool,
        *,
        peak: MealPeak | None = None,
        scenario: str = "MEAL_PROMPT",
    ) -> Decision:
        """식사 시각이 평소보다 이상하게 늦은가."""
        return self._evaluate(
            kind="meal",
            dist_name="meal_time",
            threshold_key="meal",
            away=away,
            occupancy=occupancy,
            suppression=suppression,
            done=meal_done,
            scenario=scenario,
            peak=peak,
        )

    def evaluate_wake(
        self,
        away: AwayContext,
        occupancy: OccupancyContext,
        suppression: SuppressionContext,
        *,
        scenario: str = "WAKE_ROUTINE",
    ) -> Decision:
        """기상 시각이 평소보다 늦은가. 자정 기준 분포."""
        return self._evaluate(
            kind="wake",
            dist_name="wake_time",
            threshold_key="wake",
            away=away,
            occupancy=occupancy,
            suppression=suppression,
            done=False,
            scenario=scenario,
        )


    # ------------------------------------------------------------ 내부

    def _evaluate(
        self,
        *,
        kind: str,                          # 어떤 개일인지, 예: "meal"
        dist_name: str,                     # 분포 이름, 예: "meal_time"
        threshold_key: str,                 # policy.toml에서 임계값을 꺼낼 키, 예: "meal"
        away: AwayContext,                  # 외출 여부 판정 결과, 예: HOME, AWAY
        occupancy: OccupancyContext,        # 몇 명인지, 예: SINGLE
        suppression: SuppressionContext,    # 알림 억제 중인지
        done: bool,                         # 오늘 이미 했는지
        scenario: str,                      # 억제 예외 확인용 시나리오 이름, 예: "MEDICATION_PROMPT"
        since: float | None = None,         # 상대 분포의 기준 시각
        peak: MealPeak | None = None,       # 끼니 구간. 주면 그 안에서만 본다
    ) -> Decision:
        now = self._clock.now()
        factors: dict[str, Any] = {}        # 판단 근거를 쌓는 dict

        # 기준점이 자정인 분포와 다른 사건인 분포를 가른다.
        # hydration_lag 는 "기상 후 몇 분" 이라 since 가 기상 시각이다.
        if since is None:
            minutes = minutes_since_midnight(now)
        else:
            minutes = (now - since) / 60.0

        # ① 모델 확인: 모델이 없으면 비교할 기준이 없음
        result = query(self._store, dist_name, minutes)
        if result is None:
            return Decision(False, NO_MODEL, kind, factors=factors)

        factors["minutes"] = minutes
        factors["sample_days"] = result["sample_days"]
        factors["trained_at"] = result["trained_at"]

        # 2~5는 현재 분포를 보기 전 미리 거르는 항목들
        # ② 외출 중이면 집 안 채널로 보내도 무의미
        if away.state != "HOME":
            factors["away_state"] = away.state
            return Decision(False, NOT_HOME, kind, factors=factors)

        # ③ MULTI 판정 시 개인 행위 추론과 개인화 알림 중단
        if occupancy.state == "MULTI":
            return Decision(False, MULTI_OCCUPANT, kind, factors=factors)

        # ④ 이미 했는가
        if done:
            return Decision(False, ALREADY_DONE, kind, factors=factors)

        # ⑤ 쿨다운·프로브·억제.
        if not suppression.allows(scenario):
            factors["suppression_reason"] = suppression.reason
            return Decision(False, SUPPRESSED, kind, factors=factors)

        # 분포 값 꺼내기
        if peak is None:
            tail = result["tail_probability"]
            pred = result["predictability"]
        else:
            # 구간 안에서 다시 정규화한다. 하루 전체 기준이면
            # 아침 봉우리를 한참 지나도 저녁 질량 때문에 tail 이 크다.
            dist = self._store.distribution(dist_name)
            tail = peak_tail(dist, peak, minutes)
            pred = peak.predictability
            factors["peak_center"] = peak.center
            factors["peak_from"] = peak.from_
            factors["peak_to"] = peak.to
            factors["days_ratio"] = peak.days_ratio

        factors["tail_probability"] = round(tail, 4)
        factors["predictability"] = None if pred is None else round(pred, 4)

        # 임계값 조회
        tail_max = float(
            self._config.value("thresholds", threshold_key, "tail_max", default=0.05)
        )
        pred_min = float(
            self._config.value(
                "thresholds", threshold_key, "predictability_min", default=0.05
            )
        )

        # ⑥ 불규칙한 사용자 — 비교 기준 자체가 없다
        if pred is None or pred < pred_min:
            return Decision(False, PATTERN_UNRELIABLE, kind, tail, pred, 0.0, factors)

        # ⑦ 평소 시각이면 개입하지 않는다
        if tail >= tail_max:
            return Decision(False, TIME_NORMAL, kind, tail, pred, 0.0, factors)

        # 개입 여부 판단 결과
        return Decision(
            candidate=True,
            reason=ANOMALY,
            kind=kind,
            tail_probability=tail,
            predictability=pred,
            confidence=self._confidence(tail, pred, tail_max, pred_min),
            factors=factors,
        )

    @staticmethod
    def _confidence(tail: float, pred: float, tail_max: float, pred_min: float) -> float:
        """얼마나 확신하는가.

        임계를 겨우 넘은 것과 한참 넘은 것은 다르다.
        명세: confidence 와 결과를 함께 보면 확신도 구간별 성공률을 계산할 수 있다. 낮은 구간의 성공률이 저조하면 임계값을 올린다.
        """
        # tail_margin: 얼마나 늦었는가 / tail 값이 임계값 대비 얼마나 작은지를 0~1로 변환
        # pred_margin: 얼마나 규칙적인가 / 임계를 얼마나 넘겼는지를 임계값 자체로 나눔
        tail_margin = 1.0 - (tail / tail_max) if tail_max > 0 else 0.0
        pred_margin = min(1.0, (pred - pred_min) / max(pred_min, 0.01))
        return max(0.0, min(1.0, 0.5 * tail_margin + 0.5 * pred_margin))