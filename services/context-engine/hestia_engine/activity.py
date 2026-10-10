"""Activity — 무엇을 하고 있는가.

presence 와 성격이 다르다. 센서가 "사람이 있다"는 직접 말해주지만
"식사 중"이라고 말해주는 센서는 없다. 여러 신호를 모아 추론해야 한다.

10종 각각에 점수를 매기고 가장 높은 것을 고른다.
if-else 사슬이 아닌 이유:
  - 조건 순서가 결과를 바꾼다. 상태가 늘면 순서 정하기가 설계 결정이 된다.
  - 1등과 나머지의 격차가 곧 confidence 다.

가전과 압력 패드가 결정적이다. 거실에 있다는 사실만으로는 상태가 정해지지
않고, TV 전원과 소파 압력 패드로 WATCHING_TV / IN_SOFA_AWAKE 가 갈린다.
둘 다 없으면 UNKNOWN 이다.

UNKNOWN 이 자주 나오는 것은 실패가 아니다 (명세: open-set).
모르면 모른다고 하는 쪽이, 억지로 고르고 그 위에 개입을 얹는 것보다 낫다.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from .clock import Clock
from .config import Config
from .context import AwayContext, Context, PresenceContext
from .timeutil import KST
from .world import BedState, PresenceState, WorldState

log = logging.getLogger(__name__)

# 명세의 activity state. TRANSITION / ACTIVE / FOCUSED 는 쓰지 않는다 —
# 셋 다 '다른 걸로 설명 안 되는 나머지'라 UNKNOWN 과 구별되지 않았다.
ACTIVITY_STATES = (
    "SLEEPING", "IN_BED_AWAKE", "IN_SOFA_AWAKE",
    "COOKING", "EATING", "KITCHEN_MISC",
    "BATHROOM",
    "WATCHING_TV",
    "AWAY", "UNKNOWN",
)

# HMM 학습용 축약 (6종). 전이행렬을 3주 데이터로 채우려면 묶어야 한다.
HMM_STATES = {
    "SLEEPING": "SLEEPING",
    "IN_BED_AWAKE": "SLEEPING",
    "IN_SOFA_AWAKE": "SLEEPING",
    "COOKING": "MEAL",
    "EATING": "MEAL",
    "KITCHEN_MISC": "MEAL",
    "BATHROOM": "BATHROOM",
    "WATCHING_TV": "RESTING",
    "AWAY": "AWAY",
    "UNKNOWN": "OTHER",
}


@dataclass(frozen=True, slots=True)
class ActivityContext(Context):
    state: str = "UNKNOWN"
    t0: float | None = None        # 활동 묶음 시작. FSM(2-9)이 채운다
    area: str | None = None
    scores: dict[str, float] = field(default_factory=dict)

    def payload(self, now: float) -> dict[str, Any]:
        return {
            "state": self.state,
            "since": self.since,
            "t0": self.t0,
            "area": self.area,
            "confidence": round(self.confidence, 3),
            "factors": dict(self.factors),
        }

    def same_as(self, other: Context | None) -> bool:
        return isinstance(other, ActivityContext) and self.state == other.state


class ActivityEvaluator:
    """10종 점수를 계산하고 하나를 고른다."""

    def __init__(self, clock: Clock, config: Config, world: WorldState, models: Any | None = None,) -> None:
        self._clock = clock
        self._config = config
        self._world = world
        self._models = models

    def evaluate(
        self,
        presence: PresenceContext,
        away: AwayContext,
        prev: ActivityContext | None,
        asleep_area: str | None = None,
    ) -> tuple[ActivityContext, list[tuple[str, float]]]:
        now = self._clock.now()
        timers: list[tuple[str, float]] = []
        factors: dict[str, Any] = {}

        # AWAY 는 계산하지 않는다. away context 를 그대로 따른다.
        if away.state == "AWAY":
            return self._build("AWAY", None, {}, 1.0, factors, prev, now), timers

        if away.state == "SENSOR_FAULT":
            factors["sensor_fault"] = True
            return self._build("UNKNOWN", None, {}, 0.0, factors, prev, now), timers

        scores = self._score_all(presence, factors, now)

        # 수면은 점수로 정하지 않는다. SLEEP_ROUTINE 이 프로브로 확정한 것만 SLEEPING 이다 — 침실이든 소파든 같은 경로를 탄다.
        if asleep_area is not None:
            factors["asleep_area"] = asleep_area
            scores["SLEEPING"] = 1.0

        # 히스테리시스 — 현재 상태를 유지하는 쪽에 가산점.
        # 0.52 / 0.51 에서 초마다 뒤집히는 것을 막는다.
        if prev is not None and prev.state in scores:
            bonus = float(self._config.value("activity", "hysteresis_bonus", default=0.1))
            scores[prev.state] += bonus
            factors["hysteresis_on"] = prev.state

        state, confidence = self._pick(scores, factors)

        # 최소 지속 — 바꾼 지 얼마 안 됐으면 유지한다.
        # 단 UNKNOWN 에서 벗어나는 것은 막지 않는다. UNKNOWN 은 상태가 아니라
        # 상태의 부재이고, '모름 → 앎'은 떨림이 아니라 개선이다.
        min_hold = float(self._config.value("activity", "min_hold_sec", default=60))
        if (
            prev is not None
            and prev.state != state
            and prev.state != "UNKNOWN"
            and now - prev.since < min_hold
        ):
            timers.append(("activity-hold", prev.since + min_hold))
            factors["held_from"] = state
            state = prev.state
            confidence = prev.confidence

        return self._build(state, presence.user_area, scores, confidence, factors, prev, now), timers

    # ------------------------------------------------------------ 점수

    def _score_all(
        self, presence: PresenceContext, factors: dict[str, Any], now: float
    ) -> dict[str, float]:
        w = self._weights
        s: dict[str, float] = {}

        hour = datetime.fromtimestamp(now, KST).hour
        night = self._is_night(hour)
        factors["hour"] = hour

        # ---- 수면·기상
        bed = self._bed_in("bedroom")
        if bed is not None and bed.occupied:
            still_sec = self._world.still_sec("bedroom")
            still_need = float(self._config.value(
                "activity", "sleep", "still_for_sleep_sec", default=600
            ))
            factors["bed_occupied_sec"] = round(bed.occupied_sec(now))
            factors["bedroom_still_sec"] = round(still_sec)

            awake = w("IN_BED_AWAKE", "bed_occupied")
            if still_sec < still_need:
                awake += w("IN_BED_AWAKE", "moving")
            if not night:
                awake += w("IN_BED_AWAKE", "not_night")
            s["IN_BED_AWAKE"] = awake

       
        # ---- 식사
        meal_areas = self._config.areas_with_role("MEAL")
        if any(presence.areas.get(a) for a in meal_areas):
            cooking = self._world.any_power_on("MEAL")
            dwell = max((self._world.dwell_sec(a) for a in meal_areas), default=0.0)
            off_sec = self._power_off_sec("MEAL", now)
            heat_sec = self._power_off_sec("MEAL_HEAT", now)
            fridge = self._world.since_any_event("smart_fridge", "door_opened")

            fridge_window = float(
                self._config.value("activity", "meal", "fridge_recent_window_sec", default=900)
            )
            gap_need = float(
                self._config.value("activity", "meal", "prep_to_eating_gap_sec", default=180)
            )
            off_window = float(
                self._config.value("activity", "meal", "cooking_off_window_sec", default=3600)
            )
            heat_window = float(
                self._config.value("activity", "meal", "heated_window_sec", default=900)
            )
            dwell_need = float(
                self._config.value("activity", "meal", "eating_min_dwell_sec", default=300)
            )
            fridge_recent = fridge is not None and fridge <= fridge_window

            factors["cooking_on"] = cooking
            factors["meal_area_dwell_sec"] = round(dwell)
            factors["cooking_off_sec"] = None if off_sec is None else round(off_sec)
            factors["heated_sec"] = None if heat_sec is None else round(heat_sec)
            factors["fridge_recent"] = fridge_recent

            # 개인 분포에서 지금이 어디쯤인가.
            # 분포가 activity 를 움직이고, activity 가 t0 를 만들고,
            # t0 가 분포를 만든다. 되먹임을 막는 장치(낮은 가중치,
            # sample_days 하한, predictability 하한)가 갖춰진 뒤에 켠다.
            pct = self._percentile("meal_time", now)
            if pct is not None:
                factors["meal_time_percentile"] = round(pct, 3)

            if cooking:
                prep = w("COOKING", "cooking_on") + w("COOKING", "kitchen_present")
                if fridge_recent:
                    prep += w("COOKING", "fridge_recent")
                s["COOKING"] = prep

            # 명세: 체류 지속이 주 신호, 조리 기구·냉장고가 보조.
            # 가산 점수다 — 조리 종료를 관문으로 두면 조리하지 않는
            # 끼니가 영영 잡히지 않는다.
            if not cooking:
                eating = 0.0
                if dwell >= dwell_need:
                    eating += w("EATING", "meal_area_dwell")
                if self._low_energy_in(meal_areas):
                    # 설거지와 가르는 신호다. 머무는 것만으로는
                    # 서서 치우는 것과 구별되지 않는다.
                    eating += w("EATING", "low_energy")
                if off_sec is not None and gap_need <= off_sec <= off_window:
                    eating += w("EATING", "cooking_off_recent")
                if heat_sec is not None and heat_sec <= heat_window:
                    # 데우는 것은 조리가 아니다. COOKING 을 만들지 않고
                    # 창도 짧다 — 꺼내서 바로 먹는다.
                    eating += w("EATING", "heated_recent")
                if fridge_recent:
                    eating += w("EATING", "fridge_recent")
                if eating > 0:
                    s["EATING"] = eating

            # 조리도 식사도 아닌 주방 체류. t0 오염을 막는 자리다 —
            # 물 마시러 30초 들른 것이 EATING 으로 잡히면 KDE 분포가 망가진다.
            s["KITCHEN_MISC"] = w("KITCHEN_MISC", "kitchen_present")

 
        # ---- 위생
        if presence.areas.get("bathroom"):
            s["BATHROOM"] = w("BATHROOM", "bathroom_present")

        # ---- 거실
        if presence.areas.get("living"):
            tv = self._tv()
            tv_on = tv is not None and tv.get("power") == "ON"
            remote = self._world.since_any_event("smart_tv", "remote_input")
            sofa = self._bed_in("living")

            factors["tv_power"] = None if tv is None else tv.get("power")
            factors["tv_remote_sec"] = None if remote is None else round(remote)

            if tv_on:
                watching = w("WATCHING_TV", "tv_on") + w("WATCHING_TV", "living_present")
                if remote is not None and remote < 3600:
                    watching += w("WATCHING_TV", "remote_recent")
                s["WATCHING_TV"] = watching

            # 소파 압력 패드. 침대와 같은 type="bed" 이고 area 로 갈린다.
            # 패드가 없는 거실은 TV 가 꺼져 있으면 UNKNOWN.
            if sofa is not None and sofa.occupied:
                still_sec = self._world.still_sec("living")
                still_need = float(self._config.value(
                    "activity", "sleep", "still_for_sleep_sec", default=600
                ))
                factors["sofa_occupied_sec"] = round(sofa.occupied_sec(now))
                factors["living_still_sec"] = round(still_sec)
                awake = w("IN_SOFA_AWAKE", "bed_occupied") + w("IN_SOFA_AWAKE", "living_present")
                if still_sec < still_need:
                    awake += w("IN_SOFA_AWAKE", "moving")
                s["IN_SOFA_AWAKE"] = awake

        return s

    # ------------------------------------------------------------ 선택

    def _pick(self, scores: dict[str, float], factors: dict[str, Any]) -> tuple[str, float]:
        """최댓값을 고르되 임계 미달이면 UNKNOWN.

        confidence 는 전체 합 대비 비율이다. 후보가 10개나 되므로
        2등만 보는 것보다 전체 분포를 보는 쪽이 맞다.
        """
        if not scores:
            return "UNKNOWN", 0.0

        state, top = max(scores.items(), key=lambda kv: kv[1])
        min_score = float(self._config.value("activity", "min_score", default=0.35))
        factors["top_score"] = round(top, 3)

        if top < min_score:
            factors["below_min_score"] = True
            return "UNKNOWN", 0.0

        total = sum(scores.values())
        share = top / total if total > 0 else 0.0
        factors["score_share"] = round(share, 3)
        return state, min(1.0, top) * share

    def _build(
        self,
        state: str,
        area: str | None,
        scores: dict[str, float],
        confidence: float,
        factors: dict[str, Any],
        prev: ActivityContext | None,
        now: float,
    ) -> ActivityContext:
        since = prev.since if prev is not None and prev.state == state else now
        t0 = prev.t0 if prev is not None and prev.state == state else None
        return ActivityContext(
            name="activity",
            since=since,
            confidence=confidence,
            factors=factors,
            state=state,
            t0=t0,
            area=area,
            scores={k: round(v, 3) for k, v in scores.items()},
        )

    # ------------------------------------------------------------ 보조

    def _percentile(self, dist_name: str, now: float) -> float | None:
        """개인 분포에서 현재 시각의 백분위. 모델이 없으면 None."""
        if self._models is None:
            return None
        from .model import percentile
        from .timeutil import minutes_since_midnight

        dist = self._models.distribution(dist_name)
        if dist is None:
            return None
        return percentile(dist, minutes_since_midnight(now))

    def _weights(self, state: str, key: str) -> float:
        return float(self._config.value("activity", "weights", state, key, default=0.0))

    def _is_night(self, hour: int) -> bool:
        start = int(self._config.value("activity", "sleep", "night_start_hour", default=21))
        end = int(self._config.value("activity", "sleep", "night_end_hour", default=7))
        return hour >= start or hour < end

    def _bed_in(self, area: str) -> BedState | None:
        for st in self._world.sensors_of(area):
            if isinstance(st, BedState):
                return st
        return None

    def _power_off_sec(self, role: str, now: float) -> float | None:
        """그 역할의 전력이 꺼진 뒤 경과. 켜져 있거나 쓴 적 없으면 None.

        여럿이면 가장 최근에 꺼진 것을 쓴다. 밥솥의 보온(STANDBY)도
        꺼진 것으로 본다 — 취사가 끝났다는 뜻이다.
        """
        from .world import PowerState

        best: float | None = None
        for st in self._world.sensors_by_role(role):
            if isinstance(st, PowerState) and st.state != "ON" and st.changed_at > 0.0:
                gap = now - st.changed_at
                best = gap if best is None else min(best, gap)
        return best

    def _low_energy_in(self, areas: tuple[str, ...]) -> bool:
        return any(self._low_energy(a) for a in areas)

    def _low_energy(self, area: str) -> bool:
        still_max = float(self._config.value("presence", "energy", "still_max", default=10))
        active_min = float(self._config.value("presence", "energy", "active_min", default=30))
        for st in self._world.sensors_of(area):
            if isinstance(st, PresenceState) and st.present:
                return st.energy < active_min
        return False


    def _tv(self):
        devices = self._world.devices_of_type("smart_tv")
        return devices[0] if devices else None
