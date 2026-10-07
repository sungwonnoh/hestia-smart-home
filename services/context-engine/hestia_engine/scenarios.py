"""시나리오 — 언제 무엇을 할지.
    policy 는 "지금이 평소와 다른가" 를 묻고, notify 는 "어떻게 전달하나" 를 안다. 여기는 그 사이 — "그래서 뭘 해야 하나".

각 시나리오는 셋을 한다.

    ① 트리거 조건 확인      context 와 FSM 플래그를 읽는다
    ② policy 로 판정        개인 분포에서 지금이 이상한가
    ③ 후보면 알림 발송      notifier 에 넘긴다

②의 결과는 후보가 아니어도 발행한다. 왜 개입하지 않았는지가 왜 개입했는지만큼 중요(명세의 intervention/decision).

지금은 WAKE_ROUTINE 하나뿐. 나머지 셋(MEDICATION_PROMPT, SLEEP_ROUTINE, SAFETY)은 구조가 잡힌 뒤에 추가.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from .clock import Clock
from .config import Config
from .notify import Notifier
from .world import PresenceState, WorldState
from .policy import Decision, InterventionPolicy
from .timeutil import day_key
from .control import Controller

log = logging.getLogger(__name__)

SRC_ID = "rpi5"
SCHEMA_VERSION = 1


class ScenarioRunner:
    """매 recompute 후에 불림.
       Engine 이 소유하고, 타이머 경로에서도 같은 함수를 탄다 — 시간 경과로만 성립하는 조건(기상 후 N분 물을 안 마심)이 묻히지 않게.
    """

    def __init__(
        self,
        clock: Clock,
        config: Config,
        world: WorldState,
        policy: InterventionPolicy,
        notifier: Notifier,
        controller: Controller,
        publish: Callable[[str, dict[str, Any], bool], None],
    ) -> None:
        self._clock = clock
        self._config = config
        self._world = world
        self._policy = policy
        self._notifier = notifier
        self._controller = controller
        self._publish = publish

        # 직전 판정. candidate 나 reason 이 바뀔 때만 발행한다 —
        # 판정은 context 가 바뀔 때마다 일어나므로 매번 발행하면 과함.
        self._last: dict[str, tuple[bool, str]] = {}
        self._seq = 0

    def tick(self, context: Any) -> None:
        """시나리오를 전부 훑는다. 조건에 안 맞으면 조용히 지나간다."""
        timers: list[tuple[str, float]] = []
        if context.away is None or context.suppression is None:
            return timers
        timers += self._safety(context)
        timers += self._wake_routine(context)
        return timers


    # ------------------------------------------------------------ SAFETY

    def _safety(self, context: Any) -> list[tuple[str, float]]:
        """낙상 의심 — 사람이 있는데 오래 움직이지 않는다.
           개인 분포를 쓰지 않는다. 지금은 화장실만 본다.
        """
        timers: list[tuple[str, float]] = []
        now = self._clock.now()

        areas = tuple(
            self._config.value("thresholds", "safety", "still_areas", default=["bathroom"])
        )
        limit = float(
            self._config.value("thresholds", "safety", "still_sec", default=900)
        )

        for area in areas:
            if not self._area_occupied(area):
                continue

            since = self._still_since(area)
            if since is None:
                # 움직이는 중. 멈추는 순간 센서가 메시지를 보내
                # 다시 걸리므로 예약하지 않는다.
                self._emit_decision(Decision(
                    kind=f"safety:{area}",
                    candidate=False,
                    reason="MOVING",
                    factors={"area": area, "still_sec": 0, "limit_sec": limit},
                ))
                continue
            
            still = now - since
            over = still >= limit

            decision = Decision(
                kind=f"safety:{area}",
                candidate=over,
                reason="STILL_TOO_LONG" if over else "MOVING",
                confidence=1.0 if over else 0.0,
                factors={"area": area, "still_sec": round(still), "limit_sec": limit},
            )
            decision_id = self._emit_decision(decision)

            if not over:
                timers.append((f"safety-{area}", since + limit))
                continue

            self._notifier.send(
                scenario="SAFETY",
                title=str(self._notify_value("SAFETY", "title", "확인이 필요합니다")),
                text=str(self._notify_value("SAFETY", "text", "괜찮으신가요?")),
                priority="health",
                presence=context.presence,
                suppression=context.suppression,     # SAFETY 는 억제를 뚫는다
                comply_check="movement",
                comply_area=area,
                decision_id=decision_id,
                confidence=decision.confidence,
            )
            return timers          # 한 번에 하나만

        return timers
    
    
    # ------------------------------------------------------------ WAKE_ROUTINE

    def _wake_routine(self, context: Any) -> list[tuple[str, float]]:
        """기상 후 수분 섭취.

        시나리오 4. 기상하고 평소보다 오래 물을 마시지 않으면 권한다. hydration_lag 분포를 쓰는 것이 핵심이다 — "몇 시에" 가 아니라 "기상하고 몇 분 만에" 가 이 사람의 패턴이다.
        """
        timers: list[tuple[str, float]] = []
        wake = context.wake_fsm.state

        # ① 트리거 — 기상했고, 아직 안 마셨고, 집에 있어야 한다
        if wake.state != "AWAKE" or wake.wake_t0 is None:
            return timers
        if wake.hydration_done:
            return timers

        # 기상 직후 몇 분은 묻지 않는다. 일어나자마자 물을 마시는
        # 사람도 있고, 화장실부터 가는 사람도 있다.
        now = self._clock.now()
        grace = float(
            self._config.value("scenarios", "wake", "hydration_grace_sec", default=900)
        )
        if now - wake.wake_t0 < grace:
            return [("wake-hydration", wake.wake_t0 + grace)]

        # ② 판정 — 개인 분포에서 지금이 이상하게 늦은가
        decision = self._policy.evaluate_hydration(
            context.away,
            context.occupancy,
            context.suppression,
            wake_t0=wake.wake_t0,
            hydration_done=wake.hydration_done,
        )
        decision_id = self._emit_decision(decision)

        if not decision.candidate:      # 아직 평소 범위
            recheck = float(
                self._config.value("scenarios", "wake", "recheck_sec", default=300)
            )
            return [("wake-hydration", now + recheck)]

        # ③ 발송
        self._notifier.send(
            scenario="WAKE_ROUTINE",
            title=str(self._notify_value("WAKE_ROUTINE", "title", "수분 섭취")),
            text=str(self._notify_value("WAKE_ROUTINE", "text", "물 한 잔 드세요")),
            priority=str(self._notify_value("WAKE_ROUTINE", "priority", "normal")),
            presence=context.presence,
            suppression=context.suppression,
            comply_kind="hydration",
            comply_check="wake",
            decision_id=decision_id,
            confidence=decision.confidence,
        )
        return timers

    # ------------------------------------------------------------ 판정 발행

    def _emit_decision(self, d: Decision) -> str | None:
        """candidate 또는 reason 이 직전과 달라질 때만 발행한다."""
        key = (d.candidate, d.reason)
        if self._last.get(d.kind) == key:
            return None

        self._last[d.kind] = key
        decision_id = self._next_id()
        self._publish("hestia/intervention/decision", {
            "version": SCHEMA_VERSION,
            "sent_ts": int(self._clock.now()),
            "src_id": SRC_ID,
            "decision_id": decision_id,
            **d.payload(),
        }, False)
        log.debug("판정 %s %s candidate=%s", decision_id, d.kind, d.candidate)
        return decision_id

    # ------------------------------------------------------------ 내부

    def _notify_value(self, scenario: str, key: str, default: Any) -> Any:
        return self._config.notify_policy(scenario).get(key, default)

    def _next_id(self) -> str:
        self._seq += 1
        return f"d-{day_key(self._clock.now()).replace('-', '')}-{self._seq:03d}"

    def _area_occupied(self, area: str) -> bool:
        """그 구역에 사람이 있는가."""
        return any(
            isinstance(s, PresenceState) and s.present
            for s in self._world.sensors_of(area)
        )

    def _area_occupied(self, area: str) -> bool:
        """그 구역에 사람이 있는가."""
        return any(
            isinstance(s, PresenceState) and s.present
            for s in self._world.sensors_of(area)
        )

    def _still_since(self, area: str) -> float | None:
        """그 구역에서 정지가 시작된 시각. 움직이는 중이면 None."""
        spans = [
            s.still_since
            for s in self._world.sensors_of(area)
            if isinstance(s, PresenceState) and s.present and s.still_since is not None
        ]
        return min(spans) if spans else None

    def _notify_value(self, scenario: str, key: str, default: Any) -> Any:
        return self._config.notify_policy(scenario).get(key, default)