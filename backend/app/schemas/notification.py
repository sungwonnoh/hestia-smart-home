"""알림 MQTT payload 와 Flutter Notification 모델."""

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict

from .common import ApiModel, MqttEnvelope, Number

ACK_TYPES = ("DELIVERED", "SEEN")
PRIORITIES = ("low", "normal", "high", "safety")
NOTIFICATION_TYPES = ("INFO", "REMINDER", "WARNING", "SAFETY")

# channels 의 RPi4 TTS 예약값. 장치가 아니므로 공간 변환에서 뺀다.
VOICE_CHANNEL = "voice"

# Context Engine scenario → Flutter 표시용 type. scenario 원본은 따로 보존한다.
SCENARIO_TYPES: Dict[str, str] = {
    "SAFETY": "SAFETY",
    "SENSOR_FAULT": "WARNING",
    "MEDICATION_PROMPT": "REMINDER",
    "WAKE_ROUTINE": "REMINDER",
    "SLEEP_ROUTINE": "INFO",
}


# ------------------------------------------------------------------ MQTT


class NotifyContent(BaseModel):
    """notify/push 의 payload — 채널에 보여줄 내용."""

    model_config = ConfigDict(extra="allow")

    title: Optional[str] = None
    text: Optional[str] = None


class NotifyPushPayload(MqttEnvelope):
    """hestia/notify/push — Context Engine 이 생성한 알림.

    필수는 notify_id 뿐이다. 나머지는 없어도 화면에 보일 수 있게 기본값을 둔다.
    type 은 MQTT 필드가 아니다. Backend 가 scenario 로 만든다.
    판단 근거(explanationId)와의 연결은 Context Engine decision_id ↔ notify_id 전달 방식이
    확정될 때까지 하지 않는다. 명세 밖 필드로 추정하지 않는다.
    """

    notify_id: str
    scenario: str = ""
    priority: str = "normal"
    # 발송 대상 virtual_id 목록 (공간 아님). "voice" 는 RPi4 TTS.
    channels: List[str] = []
    requires_ack: bool = False
    ack_deadline: Optional[Number] = None
    escalation_level: Optional[int] = None
    payload: NotifyContent = NotifyContent()


class NotifyAckPayload(MqttEnvelope):
    """hestia/notify/ack — 채널 노드(또는 이 API)가 보낸 응답."""

    notify_id: str
    ack_type: Literal["DELIVERED", "SEEN"]


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
