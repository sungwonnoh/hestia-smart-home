"""가전 제어.
    send → device/{virtual_id}/cmd 발행 → 노드가 실행 → state 발행
                                                        ↓
                                              reverted() 가 그것을 읽는다
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable

from .clock import Clock
from .config import Config
from .messages import ACTIONS
from .timeutil import day_key
from .world import WorldState

log = logging.getLogger(__name__)

SRC_ID = "rpi5"
SCHEMA_VERSION = 1

# device_type 별로 set 이 받는 params 키.
SET_PARAMS = {
    "smart_tv": frozenset({"power", "volume"}),
    "smart_light": frozenset({"power", "brightness", "color_temp"}),
    "air_conditioner": frozenset({"power", "mode", "temp_set"}),
    "air_purifier": frozenset({"power", "mode"}),
    "water_purifier": frozenset({"power"}),
    "robot_cleaner": frozenset({"schedule"}),
}

# params 가 빈 객체인 action (명세). robot_cleaner 전용.
EMPTY_PARAM_ACTIONS = frozenset({"start", "stop", "dock"})

# 모든 기기가 받는 선택 키. 점진 변화 소요 시간(ms)이다.
TRANSITION_KEY = "transition_ms"


# ==================================================================== 보낸 명령


@dataclass(frozen=True, slots=True)
class SentCommand:
    """마지막으로 내린 명령. 되돌림 판정의 기준이다."""

    cmd_id: str
    target: str                              # virtual_id
    device_type: str
    action: str
    params: dict[str, Any] = field(default_factory=dict)        # 명령으로 보낸 목표 상태
    sent_at: float = 0.0
    reason: str = ""                         # 어느 시나리오의 판단인지
    priority: str = "normal"


# ==================================================================== Controller


class Controller:
    """가전에 명령을 내리고 마지막 명령을 기억한다."""

    def __init__(
        self,
        clock: Clock,
        config: Config,
        world: WorldState,
        publish: Callable[[str, dict[str, Any], bool], None],
    ) -> None:
        self._clock = clock
        self._config = config
        self._world = world
        self._publish = publish

        self._last: dict[str, SentCommand] = {}    # target → 마지막 명령
        self._seq = 0

    # ------------------------------------------------------------ 발송

    def send(
        self,
        *,
        target: str,
        action: str = "set",
        params: dict[str, Any] | None = None,
        reason: str = "",
        priority: str = "normal",
        transition_ms: int | None = None,
    ) -> str | None:
        """명령 한 건. 못 보내면 None 과 함께 이유를 로그에 남긴다."""
        d = self._config.device(target)
        if d is None or not d.enabled:
            log.warning("제어 대상이 설정에 없음: %s", target)
            return None

        if action not in ACTIONS:
            log.warning("알 수 없는 action: %s", action)
            return None

        body = dict(params or {})
        if transition_ms is not None:
            body[TRANSITION_KEY] = int(transition_ms)

        if not self._valid(d.device_type, action, body):
            return None

        cmd_id = self._next_id()
        self._publish(
            f"hestia/device/{target}/cmd",
            {
                "version": SCHEMA_VERSION,
                "sent_ts": int(self._clock.now()),
                "src_id": SRC_ID,
                "cmd_id": cmd_id,
                "device_type": d.device_type,
                "action": action,
                "params": body,
                "reason": reason,
                "priority": priority,
            },
            # retained 금지.
            False,
        )

        self._last[target] = SentCommand(
            cmd_id=cmd_id,
            target=target,
            device_type=d.device_type,
            action=action,
            params=body,
            sent_at=self._clock.now(),
            reason=reason,
            priority=priority,
        )
        log.info("제어 %s %s %s %s (%s)", cmd_id, target, action, body, reason)
        return cmd_id

    def set(
        self,
        target: str,
        *,
        reason: str = "",
        priority: str = "normal",
        transition_ms: int | None = None,
        **params: Any,
    ) -> str | None:
        """set 의 축약.
            controller.set("vd-02", brightness=40, transition_ms=30000,
                           reason="SLEEP_ROUTINE")
        """
        return self.send(
            target=target, action="set", params=params,
            reason=reason, priority=priority, transition_ms=transition_ms,
        )

    # ------------------------------------------------------------ 조회

    def last_for(self, target: str) -> SentCommand | None:
        """그 기기에 마지막으로 내린 명령."""
        return self._last.get(target)

    def reverted(self, target: str) -> bool:
        """사용자가 명령을 되돌렸는가.
        """
        cmd = self._last.get(target)
        if cmd is None:
            return False

        st = self._world.device(target)
        if st is None:
            return False
        if st.updated_at <= cmd.sent_at:
            return False                    # 아직 실행 전의 보고다

        if cmd.action in EMPTY_PARAM_ACTIONS:
            return self._action_reverted(target, cmd)

        for key, want in cmd.params.items():
            if key == TRANSITION_KEY:
                continue
            if self._key_reverted(st, key, want):
                return True
        return False

    def devices_of_type(
        self, device_type: str, area: str | None = None
    ) -> tuple[str, ...]:
        """그 종류의 제어 대상 찾기."""
        ids = (
            self._config.devices_in(area)
            if area
            else tuple(d.id for d in self._config.all_devices())
        )
        return tuple(
            vid
            for vid in ids
            if (d := self._config.device(vid))
            and d.device_type == device_type
            and d.enabled
        )

    # ------------------------------------------------------------ 내부

    @staticmethod
    def _key_reverted(st: Any, key: str, want: Any) -> bool:
        """그 필드가 명령값에서 벗어났는가.
        """
        now = st.get(key)
        if now is None:
            return False
        if isinstance(want, (int, float)) and isinstance(now, (int, float)):
            return float(now) != float(want)
        return now != want

    def _action_reverted(self, target: str, cmd: SentCommand) -> bool:
        """start / stop / dock 이 뒤집혔는가.
        """
        st = self._world.device(target)
        if st is None:
            return False
        status = st.get("status")
        if status is None:
            return False

        match cmd.action:
            case "start":
                return status != "CLEANING"
            case "stop":
                return status == "CLEANING"
            case "dock":
                return status not in ("DOCKED", "CHARGING")
            case _:
                return False

    def _valid(self, device_type: str, action: str, params: dict[str, Any]) -> bool:
        if action in EMPTY_PARAM_ACTIONS:
            extra = set(params) - {TRANSITION_KEY}
            if extra:
                log.warning("%s 는 params 를 받지 않음: %s", action, sorted(extra))
                return False
            return True

        allowed = SET_PARAMS.get(device_type)
        if allowed is None:
            log.warning("set 을 받지 않는 기기 종류: %s", device_type)
            return False

        unknown = set(params) - allowed - {TRANSITION_KEY}
        if unknown:
            log.warning("%s 가 받지 않는 params: %s", device_type, sorted(unknown))
            return False
        if not set(params) - {TRANSITION_KEY}:
            log.warning("set 인데 params 가 비었음")
            return False
        return True

    def _next_id(self) -> str:
        self._seq += 1
        return f"c-{day_key(self._clock.now()).replace('-', '')}-{self._seq:03d}"