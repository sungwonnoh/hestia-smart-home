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
    NOTIFICATION_TYPES,
    PRIORITIES,
    NotificationOut,
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
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._history = history
        self._publisher = publisher
        self._src_id = src_id
        self._clock = clock

    # ------------------------------------------------------------ MQTT 입력

    def on_push(self, p: NotifyPushPayload, raw: dict[str, Any]) -> NotificationRecord | None:
        """새 알림이면 저장하고 돌려준다. 이미 받은 알림이면 None."""
        priority = p.priority.lower() if p.priority.lower() in PRIORITIES else "normal"
        ntype = (p.type or "").upper()
        if ntype not in NOTIFICATION_TYPES:
            ntype = "SAFETY" if priority == "safety" else "INFO"

        explanation_id = None
        if p.context:
            open_id = self._history.open_context_id(p.context)
            explanation_id = str(open_id) if open_id is not None else None

        record = NotificationRecord(
            id=p.notify_id,
            scenario=p.scenario,
            type=ntype,
            priority=priority,
            title=p.title or p.text or p.scenario or "HESTIA 알림",
            message=p.message or p.text or "",
            room_id=p.area,
            explanation_id=explanation_id,
            created_at=float(p.sent_ts),
        )
        return record if self._history.add_notification(record, raw) else None

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
