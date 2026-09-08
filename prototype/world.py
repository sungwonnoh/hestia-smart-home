"""World State — 지금 이 집이 어떤 상태인가.

MQTT 메시지를 받을 때마다 누적/갱신되는 메모리.
상태 변화에서 파생 이벤트를 뽑아내는 것도 여기서 한다.
(세탁기 RUNNING -> COMPLETE 전이가 WASHER_COMPLETE 이벤트가 된다)
"""

from dataclasses import dataclass, field
from datetime import datetime

import config


@dataclass
class Event:
    name: str
    source: str
    ts: datetime
    data: dict = field(default_factory=dict)


class WorldState:
    def __init__(self, clock):
        self.clock = clock
        self.sensors: dict[str, dict] = {}
        self.devices: dict[str, dict] = {}
        self._role_index = {v["role"]: k for k, v in config.DEVICES.items()}

    # ---------- 갱신 ----------

    def apply_sensor(self, sensor_id: str, payload: dict) -> list[Event]:
        meta = config.SENSORS.get(sensor_id)
        if meta is None:
            return []
        value = payload.get("value")
        prev = self.sensors.get(sensor_id, {}).get("value")
        self.sensors[sensor_id] = {"value": value, "ts": self.clock.now()}
        if prev == value:
            return []
        area, typ = meta["area"], meta["type"]
        if typ == "presence":
            name = f"{area}_PRESENCE_{'ON' if value else 'OFF'}"
            return [Event(name, sensor_id, self.clock.now(), {"area": area})]
        return []

    def apply_device_state(self, device_id: str, payload: dict) -> list[Event]:
        meta = config.DEVICES.get(device_id)
        if meta is None:
            return []
        state = payload.get("state", payload.get("power"))
        prev = self.devices.get(device_id, {}).get("state")
        self.devices[device_id] = {"state": state, "ts": self.clock.now()}
        if prev == state or state is None:
            return []
        # 상태 전이에서 파생된 이벤트
        return [Event(f"{meta['role']}_{state}", device_id, self.clock.now(),
                      {"role": meta["role"], "prev": prev})]

    def apply_device_event(self, device_id: str, payload: dict) -> list[Event]:
        meta = config.DEVICES.get(device_id)
        if meta is None:
            return []
        raw = payload.get("event")
        if raw is None:
            return []
        return [Event(f"{meta['role']}_{raw}", device_id, self.clock.now(),
                      {"role": meta["role"]})]

    # ---------- 조회 (엔진은 이 함수들로만 월드를 본다) ----------

    def area_presence(self, area: str) -> bool:
        for sid, meta in config.SENSORS.items():
            if meta["area"] == area and meta["type"] == "presence":
                if self.sensors.get(sid, {}).get("value"):
                    return True
        return False

    def any_presence(self) -> bool:
        return any(
            self.sensors.get(sid, {}).get("value")
            for sid, meta in config.SENSORS.items()
            if meta["type"] == "presence"
        )

    def occupied_area(self) -> str | None:
        for sid, meta in config.SENSORS.items():
            if meta["type"] == "presence" and self.sensors.get(sid, {}).get("value"):
                return meta["area"]
        return None

    def device_state(self, role: str) -> str | None:
        did = self._role_index.get(role)
        return self.devices.get(did, {}).get("state") if did else None

    def has_seen_any(self) -> bool:
        return bool(self.sensors or self.devices)

    def dump(self) -> list[str]:
        lines = []
        loc = self.occupied_area()
        lines.append(f"user.location = {loc.lower() if loc else '-'}")
        for did, st in self.devices.items():
            lines.append(f"{did}.state = {st['state']}")
        return lines