"""가전: MQTT 장치 상태 ↔ Flutter Device 변환과 virtual_id 연결."""

from __future__ import annotations

import logging
import time
from typing import Any, Callable

from ..repositories.device_repository import DeviceRecord, DeviceRepository
from ..schemas.common import strip_envelope
from ..schemas.device import FLUTTER_TO_MQTT_TYPE, DeviceOut
from .context_service import StateCache, iso

log = logging.getLogger(__name__)

RUNNING_CYCLES = {"WASH", "RINSE", "SPIN"}
CLEANER_STATE = {
    "CLEANING": "RUNNING",
    "RETURNING": "RUNNING",
    "DOCKED": "STANDBY",
    "PAUSED": "STANDBY",
    "ERROR": "ERROR",
}


def to_status(device_type: str | None, payload: dict[str, Any]) -> dict[str, Any]:
    """장치별 payload → Flutter DeviceStatus {"state": ..., 나머지 필드}.

    표시용 대표 상태만 고른다. 장치별 원래 필드는 그대로 함께 보낸다.
    """
    attrs = {k: v for k, v in strip_envelope(payload).items()
             if k not in {"device_type", "source"}}
    power = attrs.get("power")

    if device_type == "air_conditioner":
        state = power or "UNKNOWN"
        if attrs.get("temp_set") is not None:
            attrs["targetTemp"] = attrs["temp_set"]
    elif device_type == "air_purifier":
        mode = attrs.get("mode")
        state = mode if power == "ON" and mode else (power or "UNKNOWN")
    elif device_type == "smart_fridge":
        state = "ON"  # 항상 켜져 있음. 생존은 보고 주기로 판단한다.
    elif device_type == "washer":
        cycle = attrs.get("cycle")
        if cycle == "DONE":
            state = "DONE"
        elif cycle in RUNNING_CYCLES:
            state = "RUNNING"
        else:
            state = power or "UNKNOWN"
    elif device_type == "robot_cleaner":
        state = CLEANER_STATE.get(attrs.get("status", ""), "UNKNOWN")
    else:
        state = power or "UNKNOWN"

    attrs["state"] = state
    return attrs


class DeviceService:
    def __init__(
        self,
        repo: DeviceRepository,
        cache: StateCache,
        stale_sec: float,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._repo = repo
        self._cache = cache
        self._stale_sec = stale_sec
        self._clock = clock

    # ------------------------------------------------------------ MQTT 입력

    def on_state(self, vid: str, payload: dict[str, Any], recv_ts: float) -> None:
        self._cache.set_device(vid, payload, recv_ts)
        if not self._repo.save_state(vid, payload, recv_ts):
            # 아직 연결된 가전이 없다. 같은 종류의 가전이 있으면 연결한다.
            # 연결되면 bind_unassigned 가 cache 의 마지막 상태를 저장한다.
            self.bind_unassigned()

    def bind_unassigned(self) -> int:
        """virtual_id 가 없는 가전을 같은 종류의 MQTT 장치에 연결한다.

        같은 공간(area)의 장치를 우선한다. 판단이 아니라 설정 보조이며,
        PUT /devices/{id} 로 virtualId 를 직접 지정할 수 있다.
        """
        devices = self._repo.list_devices()
        taken = {d.virtual_id for d in devices if d.virtual_id}
        known = self._cache.known_devices()
        bound = 0
        for d in devices:
            if d.virtual_id:
                continue
            mqtt_type = FLUTTER_TO_MQTT_TYPE.get(d.type)
            candidates = sorted(
                vid for vid, info in known.items()
                if vid not in taken and info.get("device_type") == mqtt_type
            )
            if not candidates:
                continue
            same_area = [v for v in candidates if known[v].get("area") == d.room_id]
            vid = (same_area or candidates)[0]
            self._repo.bind(d.id, vid)
            taken.add(vid)
            bound += 1
            log.info("가전 연결: %s → %s", d.id, vid)
            last = self._cache.device(vid)
            if last is not None:
                self._repo.save_state(vid, *last)
        return bound

    # ------------------------------------------------------------ API 출력

    def to_out(self, d: DeviceRecord) -> DeviceOut:
        now = self._clock()
        online = d.last_seen_at is not None and now - d.last_seen_at <= self._stale_sec
        mqtt_type = FLUTTER_TO_MQTT_TYPE.get(d.type)
        status = to_status(mqtt_type, d.last_state) if d.last_state else {"state": "UNKNOWN"}
        return DeviceOut(
            id=d.id,
            name=d.name,
            type=d.type,
            room_id=d.room_id,
            status=status,
            online=online,
            virtual_id=d.virtual_id,
            last_seen_at=iso(d.last_seen_at),
        )

    def list(self) -> list[DeviceOut]:
        return [self.to_out(d) for d in self._repo.list_devices()]
