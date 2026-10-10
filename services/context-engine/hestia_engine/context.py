"""Context — World State 를 읽어 판단한다.

여기서 처음으로 '판단'이 들어간다. (World State 는 "vs-04 가 present=true" 까지만 알고, 이 층이 그것을 "사람이 주방에 있다"로 바꾼다.)

설계: World State 가 유일한 상태이고 context 는 그 함수다.
매 호출마다 처음부터 다시 계산하며, 직전 결과는 비교용으로만 들고 있다.

recompute()  — 계산만 하고 발행하지 않는다. 그래서 몇 번 불려도 결과가 같고, 타이머와 메시지가 거의 동시에 와도 중복 발행되지 않는다.

시간 기반 전이(PIR 무반응, AWAY 등)는 메시지가 '안 와서' 참이 되므로 타이머로 재계산을 예약한다. 타이머 시각은 반드시 **근거 시각 + N** 이다.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field, replace
from typing import Any, Callable

from .clock import Clock
from .config import Config
from .timers import Scheduler
from .world import (
    BedState,
    DoorState,
    MotionState,
    PresenceState,
    WorldState,
)

log = logging.getLogger(__name__)


# ==================================================================== 기반


@dataclass(frozen=True, slots=True)
class Context:
    """hestia/context/{name} 으로 발행되는 판단 결과."""

    name: str       #activity / presence / day / away / occupancy / suppression
    since: float    #해당 상태가 시작된 시각
    confidence: float = 1.0     #신뢰도(이 판단을 얼마나 믿을 수 있는가, 0~1)
    factors: dict[str, Any] = field(default_factory=dict)       #confidence 산출에 기여한 근거값

    def payload(self, now: float) -> dict[str, Any]:
        """ Envelope + 타입별 필드. 
            src_id 는 발행부가 채운다."""
        raise NotImplementedError

    def same_as(self, other: Context | None) -> bool:
        """발행할지 판단한다. since·confidence·factors 는 비교에서 뺀다.
        """
        raise NotImplementedError


@dataclass(frozen=True, slots=True)
class Evidence:
    """재실의 근거 한 건. (어느 센서가 왜 그렇게 말하는가)"""

    vid: str           # 센서의 virtual_id
    kind: str          # mmwave_active / mmwave_still / bed / pir_recent / pir_stale
    score: float
    since: float       # 이 근거가 생긴 시각 — since 승계의 원천


def _combine(scores: list[float], method: str) -> float:
    """근거가 여럿일 때 합치는 방식.
    max 는 가장 강한 근거 하나를 쓴다.

    noisy_or 는 센서별 오탐률을 측정한 뒤에 적용, 지금은 자리만 열어둔다.
    """
    if not scores:
        return 0.0
    if method == "noisy_or":
        product = 1.0
        for s in scores:
            product *= 1.0 - s
        return 1.0 - product
    return max(scores)      #confidence 계산 방법에 규칙 표 방식을 쓴다면, scores 중 가장 큰 값 반환


# ==================================================================== presence


@dataclass(frozen=True, slots=True)
class PresenceContext(Context):
    user_area: str | None = None               # 사용자의 현재 위치. 판정 불가면 None
    areas: dict[str, bool] = field(default_factory=dict)   # 구역별 재실 맵(이구역에 사람이 있으면 true, 없으면 false)

    def payload(self, now: float) -> dict[str, Any]:
        return {
            "user_area": self.user_area,
            "since": self.since,
            "areas": dict(self.areas),
            "confidence": round(self.confidence, 3),
            "factors": dict(self.factors),
        }

    def same_as(self, other: Context | None) -> bool:
        if not isinstance(other, PresenceContext):
            return False
        return self.user_area == other.user_area and self.areas == other.areas  #직전 PresenceContext와 새로 계산한 PresenceContext에서 user_area가 같은 값인지 비교


class PresenceEvaluator:
    """구역별 재실과 사용자 위치를 판정한다.

    mmWave 와 PIR 을 다르게 믿는 것이 핵심이다.
      mmWave (high) — present=false 를 즉시 신뢰. 정지한 사람도 감지하므로
                      안 보인다는 것은 진짜 없는 것이다.
      PIR   (low)  — false 를 믿지 않는다. 가만히 서 있으면 반응이 멎는다.
                      마지막 true 이후 N분을 기준으로 삼는다.
    """

    def __init__(self, clock: Clock, config: Config, world: WorldState) -> None:
        self._clock = clock
        self._config = config
        self._world = world

    def evaluate(self, prev: PresenceContext | None) -> tuple[PresenceContext, list[tuple[str, float]]]:
        """(context, 예약할 타이머 목록) 을 돌려준다.
        타이머는 (key, 시각) 쌍이고 호출부가 건다.
        """
        now = self._clock.now()
        timers: list[tuple[str, float]] = []
        factors: dict[str, Any] = {}

        # 구역별 근거 수집
        by_area: dict[str, list[Evidence]] = {}
        for area in self._config.areas:
            evidence = self._evidence_of(area, now, timers, factors)
            if evidence:
                by_area[area] = evidence

        areas = {area: area in by_area for area in self._config.areas}

        # 사용자 위치 — 가장 강한 근거를 가진 구역
        method = str(self._config.value("presence", "confidence", "method", default="max"))
        scored = {
            area: _combine([e.score for e in ev], method)
            for area, ev in by_area.items()
        }
        user_area, confidence, evidence_since = self._pick(scored, by_area, prev, now, timers)

        since = (
            prev.since
            if prev is not None and prev.user_area == user_area and prev.areas == areas
            else evidence_since
        )

        ctx = PresenceContext(
            name="presence",
            since=since,
            confidence=confidence,
            factors=factors,
            user_area=user_area,
            areas=areas,
        )
        return ctx, timers

    # ------------------------------------------------------------ 근거 수집

    def _evidence_of(
        self,
        area: str,
        now: float,
        timers: list[tuple[str, float]],
        factors: dict[str, Any],
    ) -> list[Evidence]:
        """한 구역의 센서들을 훑어 해당 구역에 사람이 있다는 근거를 모음
        근거가 하나도 없으면 빈 리스트(=> 무인)"""
        #policy.toml에서 임계값 읽기
        conf = self._config.value("presence", "confidence", default={})
        still_max = float(self._config.value("presence", "energy", "still_max", default=10))
        active_min = float(self._config.value("presence", "energy", "active_min", default=30))
        grace = float(self._config.value("presence", "mmwave_absent_grace_sec", default=30))
        pir_timeout = float(self._config.value("presence", "pir_absent_timeout_sec", default=300))

        offline = set(self._world.sensors_offline())
        found: list[Evidence] = []

        for st in self._world.sensors_of(area):     #st = 해당 area의 센서들의 state 객체
            if st.vid in offline:
                continue        # 죽은 노드의 센서는 판단에서 제외 (명세)

            match st:
                case PresenceState():       #mmWave
                    factors[f"{st.vid}_present"] = st.present
                    factors[f"{st.vid}_energy"] = st.energy
                    if st.present:      #present=true인 경우
                        kind = "mmwave_active" if st.energy >= active_min else "mmwave_still"
                        found.append(
                            Evidence(st.vid, kind, float(conf.get(kind, 0.9)), st.changed_at or now)
                        )
                    else:
                        # present=false 를 즉시 믿되, grace 만큼은 유예한다. (30초 유예)
                        # 노드 재부팅 직후의 한 건을 걸러내는 용도.
                        if st.changed_at > 0.0 and now - st.changed_at < grace:
                            found.append(
                                Evidence(
                                    st.vid, "mmwave_still",
                                    float(conf.get("mmwave_still", 0.85)), st.changed_at,
                                )
                            )
                            timers.append((f"mmwave-{st.vid}", st.changed_at + grace))
                            #유예 시간 후(changed_at + grace)에 재계산하도록 타이머 예약

                case BedState():        #압력 패드
                    factors[f"{st.vid}_occupied"] = st.occupied
                    if st.occupied:     #압력 패드가 점유 상태이면
                        found.append(
                            Evidence(st.vid, "bed", float(conf.get("bed", 0.8)), st.changed_at or now)
                        )

                case MotionState():     #PIR
                    # PIR 은 마지막 true 이후 경과로 판단한다.
                    # motion=false 를 받아도 즉시 부재로 보지 않는다. (PIR은 정지중인 사람을 인식하지 못하기 때문)
                    last_true = self._last_motion_at(st)
                    factors[f"{st.vid}_last_motion_sec"] = (
                        None if last_true is None else round(now - last_true)
                    )
                    if last_true is None:
                        continue
                    gap = now - last_true
                    if gap < pir_timeout:
                        kind = "pir_recent" if gap < pir_timeout / 2 else "pir_stale"
                        found.append(
                            Evidence(st.vid, kind, float(conf.get(kind, 0.5)), last_true)
                        )
                        timers.append((f"pir-{st.vid}", last_true + pir_timeout))

        return found

    @staticmethod
    def _last_motion_at(st: MotionState) -> float | None:
        """마지막으로 motion=true 였던 시각.

        지금 true 면 changed_at 이고, false 면 링버퍼에서 찾는다.
        """
        if st.motion:
            return st.changed_at if st.changed_at > 0.0 else None
        for ts, value in reversed(st.history):
            if value is True:
                return ts
        return None

    # ------------------------------------------------------------ 위치 선택

    def _pick(
        self,
        scored: dict[str, float],
        by_area: dict[str, list[Evidence]],
        prev: PresenceContext | None,
        now: float,
        timers: list[tuple[str, float]],
    ) -> tuple[str | None, float, float]:
        """가장 강한 근거를 가진 구역 하나를 고른다.
        구역 전환에는 최소 지속을 둔다. 주방에서 거실 mmWave 가 벽 너머로 잡히거나 이동 중일 때 user_area 가 초마다 뒤집히는 것을 막는다.
        """
        if not scored:
            return None, 0.0, now

        ranked = sorted(scored.items(), key=lambda kv: kv[1], reverse=True)
        top_area = ranked[0][0]
        evidence_since = min(e.since for e in by_area[top_area])

        # 구역 전환 최소 지속
        switch_min = float(self._config.value("presence", "area_switch_min_sec", default=20))
        if prev is not None and prev.user_area is not None and prev.user_area != top_area:
            held = now - evidence_since
            if held < switch_min and prev.user_area in scored:
                timers.append(("area-switch", evidence_since + switch_min))
                prev_since = min(e.since for e in by_area[prev.user_area])
                # 전환 중에는 두 구역이 경합한다. 가장 불확실한 순간이므로
                # 유지하는 쪽에도 같은 페널티를 적용해야 한다.
                return prev.user_area, self._penalized(prev.user_area, scored), prev_since

        return top_area, self._penalized(top_area, scored), evidence_since

    def _penalized(self, area: str, scored: dict[str, float]) -> float:
        """2등과 붙어 있으면 확신도를 깎는다.

        top_score 만으로는 '그 구역에 사람이 있는가'까지만 말한다.
        우리가 답하는 질문은 '사용자가 거기 있는가'이고,
        다른 구역도 비슷하게 확실하면 고른 것 자체가 불확실하다.
        """
        top = scored.get(area, 0.0)
        if top <= 0.0:
            return 0.0
        others = [s for a, s in scored.items() if a != area]
        runner_up = max(others) if others else 0.0
        penalty = float(
            self._config.value("presence", "confidence", "runner_up_penalty", default=0.5)
        )
        return top * (1.0 - (runner_up / top) * penalty)

# ==================================================================== away


AWAY_STATES = ("HOME", "AWAY", "UNKNOWN", "SENSOR_FAULT")


@dataclass(frozen=True, slots=True)
class AwayContext(Context):
    state: str = "UNKNOWN"
    evidence: str | None = None        # door_exit / no_motion / lwt_offline

    def payload(self, now: float) -> dict[str, Any]:
        return {
            "state": self.state,
            "since": self.since,
            "evidence": self.evidence,
            "confidence": round(self.confidence, 3),
            "factors": dict(self.factors),
        }

    def same_as(self, other: Context | None) -> bool:
        if not isinstance(other, AwayContext):
            return False
        return self.state == other.state and self.evidence == other.evidence


class AwayEvaluator:
    """부재인가, 센서가 죽은 것인가.
    SENSOR_FAULT=> 센서가 죽은 것

    AWAY 와 UNKNOWN 을 나누는 것은 '현관 이벤트가 있었나'다.
    문이 열렸다 닫히고 전 구역이 조용하면 나간 것이고,
    문은 안 열렸는데 조용하면 자고 있을 수도 쓰러졌을 수도 있다.
    성급히 AWAY 로 가면 안전 시나리오가 전부 억제된다.
    """

    def __init__(self, clock: Clock, config: Config, world: WorldState) -> None:
        self._clock = clock
        self._config = config
        self._world = world

    def evaluate(
        self, presence: PresenceContext, prev: AwayContext | None
    ) -> tuple[AwayContext, list[tuple[str, float]]]:
        now = self._clock.now()
        timers: list[tuple[str, float]] = []
        factors: dict[str, Any] = {}

        offline = self._world.offline_nodes()
        factors["nodes_online"] = len(self._world.nodes) - len(offline)

        # ① 센서 고장이 최우선. 재실 판단 자체를 믿을 수 없다.
        if offline:
            factors["offline_nodes"] = list(offline)
            fault_since = self._fault_since(offline, prev, now)
            return self._build("SENSOR_FAULT", "lwt_offline", fault_since, 1.0, factors, prev), timers

        # ② 어느 구역이든 재실이면 HOME
        if any(presence.areas.values()):
            return self._build("HOME", None, now, presence.confidence, factors, prev), timers

        # ③ 전 구역 무반응. 현관 이벤트가 있었나로 갈린다.
        quiet_since = self._quiet_since(now)
        quiet_sec = now - quiet_since
        factors["all_areas_quiet_sec"] = round(quiet_sec)

        exit_at = self._door_exit_at(now)
        quiet_need = float(self._config.value("away", "door_exit_quiet_sec", default=600))
        unknown_need = float(self._config.value("away", "no_motion_unknown_sec", default=3600))

        if exit_at is not None:
            factors["door_exit_sec_ago"] = round(now - exit_at)
            if quiet_sec >= quiet_need:
                return self._build("AWAY", "door_exit", exit_at, 0.9, factors, prev), timers
            timers.append(("away", quiet_since + quiet_need))

        if quiet_sec >= unknown_need:
            return self._build("UNKNOWN", "no_motion", quiet_since, 0.5, factors, prev), timers

        timers.append(("away-unknown", quiet_since + unknown_need))

        # 아직 판단할 만큼 조용하지 않다. 직전 상태를 유지하되 기본은 HOME.
        keep = prev.state if prev is not None and prev.state in ("HOME", "AWAY") else "HOME"
        keep_ev = prev.evidence if prev is not None and keep == prev.state else None
        return self._build(keep, keep_ev, quiet_since, 0.4, factors, prev), timers

    # ------------------------------------------------------------ 내부

    def _build(
        self,
        state: str,
        evidence: str | None,
        evidence_ts: float,
        confidence: float,
        factors: dict[str, Any],
        prev: AwayContext | None,
    ) -> AwayContext:
        """since 는 '판단이 내려진 시각'이 아니라 '사건이 일어난 시각'이다.

        PIR 무반응으로 09:15 에 부재가 됐다면 since 는 09:10 (마지막 반응).
        우리가 5분 기다린 것뿐이고, 사람이 나간 것은 그때다.
        """
        since = prev.since if prev is not None and prev.state == state else evidence_ts
        return AwayContext(
            name="away",
            since=since,
            confidence=confidence,
            factors=factors,
            state=state,
            evidence=evidence,
        )

    def _quiet_since(self, now: float) -> float:
        """전 구역이 조용해진 시각 = 마지막 활동 시각.

        어느 센서든 마지막으로 '뭔가 있다'고 말한 때를 찾는다.
        """
        latest = 0.0
        for st in self._world.sensors.values():
            match st:
                case PresenceState() if st.present:
                    return now          # 지금 재실이면 조용하지 않다
                case PresenceState():
                    latest = max(latest, st.changed_at)
                case BedState() if st.occupied:
                    return now
                case BedState():
                    latest = max(latest, st.changed_at)
                case DoorState():
                    # 문 개폐도 활동이다. 빼면 '문만 여닫고 조용한' 상황에서
                    # quiet_since 가 엉뚱한 시각이 된다.
                    latest = max(latest, st.changed_at)
                case MotionState():
                    last = PresenceEvaluator._last_motion_at(st)
                    if last is not None:
                        latest = max(latest, last)
        return latest if latest > 0.0 else now

    def _door_exit_at(self, now: float) -> float | None:
        """현관문이 닫힌 가장 최근 시각. 창 안에 없으면 None.

        명세의 away 근거 door_exit — 현관 개폐 후 전 구역 무반응.
        """
        window = float(self._config.value("away", "door_exit_window_sec", default=1800))
        latest: float | None = None
        for vid in self._config.sensors_with_role("ENTRY"):
            st = self._world.sensor(vid)
            if isinstance(st, DoorState) and not st.open and st.changed_at > 0.0:
                if now - st.changed_at <= window:
                    latest = st.changed_at if latest is None else max(latest, st.changed_at)
        return latest

    def _fault_since(
        self, offline: tuple[str, ...], prev: AwayContext | None, now: float
    ) -> float:
        if prev is not None and prev.state == "SENSOR_FAULT":
            return prev.since
        return now


# ==================================================================== occupancy


@dataclass(frozen=True, slots=True)
class OccupancyContext(Context):
    state: str = "UNKNOWN"             # SINGLE / MULTI / UNKNOWN

    def payload(self, now: float) -> dict[str, Any]:
        return {
            "state": self.state,
            "since": self.since,
            "confidence": round(self.confidence, 3),
            "factors": dict(self.factors),
        }

    def same_as(self, other: Context | None) -> bool:
        return isinstance(other, OccupancyContext) and self.state == other.state


class OccupancyEvaluator:
    """몇 명인가.

    명세는 근거를 셋 들지만 현재 배치로 쓸 수 있는 것은 하나뿐이다.
      두 구역 동시 재실     ✅
      mmWave targets 다중   ❌ 페이로드에 필드 없음 (LD2450 필요)
      도어벨 ring 이후 증가  ❌ 도어벨 미배치

    MULTI 의 대가가 크다 — 개인 Baseline 학습 중단, 개인화 알림 중단.
    오탐이 나면 프로젝트의 차별점이 조용히 꺼진다. 보수적으로 간다.
    """

    def __init__(self, clock: Clock, config: Config, world: WorldState) -> None:
        self._clock = clock
        self._config = config
        self._world = world

    def evaluate(
        self, presence: PresenceContext, away: AwayContext, prev: OccupancyContext | None
    ) -> tuple[OccupancyContext, list[tuple[str, float]]]:
        now = self._clock.now()
        timers: list[tuple[str, float]] = []

        occupied = [a for a, v in presence.areas.items() if v]
        factors: dict[str, Any] = {
            "concurrent_areas": len(occupied),
            "mmwave_targets_max": None,      # LD2450 도입 전까지 측정 불가
            "doorbell_recent": False,        # 도어벨 미배치
        }

        if away.state in ("AWAY", "SENSOR_FAULT"):
            return self._build("UNKNOWN", now, 0.3, factors, prev), timers

        if len(occupied) < 2:
            return self._build("SINGLE", now, 0.75, factors, prev), timers

        # 두 구역 이상 동시 재실. 확신도가 충분한 것만 센다.
        min_conf = float(self._config.value("occupancy", "multi_min_confidence", default=0.8))
        strong = self._strong_areas(occupied, min_conf)
        factors["strong_areas"] = len(strong)

        if len(strong) < 2:
            # 약한 근거(PIR 등)만 겹친 것은 MULTI 근거가 되지 못한다.
            return self._build("SINGLE", now, 0.6, factors, prev), timers

        # 동시 재실이 시작된 시각 = 늦게 켜진 쪽의 시각.
        # 두 구역이 각자 changed_at 을 갖고 있을 뿐이라 World State 에 기록이 없다.
        started = max(ts for _, ts in strong)
        need = float(self._config.value("occupancy", "multi_min_sec", default=120))
        factors["concurrent_sec"] = round(now - started)

        if now - started < need:
            timers.append(("occupancy", started + need))
            return self._build("SINGLE", now, 0.5, factors, prev), timers

        return self._build("MULTI", started, 0.8, factors, prev), timers

    def _strong_areas(self, areas: list[str], min_conf: float) -> list[tuple[str, float]]:
        """확신도가 min_conf 이상인 구역과 그 근거 시작 시각.

        PIR 만 있는 구역은 최대 0.6 이라 영원히 여기 들지 못한다.
        다용도실 PIR 이 반응했다고 '두 번째 사람'으로 보면 안 된다.
        """
        conf = self._config.value("presence", "confidence", default={})
        active_min = float(self._config.value("presence", "energy", "active_min", default=30))
        out: list[tuple[str, float]] = []

        for area in areas:
            best = 0.0
            since = 0.0
            for st in self._world.sensors_of(area):
                match st:
                    case PresenceState() if st.present:
                        kind = "mmwave_active" if st.energy >= active_min else "mmwave_still"
                        score = float(conf.get(kind, 0.9))
                    case BedState() if st.occupied:
                        score = float(conf.get("bed", 0.8))
                    case _:
                        continue
                if score > best:
                    # 전환 시각을 모르면 관측 시작 시각을 쓴다.
                    best = score
                    since = st.changed_at if st.changed_at > 0.0 else self._clock.now()
            if best >= min_conf:
                out.append((area, since))
        return out

    def _build(
        self,
        state: str,
        evidence_ts: float,
        confidence: float,
        factors: dict[str, Any],
        prev: OccupancyContext | None,
    ) -> OccupancyContext:
        since = prev.since if prev is not None and prev.state == state else evidence_ts
        return OccupancyContext(
            name="occupancy",
            since=since,
            confidence=confidence,
            factors=factors,
            state=state,
        )


# ==================================================================== suppression


SUPPRESSION_REASONS = ("SLEEP_PROBE", "AWAY", "MULTI", "COOLDOWN")


@dataclass(frozen=True, slots=True)
class SuppressionContext(Context):
    """지금 알림을 보내면 안 되는 상태인가.

    다른 context 와 성격이 다르다 — 센서를 읽어 세상을 추론하는 것이 아니라
    엔진 자기 상태다. 그래서 confidence·factors 를 쓰지 않는다 (명세).

    발행은 대시보드 가시성과 재시작 복원용이고, 실제 억제는
    알림 코드가 메모리에서 allows() 로 확인한다.
    """

    active: bool = False
    reason: str | None = None
    stage: int | None = None           # SLEEP_PROBE 일 때만
    until: float | None = None         # 시한부 억제만. 조건부는 None
    except_: tuple[str, ...] = ()
    cooldowns: tuple[str, ...] = ()    # 쿨다운 중인 시나리오

    def payload(self, now: float) -> dict[str, Any]:
        return {
            "active": self.active,
            "reason": self.reason,
            "stage": self.stage,
            "until": self.until,
            "except": list(self.except_),
            "cooldowns": list(self.cooldowns),
        }

    def same_as(self, other: Context | None) -> bool:
        if not isinstance(other, SuppressionContext):
            return False
        return (
            self.active == other.active
            and self.reason == other.reason
            and self.stage == other.stage
            and self.cooldowns == other.cooldowns
        )

    def allows(self, scenario: str) -> bool:
        """이 시나리오의 알림을 지금 보내도 되는가."""
        if scenario in self.except_:
            return True                      # SAFETY 는 항상 뚫는다
        if scenario in self.cooldowns:
            return False                     # 같은 말을 반복하지 않는다
        if self.reason == "COOLDOWN":
            return True
        return not self.active


class SuppressionEvaluator:
    """억제 사유 넷을 판정한다.

    AWAY / MULTI 는 조건부 — 상태가 바뀌어야 풀린다 (until 없음).
    COOLDOWN / SLEEP_PROBE 는 시한부 — until 이 있고 타이머가 필요하다.

    SLEEP_PROBE 는 자리만 둔다. 프로브는 시나리오 로직이고,
    그 단계는 presence.user_area 로 갈린다 —
    침대면 조명·볼륨, 소파면 TV 배너. 명세의 선형 4단계는 부정확하다.
    """

    def __init__(self, clock: Clock, config: Config) -> None:
        self._clock = clock
        self._config = config
        self._cooldowns: dict[str, float] = {}      # 시나리오->쿨다운 종료 시각.
        self._probe_stage: int | None = None
        self._probe_until: float | None = None

    def evaluate(
        self,
        away: AwayContext,
        occupancy: OccupancyContext,
        prev: SuppressionContext | None,
    ) -> tuple[SuppressionContext, list[tuple[str, float]]]:
        now = self._clock.now()
        timers: list[tuple[str, float]] = []
        base = tuple(
            self._config.value("suppression", "always_except", default=["SAFETY"])
        )

        # 만료된 쿨다운을 먼저 정리한다.
        cooldowns = self._active_cooldowns(now)
        for until in self._cooldowns.values():
            timers.append(("suppression-cooldown", until))

        # 우선순위 — 더 강한 억제가 이긴다
        if self._probe_until is not None and now < self._probe_until:
            timers.append(("suppression-probe", self._probe_until))
            return self._build(
                "SLEEP_PROBE", self._probe_stage, self._probe_until,
                (*base, "SLEEP_ROUTINE"), cooldowns, prev, now,
            ), timers

        if away.state == "AWAY":
            # 집 안 채널로 보내도 무의미하다
            return self._build("AWAY", None, None, base, cooldowns, prev, now), timers

        if occupancy.state == "MULTI":
            # 명세: 개인 Baseline 학습 중단, 개인화 알림 중단
            return self._build("MULTI", None, None, base, cooldowns, prev, now), timers


        if cooldowns:
            until = max(self._cooldowns.values())
            return self._build(
                "COOLDOWN", None, until, base, cooldowns, prev, now
            ), timers

        return self._build(None, None, None, base, cooldowns, prev, now), timers

    # ------------------------------------------------------------ 알림 층이 부른다

    def note_notification(self, scenario: str, at: float | None = None) -> None:
        """알림을 발행했다. 쿨다운을 건다.
        """
        cooldown = float(
            self._config.notify_policy(scenario).get("cooldown_sec", 1800)
        )
        if cooldown <= 0:
            return                      # SAFETY 는 0 이다
        now = at if at is not None else self._clock.now()
        self._cooldowns[scenario] = now + cooldown

    def start_probe(self, stage: int, duration_sec: float) -> None:
        """수면 프로브 시작. 반응 관측이 오염되지 않도록 다른 알림을 막는다."""
        self._probe_stage = stage
        self._probe_until = self._clock.now() + duration_sec

    def end_probe(self) -> None:
        self._probe_stage = None
        self._probe_until = None

    def clear_cooldown(self, scenario: str | None = None) -> None:
        """하나 또는 전부를 푼다."""
        if scenario is None:
            self._cooldowns.clear()
        else:
            self._cooldowns.pop(scenario, None)

    # ------------------------------------------------------------ 내부

    def _active_cooldowns(self, now: float) -> tuple[str, ...]:
        """아직 유효한 쿨다운. 만료된 것은 지운다."""
        for s in [s for s, until in self._cooldowns.items() if until <= now]:
            del self._cooldowns[s]
        return tuple(sorted(self._cooldowns))

    def _build(
        self,
        reason: str | None,
        stage: int | None,
        until: float | None,
        base_except: tuple[str, ...],
        cooldowns: tuple[str, ...],
        prev: SuppressionContext | None,
        now: float,
    ) -> SuppressionContext:
        active = reason is not None
        since = (
            prev.since
            if prev is not None and prev.active == active and prev.reason == reason
            else now
        )
        return SuppressionContext(
            name="suppression",
            since=since,
            active=active,
            reason=reason,
            stage=stage,
            until=until,
            except_=base_except if (active or cooldowns) else (),
            cooldowns=cooldowns,
        )
    

# ==================================================================== 엮기


class ContextEngine:
    """여섯 context 를 계산하고 바뀐 것만 돌려준다.

    recompute() 는 순수하다 — 계산과 타이머 예약만 하고 발행하지 않는다.
    그래서 몇 번 불려도 안전하고, 두 번째 호출은 빈 튜플을 돌려준다.

    의존 순서: presence → away → occupancy → activity → suppression
    뒤쪽이 앞쪽 결과를 인자로 받는다.

    경로가 둘이다.
      메시지 → 호출부가 recompute() 를 부르고 반환값을 받는다
      타이머 → _on_timer 가 recompute() 를 부르고 on_change 로 넘긴다
    후자가 없으면 시간 경과로만 일어나는 전이(PIR 타임아웃, AWAY, MULTI)가
    전부 조용히 묻힌다.
    """

    def __init__(
        self,
        clock: Clock,
        config: Config,
        world: WorldState,
        scheduler: Scheduler,
        on_change: Callable[[tuple[Context, ...]], None] | None = None,
        t0log: Any | None = None,
        models: Any | None = None,
    ) -> None:
        from .activity import ActivityContext, ActivityEvaluator   # 순환 import 회피
        from .fsm import MealFSM, DayFSM

        self._clock = clock
        self._config = config
        self._sched = scheduler
        self._on_change = on_change
        self._presence_eval = PresenceEvaluator(clock, config, world)
        self._away_eval = AwayEvaluator(clock, config, world)
        self._occupancy_eval = OccupancyEvaluator(clock, config, world)
        self._activity_eval = ActivityEvaluator(clock, config, world, models)
        self._suppression_eval = SuppressionEvaluator(clock, config)

        self.meal_fsm = MealFSM(clock, config, world, t0log)
        self.day_fsm = DayFSM(clock, config, world, t0log)
        self.meal_fsm.on_close = self.day_fsm.note_meal

        self.presence: PresenceContext | None = None
        self.away: AwayContext | None = None
        self.occupancy: OccupancyContext | None = None
        self.activity: ActivityContext | None = None
        self.suppression: SuppressionContext | None = None
        self.day: Any | None = None
        self._day_prev: Any | None = None
        self._asleep_area: str | None = None
        self._on_hydration: Callable[[int], None] | None = None


    def recompute(self) -> tuple[Context, ...]:
        """전부 다시 계산하고, 직전과 다른 것만 돌려준다."""
        timers: list[tuple[str, float]] = []
        changed: list[Context] = []

        presence, t = self._presence_eval.evaluate(self.presence)
        timers += t
        if not presence.same_as(self.presence):
            changed.append(presence)
        self.presence = presence

        away, t = self._away_eval.evaluate(presence, self.away)
        timers += t
        if not away.same_as(self.away):
            changed.append(away)
        self.away = away

        occupancy, t = self._occupancy_eval.evaluate(presence, away, self.occupancy)
        timers += t
        if not occupancy.same_as(self.occupancy):
            changed.append(occupancy)
        self.occupancy = occupancy

        # activity 는 presence 와 away 를 읽는다. 순서상 마지막.
        activity, t = self._activity_eval.evaluate(
            presence, away, self.activity, asleep_area=self._asleep_area
        )
        timers += t

        meal_areas = self._config.areas_with_role("MEAL")
        in_meal = any(presence.areas.get(a) for a in meal_areas)
        timers += self.meal_fsm.update(
            activity.state, in_meal_area=in_meal, since=activity.since
        )

        timers += self.day_fsm.tick()
        self.day = self.day_fsm.state
        if not self.day.same_as(self._day_prev):
            changed.append(self.day)
        self._day_prev = self.day

        if self.meal_fsm.t0 is not None and activity.state in ("COOKING", "EATING", "KITCHEN_MISC"):
            activity = replace(activity, t0=self.meal_fsm.t0)

        if not activity.same_as(self.activity):
            changed.append(activity)
        self.activity = activity

        # 억제는 away 와 occupancy 를 읽는다. 순서상 마지막.
        suppression, t = self._suppression_eval.evaluate(away, occupancy, self.suppression)
        timers += t
        if not suppression.same_as(self.suppression):
            changed.append(suppression)
        self.suppression = suppression

        for key, at in timers:
            self._arm(key, at)

        return tuple(changed)

    def all_contexts(self) -> tuple[Context, ...]:
        """5분 주기 생존 발행용 — 바뀌지 않아도 전부."""
        return tuple(
            c for c in (
                self.presence, self.away, self.occupancy,
                self.activity, self.suppression, self.day,
            )
            if c is not None
        )

    def note_event(self, msg: Any) -> None:
        """가전 이벤트를 오늘 기록에 반영한다.
           Engine 이 ingest 에서 호출한다.
        """
        from . import messages as m

        if isinstance(msg, m.DispensedEvent):
            self.day_fsm.note_hydration()
            # 양은 DayState 에 담지 않는다 — 시각 목록만 발행한다.
            # 일일 권장량 누적은 시나리오가 들고 있는다.
            if self._on_hydration is not None:
                self._on_hydration(msg.amount_ml or 0)

    def allows(self, scenario: str) -> bool:
        """이 시나리오의 알림을 지금 보내도 되는가.

        억제 중이 아니거나, 그 시나리오가 except 에 있으면 허용.
        """
        if self.suppression is None:
            return True
        return self.suppression.allows(scenario)

    def note_notification(self, scenario: str) -> None:
        """알림 층이 발송 직후 부른다."""
        self._suppression_eval.note_notification(scenario)

    def note_asleep(self, area: str | None) -> None:
        """SLEEP_ROUTINE 이 수면을 확정했다. None 이면 깼다는 뜻이다."""
        self._asleep_area = area


    @property
    def suppression_eval(self):
        """시나리오가 프로브를 걸 때 쓴다."""
        return self._suppression_eval

    # ------------------------------------------------------------ 타이머

    def _arm(self, key: str, at: float) -> None:
        """그 시각에 재계산을 예약한다.

        at 은 반드시 '근거 시각 + N' 이다. now + N 으로 잡으면 매번 미래로
        밀려 영원히 끝나지 않는다. 이미 지난 시각이면 걸지 않는다 —
        그래야 타이머가 자기를 무한히 다시 걸지 않는다.
        """
        if at <= self._clock.now():
            return
        self._sched.at(at, self._on_timer, key=f"ctx-{key}")

    def _on_timer(self) -> None:
        """타이머가 부르는 진입점.
           changed 가 비어도 콜백을 부른다 — 시나리오는 context 변화가 아니라 시간 경과로 성립하는 조건(기상 후 N분)을 본다.
        """
        changed = self.recompute()
        if self._on_change is not None:
            self._on_change(changed)