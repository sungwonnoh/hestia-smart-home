"""RPi4 학습 결과와 시스템 메시지, 판단 설명(Explanation)."""

from typing import Any, List, Optional

from pydantic import BaseModel, ConfigDict

from .common import ApiModel, MqttEnvelope, Number

MODEL_NAMES = ("kde", "hmm", "classifier")


# ------------------------------------------------------------------ MQTT


class ModelPayload(MqttEnvelope):
    """hestia/model/{name} — 모델별 내용은 그대로 보존한다."""

    model_config = ConfigDict(extra="allow")

    trained_at: Number


class RegistryEntry(BaseModel):
    model_config = ConfigDict(extra="ignore")

    virtual_id: str
    area: Optional[str] = None
    roles: List[str] = []
    type: Optional[str] = None
    device_type: Optional[str] = None
    source: Optional[str] = None
    channel: bool = False
    enabled: bool = True


class RegistryDevicesPayload(MqttEnvelope):
    """hestia/registry/devices — 장치 배치의 런타임 오버라이드."""

    devices: List[RegistryEntry]


class SystemProfilePayload(MqttEnvelope):
    profile: str


# ------------------------------------------------------------------ API


class ExplanationFactor(ApiModel):
    key: str
    value: Any = None
    label: str
    satisfied: bool = True


class Explanation(ApiModel):
    """Context Engine 의 confidence/factors 를 사람이 읽는 형태로 바꾼 것.

    API 는 새로 판단하지 않는다.
    """

    id: str
    context_name: str
    state: str
    confidence: Optional[float] = None
    factors: List[ExplanationFactor] = []
    action: Optional[str] = None
    created_at: Optional[str] = None
