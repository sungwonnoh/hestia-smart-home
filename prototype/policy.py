"""Policy — 이 이벤트에 대해 지금 무엇을 할 것인가.

context(현재 상황) + event(방금 일어난 일) -> 반응.
알림 채널 선택과 쿨다운이 여기 있다.
"""

from dataclasses import dataclass

import config
import context as ctx


@dataclass
class Action:
    policy: str      # 어떤 규칙이 걸렸는지 (로그용)
    topic: str       # 발행할 토픽 (PREFIX 제외한 뒷부분)
    payload: dict
    label: str       # 터미널에 찍을 사람이 읽는 형태


MESSAGES = {
    "WASHER_COMPLETE": ("washer_complete", "세탁이 완료되었습니다."),
}


class Policy:
    def __init__(self, clock):
        self.clock = clock
        self._last_sent: dict[str, float] = {}

    def decide(self, event, current_context: str) -> Action | None:
        spec = MESSAGES.get(event.name)
        if spec is None:
            return None
        kind, message = spec

        if self._in_cooldown(event.name):
            return Action("SUPPRESSED_COOLDOWN", "", {},
                          f"쿨다운으로 억제됨 ({config.NOTIFY_COOLDOWN_SEC}s)")

        payload = {"type": kind, "message": message}

        if current_context == ctx.WATCHING_TV:
            action = Action("TV_NOTIFICATION", "notify/tv", payload,
                            f"TV Banner: {message}")
        elif current_context == ctx.AWAY:
            action = Action("PUSH_NOTIFICATION", "notify/push", payload,
                            f"Push: {message}")
        else:
            action = Action("PUSH_NOTIFICATION", "notify/push", payload,
                            f"Push: {message}")

        self._mark_sent(event.name)
        return action

    def _in_cooldown(self, name: str) -> bool:
        last = self._last_sent.get(name)
        if last is None:
            return False
        elapsed = (self.clock.now() - last).total_seconds()
        return elapsed < config.NOTIFY_COOLDOWN_SEC

    def _mark_sent(self, name: str) -> None:
        self._last_sent[name] = self.clock.now()