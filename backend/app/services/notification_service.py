"""알림 저장과 ACK 전달.

Flutter 가 알림을 화면에 띄웠다고 SEEN 으로 처리하지 않는다.
SEEN 은 사용자가 [확인]을 눌러 POST .../ack 를 보냈을 때만 기록·전달한다.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, Protocol

from ..repositories.history_repository import HistoryRepository, NotificationRecord
from ..schemas.common import SCHEMA_VERSION
from ..schemas.notification import (
    PRIORITIES,
    SCENARIO_TYPES,
    VOICE_CHANNEL,
    NotificationOut,
    NotifyAckPayload,
    NotifyPushPayload,
)
from .context_service import iso

log = logging.getLogger(__name__)

ACK_TOPIC = "hestia/notify/ack"


class Publisher(Protocol):
    def publish(self, topic: str, payload: dict[str, Any], *, qos: int = 1,
                retain: bool = False) -> bool: ...


class NotificationNotFound(Exception):
    pass


def scenario_type(scenario: str, priority: str) -> str:
    """scenario → 표시용 type. 모르는 scenario 는 priority 로 안전하게 정한다."""
    mapped = SCENARIO_TYPES.get(scenario.upper())
    if mapped:
        return mapped
    return "SAFETY" if priority == "safety" else "INFO"


def channel_room(channels: list[str], area_of: Callable[[str], str | None]) -> str | None:
    """발송 채널(virtual_id) 중 registry 에서 공간을 찾은 첫 번째. 없으면 None."""
    for vid in channels:
        if vid == VOICE_CHANNEL:
            continue
        area = area_of(vid)
        if area:
            return area
    return None


def to_out(n: NotificationRecord) -> NotificationOut:
    return NotificationOut(
        id=n.id,
        scenario=n.scenario,
        type=n.type,
        priority=n.priority,
        title=n.title,
        message=n.message,
        created_at=iso(n.created_at) or "",
        room_id=n.room_id,
        explanation_id=n.explanation_id,
        delivered=n.delivered_at is not None,
        seen=n.seen_at is not None,
    )


class NotificationService:
    def __init__(
        self,
        history: HistoryRepository,
        publisher: Publisher,
        src_id: str,
        area_of: Callable[[str], str | None] = lambda vid: None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._history = history
        self._publisher = publisher
        self._src_id = src_id
        self._area_of = area_of
        self._clock = clock

    # ------------------------------------------------------------ MQTT 입력

    def on_push(self, p: NotifyPushPayload, raw: dict[str, Any]) -> NotificationRecord | None:
        """새 알림이면 저장하고 돌려준다. 이미 받은 알림이면 None."""
        priority = p.priority.lower() if p.priority.lower() in PRIORITIES else "normal"
        room_id = channel_room(p.channels, self._area_of)
        devices = [c for c in p.channels if c != VOICE_CHANNEL]
        if devices and room_id is None:
            # 앱 설정으로 대신 찾지 않는다. registry 가 정본이다.
            log.warning("registry 에서 알림 공간을 찾지 못함: %s channels=%s",
                        p.notify_id, devices)

        record = NotificationRecord(
            id=p.notify_id,
            scenario=p.scenario,
            type=scenario_type(p.scenario, priority),
            priority=priority,
            title=p.payload.title or p.payload.text or p.scenario or "HESTIA 알림",
            message=p.payload.text or "",
            room_id=room_id,
            # decision_id ↔ notify_id 연결 방식 확인 전까지 비워 둔다.
            explanation_id=None,
            created_at=float(p.sent_ts),
        )
        return record if self._history.add_notification(record, raw) else None

    def on_ack(self, ack: NotifyAckPayload, recv_ts: float) -> bool:
        """채널 노드의 ACK 를 반영한다. 모르는 알림이면 False.

        이 API 가 발행한 ACK 도 다시 들어오지만 mark_ack 는 처음 시각을 유지하므로 무해하다.
        """
        if self._history.get_notification(ack.notify_id) is None:
            log.info("모르는 알림의 ACK: %s %s", ack.notify_id, ack.ack_type)
            return False
        self._history.mark_ack(ack.notify_id, ack.ack_type, recv_ts)
        return True

    def on_cancel(self, notify_id: str) -> bool:
        return self._history.cancel_notification(notify_id)

    # ------------------------------------------------------------ API

    def list(self, limit: int = 100) -> list[NotificationOut]:
        return [to_out(n) for n in self._history.list_notifications(limit)]

    def acknowledge(self, notify_id: str, ack_type: str) -> bool:
        """DB 에 기록하고 MQTT 로 Context Engine 에 전달한다.

        브로커가 끊겨 있어도 기록은 남긴다. 전달 여부를 돌려준다.
        """
        if self._history.get_notification(notify_id) is None:
            raise NotificationNotFound(notify_id)
        now = self._clock()
        self._history.mark_ack(notify_id, ack_type, now)
        forwarded = self._publisher.publish(
            ACK_TOPIC,
            {
                "version": SCHEMA_VERSION,
                "sent_ts": int(now),
                "src_id": self._src_id,
                "notify_id": notify_id,
                "ack_type": ack_type,
            },
            qos=1,
            retain=False,
        )
        if not forwarded:
            log.warning("ACK 를 MQTT 로 전달하지 못함: %s %s", notify_id, ack_type)
        return forwarded
