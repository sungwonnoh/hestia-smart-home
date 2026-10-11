"""hestia/context/{name} payload.

Context 마다 구조가 달라 하나의 고정 schema 로 합치지 않는다.
"""

from typing import Any, Dict, List, Optional, Type

from pydantic import ConfigDict

from .common import MqttEnvelope, Number


class ActivityContext(MqttEnvelope):
    state: str
    since: Optional[Number] = None
    confidence: Optional[float] = None
    factors: Dict[str, Any] = {}


class PresenceContext(MqttEnvelope):
    area: Optional[str] = None
    since: Optional[Number] = None
    areas: Dict[str, bool] = {}
    confidence: Optional[float] = None
    factors: Dict[str, Any] = {}


class DayContext(MqttEnvelope):
    """오늘 하루 기록. 추론이 아니라 기록이라 state·confidence·factors 가 없다.

    Context Engine 이 기상 판정(wake)을 없애고 대신 보낸다. 목록은 각 활동 시각(epoch 초).
    """

    model_config = ConfigDict(extra="allow")

    date: str
    meals: List[Number] = []
    hydrations: List[Number] = []
    medications: List[Number] = []


class AwayContext(MqttEnvelope):
    """명세에 정의된 필드를 보존하도록 추가 필드를 허용한다."""

    model_config = ConfigDict(extra="allow")

    state: str
    confidence: Optional[float] = None
    factors: Dict[str, Any] = {}


class OccupancyContext(AwayContext):
    pass


class SuppressionContext(MqttEnvelope):
    """추론 결과가 아니라 억제 플래그 집합. confidence/factors 를 붙이지 않는다."""

    model_config = ConfigDict(extra="allow")


CONTEXT_SCHEMAS: Dict[str, Type[MqttEnvelope]] = {
    "activity": ActivityContext,
    "presence": PresenceContext,
    "day": DayContext,
    "away": AwayContext,
    "occupancy": OccupancyContext,
    "suppression": SuppressionContext,
}

CONTEXT_NAMES = tuple(CONTEXT_SCHEMAS)


def to_domain(model: MqttEnvelope) -> Dict[str, Any]:
    """API 용 Domain 표현: Envelope 를 빼고 값이 있는 필드만."""
    return model.model_dump(
        exclude={"version", "sent_ts", "src_id", "seq"},
        exclude_none=True,
    )


def history_state(name: str, model: MqttEnvelope) -> Optional[str]:
    """이력에 남길 대표 상태. presence 는 state 대신 area 를 쓴다."""
    if name == "presence":
        area = getattr(model, "area", None)
        return area if area else "NONE"
    return getattr(model, "state", None)
