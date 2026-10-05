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
from .policy import Decision, InterventionPolicy
from .timeutil import day_key

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
        policy: InterventionPolicy,
        notifier: Notifier,
        publish: Callable[[str, dict[str, Any], bool], None],
    ) -> None:
        self._clock = clock
        self._config = config
        self._policy = policy
        self._notifier = notifier
        self._publish = publish

        # 직전 판정. candidate 나 reason 이 바뀔 때만 발행한다 —
        # 판정은 context 가 바뀔 때마다 일어나므로 매번 발행하면 과함.
        self._last: dict[str, tuple[bool, str]] = {}
        self._seq = 0

    def tick(self, context: Any) -> None:
        """시나리오를 전부 훑는다. 조건에 안 맞으면 조용히 지나간다."""
        if context.away is None or context.suppression is None:
            return                              # 아직 판단이 서지 않았다
        self._wake_routine(context)

    # ------------------------------------------------------------ WAKE_ROUTINE

    def _wake_routine(self, context: Any) -> None:
        """기상 후 수분 섭취.

        시나리오 4. 기상하고 평소보다 오래 물을 마시지 않으면 권한다. hydration_lag 분포를 쓰는 것이 핵심이다 — "몇 시에" 가 아니라 "기상하고 몇 분 만에" 가 이 사람의 패턴이다.
        """
        wake = context.wake_fsm.state

        # ① 트리거 — 기상했고, 아직 안 마셨고, 집에 있어야 한다
        if wake.state != "AWAKE" or wake.wake_t0 is None:
            return
        if wake.hydration_done:
            return

        # 기상 직후 몇 분은 묻지 않는다. 일어나자마자 물을 마시는
        # 사람도 있고, 화장실부터 가는 사람도 있다.
        grace = float(
            self._config.value("scenarios", "wake", "hydration_grace_sec", default=900)
        )
        if self._clock.now() - wake.wake_t0 < grace:
            return

        # ② 판정 — 개인 분포에서 지금이 이상하게 늦은가
        decision = self._policy.evaluate_hydration(
            context.away,
            context.occupancy,
            context.suppression,
            wake_t0=wake.wake_t0,
            hydration_done=wake.hydration_done,
        )
        decision_id = self._emit_decision(decision)

        if not decision.candidate:
            return

        # ③ 발송
        self._notifier.send(
            scenario="WAKE_ROUTINE",
            title=str(self._notify_value("WAKE_ROUTINE", "title", "수분 섭취")),
            text=str(self._notify_value("WAKE_ROUTINE", "text", "물 한 잔 드세요")),
            priority=str(self._notify_value("WAKE_ROUTINE", "priority", "normal")),
            presence=context.presence,
            suppression=context.suppression,
            comply_kind="hydration",
            decision_id=decision_id,
            confidence=decision.confidence,
        )

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