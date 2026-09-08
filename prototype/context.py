"""Context 판단 — 지금 사용자가 무엇을 하고 있는가.

World State만 보고 하나의 라벨을 정한다.
이벤트를 보지 않는다. 이벤트는 Policy가 본다.

프로토타입 단계라 규칙 몇 줄이지만, 나중에 이 자리가 FSM 집합으로 커진다.
"""

UNKNOWN = "UNKNOWN"
AWAY = "AWAY"
WATCHING_TV = "WATCHING_TV"
IN_KITCHEN = "IN_KITCHEN"
IN_BEDROOM = "IN_BEDROOM"
HOME_IDLE = "HOME_IDLE"


def judge(world) -> str:
    if not world.has_seen_any():
        return UNKNOWN
    if not world.any_presence():
        return AWAY
    if world.area_presence("LIVINGROOM") and world.device_state("TV") == "ON":
        return WATCHING_TV
    if world.area_presence("KITCHEN"):
        return IN_KITCHEN
    if world.area_presence("BEDROOM"):
        return IN_BEDROOM
    return HOME_IDLE
