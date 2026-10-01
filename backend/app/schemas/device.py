"""가전 MQTT payload 와 Flutter Device 모델."""

from typing import Any, Dict, Optional

from pydantic import ConfigDict

from .common import ApiModel, MqttEnvelope


# ------------------------------------------------------------------ MQTT


class SensorStatePayload(MqttEnvelope):
    """hestia/sensor/{virtual_id}/state — 센서별 필드는 그대로 보존."""

    model_config = ConfigDict(extra="allow")

    type: str


class DeviceStatePayload(MqttEnvelope):
    """hestia/device/{virtual_id}/state — 공통 Envelope + 장치별 필드."""

    model_config = ConfigDict(extra="allow")

    device_type: str
    source: Optional[str] = None


class DeviceEventPayload(MqttEnvelope):
    """hestia/device/{virtual_id}/event"""

    model_config = ConfigDict(extra="allow")

    device_type: str
    event_type: str
    source: Optional[str] = None


# Flutter DeviceType.wireName ↔ MQTT device_type
FLUTTER_TO_MQTT_TYPE: Dict[str, str] = {
    "TV": "smart_tv",
    "LIGHT": "smart_light",
    "AIR_CONDITIONER": "air_conditioner",
    "AIR_PURIFIER": "air_purifier",
    "REFRIGERATOR": "smart_fridge",
    "WASHER": "washer",
    "WATER_PURIFIER": "water_purifier",
    "ROBOT_CLEANER": "robot_cleaner",
}
MQTT_TO_FLUTTER_TYPE: Dict[str, str] = {v: k for k, v in FLUTTER_TO_MQTT_TYPE.items()}


# ------------------------------------------------------------------ API


class DeviceIn(ApiModel):
    """설정에서 받는 가전. status/online 은 서버가 MQTT 로 채우므로 받지 않는다."""

    id: str
    name: str
    type: str
    room_id: str = ""
    # MQTT virtual_id (예: vd-01). 비우면 서버가 같은 종류·공간의 장치에 연결한다.
    virtual_id: Optional[str] = None


class DeviceUpdate(ApiModel):
    name: Optional[str] = None
    type: Optional[str] = None
    room_id: Optional[str] = None
    virtual_id: Optional[str] = None


class DeviceOut(ApiModel):
    id: str
    name: str
    type: str
    room_id: str
    # Flutter DeviceStatus: {"state": "ON", ...장치별 값}
    status: Dict[str, Any]
    online: bool
    virtual_id: Optional[str] = None
    last_seen_at: Optional[str] = None
