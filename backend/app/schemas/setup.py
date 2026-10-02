"""공간과 최초 설정."""

from typing import List

from .common import ApiModel
from .device import DeviceIn, DeviceOut
from .preference import UserPreferences


class Room(ApiModel):
    # config/homes/*.toml 의 area 체계 (living, kitchen …)
    id: str
    name: str
    roles: List[str] = []


class HomeSetupIn(ApiModel):
    """사용자 상태(HOME, SLEEPING 등)는 받지 않는다. Context Engine 이 판단한다."""

    rooms: List[Room]
    devices: List[DeviceIn]
    preferences: UserPreferences = UserPreferences()


class HomeSetupOut(ApiModel):
    rooms: List[Room]
    devices: List[DeviceOut]
    preferences: UserPreferences
