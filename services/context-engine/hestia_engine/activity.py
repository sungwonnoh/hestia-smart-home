"""Activity — 무엇을 하고 있는가.

presence 와 성격이 다르다. 센서가 "사람이 있다"는 직접 말해주지만
"식사 중"이라고 말해주는 센서는 없다. 여러 신호를 모아 추론해야 한다.

12종 각각에 점수를 매기고 가장 높은 것을 고른다.
if-else 사슬이 아닌 이유:
  - 조건 순서가 결과를 바꾼다. 상태가 늘면 순서 정하기가 설계 결정이 된다.
  - 1등과 나머지의 격차가 곧 confidence 다.

가전이 결정적이다. WATCHING_TV / RESTING 은 센서 신호가 동일하고
TV 전원과 remote_input 으로만 갈린다.

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
    "SLEEPING", "IN_BED_AWAKE", "WAKING",
    "MEAL_PREP", "EATING", "KITCHEN_MISC",
    "BATHROOM",
    "WATCHING_TV", "RESTING", "LAUNDRY",
    "AWAY", "UNKNOWN",
)

# HMM 학습용 축약 (8종). 전이행렬을 3주 데이터로 채우려면 묶어야 한다.
HMM_STATES = {
    "SLEEPING": "SLEEPING",
    "IN_BED_AWAKE": "SLEEPING",
    "WAKING": "WAKING",
    "MEAL_PREP": "MEAL",
    "EATING": "MEAL",
    "KITCHEN_MISC": "MEAL",
    "BATHROOM": "BATHROOM",
    "WATCHING_TV": "RESTING",
    "RESTING": "RESTING",
    "LAUNDRY": "LAUNDRY",
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
    """12종 점수를 계산하고 하나를 고른다."""

    def __init__(self, clock: Clock, config: Config, world: WorldState) -> None:
        self._clock = clock
        self._config = config
        self._world = world

    def evaluate(
        self,
        presence: PresenceContext,
        away: AwayContext,
        prev: ActivityContext | None,
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

        area = presence.user_area
        hour = datetime.fromtimestamp(now, KST).hour
        night = self._is_night(hour)
        factors["hour"] = hour

        # ---- 수면·기상
        bed = self._bed_in("bedroom")
        if bed is not None and bed.occupied:
            bed_sec = bed.occupied_sec(now)
            still_sec = self._world.still_sec("bedroom")
            tv_idle = self._world.since_any_event("smart_tv", "remote_input")
            still_need = float(self._config.value("activity", "sleep", "still_for_sleep_sec", default=600))
            tv_need = float(self._config.value("activity", "sleep", "tv_idle_for_sleep_sec", default=7200))

            factors["bed_occupied_sec"] = round(bed_sec)
            factors["bedroom_still_sec"] = round(still_sec)
            factors["tv_idle_sec"] = None if tv_idle is None else round(tv_idle)

            sleeping = w("SLEEPING", "bed_occupied")
            if still_sec >= still_need:
                sleeping += w("SLEEPING", "still_sustained")
            if tv_idle is not None and tv_idle >= tv_need:
                sleeping += w("SLEEPING", "tv_idle")
            if night:
                sleeping += w("SLEEPING", "night_hours")
            s["SLEEPING"] = sleeping

            awake = w("IN_BED_AWAKE", "bed_occupied")
            if still_sec < still_need:
                awake += w("IN_BED_AWAKE", "moving")
            if not night:
                awake += w("IN_BED_AWAKE", "not_night")
            s["IN_BED_AWAKE"] = awake

        elif bed is not None and not bed.occupied:
            # 침대를 떠난 직후 — WAKING
            left_sec = now - bed.changed_at
            confirm = float(self._config.value("activity", "sleep", "wake_confirm_sec", default=300))
            factors["bed_left_sec"] = round(left_sec)
            if left_sec <= confirm * 3:
                waking = w("WAKING", "bed_left_recent")
                if area == "bedroom":
                    waking += w("WAKING", "bedroom_motion")
                if 4 <= hour < 11:
                    waking += w("WAKING", "morning_hours")
                s["WAKING"] = waking

        # ---- 식사
        if presence.areas.get("kitchen"):
            cooking = self._world.any_power_on("MEAL")
            dwell = self._world.dwell_sec("kitchen")
            off_sec = self._cooking_off_sec(now)
            fridge = self._world.since_any_event("smart_fridge", "door_opened")
            fridge_window = float(
                self._config.value("activity", "meal", "fridge_recent_window_sec", default=900)
            )
            gap_need = float(
                self._config.value("activity", "meal", "prep_to_eating_gap_sec", default=180)
            )
            dwell_need = float(
                self._config.value("activity", "meal", "eating_min_dwell_sec", default=300)
            )
            fridge_recent = fridge is not None and fridge <= fridge_window

            factors["cooking_on"] = cooking
            factors["kitchen_dwell_sec"] = round(dwell)
            factors["cooking_off_sec"] = None if off_sec is None else round(off_sec)
            factors["fridge_recent"] = fridge_recent

            if cooking:
                prep = w("MEAL_PREP", "cooking_on") + w("MEAL_PREP", "kitchen_present")
                if fridge_recent:
                    prep += w("MEAL_PREP", "fridge_recent")
                s["MEAL_PREP"] = prep

            # 명세: 조리 기구 전력 종료 후 해당 구역 체류 지속이 주 신호.
            # 냉저고·정수기 접근과 mmWave 의 energy 패턴이 보조 신호.
            if not cooking and off_sec is not None and off_sec >= gap_need:
                eating = w("EATING", "cooking_off_recent")
                if dwell >= dwell_need:
                    eating += w("EATING", "kitchen_dwell")
                if self._low_energy("kitchen"):
                    eating += w("EATING", "low_energy")
                if fridge_recent:
                    eating += w("EATING", "fridge_recent")
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

            resting = w("RESTING", "living_present")
            if sofa is not None and sofa.occupied:
                resting += w("RESTING", "bed_occupied")
            if not tv_on:
                resting += w("RESTING", "tv_off")
            s["RESTING"] = resting

        # ---- 세탁
        washer = self._washer()
        if washer is not None:
            cycle = washer.get("cycle")
            running = cycle in ("WASH", "RINSE", "SPIN")
            factors["washer_cycle"] = cycle
            if running:
                laundry = w("LAUNDRY", "washer_running")
                if presence.areas.get("utility"):
                    laundry += w("LAUNDRY", "utility_motion")
                s["LAUNDRY"] = laundry

        return s

    # ------------------------------------------------------------ 선택

    def _pick(self, scores: dict[str, float], factors: dict[str, Any]) -> tuple[str, float]:
        """최댓값을 고르되 임계 미달이면 UNKNOWN.

        confidence 는 전체 합 대비 비율이다. 후보가 15개나 되므로
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

    def _low_energy(self, area: str) -> bool:
        still_max = float(self._config.value("presence", "energy", "still_max", default=10))
        active_min = float(self._config.value("presence", "energy", "active_min", default=30))
        for st in self._world.sensors_of(area):
            if isinstance(st, PresenceState) and st.present:
                return st.energy < active_min
        return False

    def _cooking_off_sec(self, now: float) -> float | None:
        """조리 기구가 꺼진 뒤 경과. 켜져 있거나 쓴 적 없으면 None.

        여럿이면 가장 최근에 꺼진 것을 쓴다.
        """
        from .world import PowerState

        best: float | None = None
        for st in self._world.sensors_by_role("MEAL"):
            if isinstance(st, PowerState) and st.state != "ON" and st.changed_at > 0.0:
                gap = now - st.changed_at
                best = gap if best is None else min(best, gap)
        return best

    def _tv(self):
        devices = self._world.devices_of_type("smart_tv")
        return devices[0] if devices else None

    def _washer(self):
        devices = self._world.devices_of_type("washer")
        return devices[0] if devices else None