"""알림 MQTT payload 와 Flutter Notification 모델."""

from typing import Any, Dict, Literal, Optional

from pydantic import ConfigDict

from .common import ApiModel, MqttEnvelope, Number

ACK_TYPES = ("DELIVERED", "SEEN")
PRIORITIES = ("low", "normal", "high", "safety")
NOTIFICATION_TYPES = ("INFO", "REMINDER", "WARNING", "SAFETY")


# ------------------------------------------------------------------ MQTT


class NotifyPushPayload(MqttEnvelope):
    """hestia/notify/push — Context Engine 이 생성한 알림.

    필수는 notify_id 뿐이다. 나머지는 없어도 화면에 보일 수 있게 기본값을 둔다.
    """

    notify_id: str
    scenario: str = ""
    priority: str = "normal"
    type: Optional[str] = None
    title: Optional[str] = None
    message: Optional[str] = None
    text: Optional[str] = None
    area: Optional[str] = None
    # 이 알림을 만든 context 이름 (예: activity). 판단 근거와 연결할 때 쓴다.
    context: Optional[str] = None


class NotifyCancelPayload(MqttEnvelope):
    notify_id: str
    reason: Optional[str] = None


class InterventionOutcomePayload(MqttEnvelope):
    """hestia/intervention/outcome — 정의된 필드 외 값은 metadata 로 보존."""

    model_config = ConfigDict(extra="allow")

    notify_id: Optional[str] = None
    scenario: Optional[str] = None
    outcome: str
    occurred_at: Optional[Number] = None


# ------------------------------------------------------------------ API


class NotificationOut(ApiModel):
    id: str
    scenario: str
    type: str
    priority: str
    title: str
    message: str
    created_at: str
    room_id: Optional[str] = None
    explanation_id: Optional[str] = None
    delivered: bool
    seen: bool


class AckRequest(ApiModel):
    ack_type: Literal["DELIVERED", "SEEN"] = "SEEN"


class AckResponse(ApiModel):
    success: bool
    # MQTT(hestia/notify/ack)로 Context Engine 에 전달했는지
    forwarded: bool


def payload_metadata(model: InterventionOutcomePayload) -> Dict[str, Any]:
    known = {"version", "sent_ts", "src_id", "seq", "notify_id", "scenario",
             "outcome", "occurred_at"}
    return {k: v for k, v in model.model_dump().items() if k not in known}
