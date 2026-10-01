"""MQTT 공통 Envelope 와 API 공통 모델.

Python 3.9 에서도 돌도록 Optional/Union 을 쓴다 (RPi5 는 3.11 이상).
"""

from typing import Any, Dict, Optional, Union

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel

SCHEMA_VERSION = 1

# 정수 초가 원칙이지만 소수 초가 와도 버리지 않는다. int 는 int 로 유지된다.
Number = Union[int, float]

ENVELOPE_FIELDS = frozenset({"version", "sent_ts", "src_id", "seq"})


class MqttEnvelope(BaseModel):
    """MQTT 최종 명세의 공통 필드. 이름을 바꾸지 않는다 (timestamp, source 등 금지)."""

    model_config = ConfigDict(extra="ignore")

    version: int
    sent_ts: Number
    src_id: str
    seq: Optional[int] = None


def strip_envelope(payload: Dict[str, Any]) -> Dict[str, Any]:
    """API 응답용. 원본은 Backend 내부 cache 에 따로 보존한다."""
    return {k: v for k, v in payload.items() if k not in ENVELOPE_FIELDS}


class ApiModel(BaseModel):
    """Flutter 와 주고받는 모델. JSON 키는 camelCase (roomId, createdAt …)."""

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="ignore",
    )


class ErrorResponse(BaseModel):
    detail: str
