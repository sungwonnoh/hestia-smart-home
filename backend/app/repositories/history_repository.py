"""알림·판단 이력·개입 결과."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from .database import Database


@dataclass
class NotificationRecord:
    id: str
    scenario: str
    type: str
    priority: str
    title: str
    message: str
    room_id: str | None
    explanation_id: str | None
    created_at: float
    delivered_at: float | None = None
    seen_at: float | None = None
    status: str = "ACTIVE"


@dataclass
class ContextRecord:
    id: int
    context_name: str
    state: str | None
    confidence: float | None
    factors: dict[str, Any]
    started_at: float
    updated_at: float
    ended_at: float | None


def _notification(row) -> NotificationRecord:
    return NotificationRecord(
        id=row["id"],
        scenario=row["scenario"],
        type=row["type"],
        priority=row["priority"],
        title=row["title"],
        message=row["message"],
        room_id=row["room_id"],
        explanation_id=row["explanation_id"],
        created_at=row["created_at"],
        delivered_at=row["delivered_at"],
        seen_at=row["seen_at"],
        status=row["status"],
    )


def _context(row) -> ContextRecord:
    return ContextRecord(
        id=row["id"],
        context_name=row["context_name"],
        state=row["state"],
        confidence=row["confidence"],
        factors=json.loads(row["factors_json"]) if row["factors_json"] else {},
        started_at=row["started_at"],
        updated_at=row["updated_at"],
        ended_at=row["ended_at"],
    )


class HistoryRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    # ------------------------------------------------------------ notifications

    def add_notification(self, n: NotificationRecord, payload: dict[str, Any]) -> bool:
        """같은 notify_id 가 다시 오면 (retained/재전송) 무시하고 False."""
        cur = self._db.execute(
            "INSERT OR IGNORE INTO notifications (id, scenario, type, priority, title, "
            "message, room_id, explanation_id, created_at, status, payload_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'ACTIVE', ?)",
            (
                n.id, n.scenario, n.type, n.priority, n.title, n.message,
                n.room_id, n.explanation_id, n.created_at,
                json.dumps(payload, ensure_ascii=False),
            ),
        )
        return cur.rowcount > 0

    def list_notifications(self, limit: int = 100) -> list[NotificationRecord]:
        rows = self._db.query(
            "SELECT * FROM notifications WHERE status != 'CANCELLED' "
            "ORDER BY created_at DESC LIMIT ?",
            (limit,),
        )
        return [_notification(r) for r in rows]

    def get_notification(self, notification_id: str) -> NotificationRecord | None:
        row = self._db.query_one("SELECT * FROM notifications WHERE id = ?", (notification_id,))
        return _notification(row) if row else None

    def mark_ack(self, notification_id: str, ack_type: str, ts: float) -> None:
        """SEEN 은 DELIVERED 를 포함한다. 처음 기록된 시각을 유지한다."""
        if ack_type == "SEEN":
            self._db.execute(
                "UPDATE notifications SET seen_at = COALESCE(seen_at, ?), "
                "delivered_at = COALESCE(delivered_at, ?) WHERE id = ?",
                (ts, ts, notification_id),
            )
        else:
            self._db.execute(
                "UPDATE notifications SET delivered_at = COALESCE(delivered_at, ?) WHERE id = ?",
                (ts, notification_id),
            )

    def cancel_notification(self, notification_id: str) -> bool:
        cur = self._db.execute(
            "UPDATE notifications SET status = 'CANCELLED' WHERE id = ?",
            (notification_id,),
        )
        return cur.rowcount > 0

    def latest_notification_for(self, explanation_id: str) -> NotificationRecord | None:
        row = self._db.query_one(
            "SELECT * FROM notifications WHERE explanation_id = ? "
            "ORDER BY created_at DESC LIMIT 1",
            (explanation_id,),
        )
        return _notification(row) if row else None

    # ------------------------------------------------------------ context history

    def record_context(
        self,
        name: str,
        state: str | None,
        confidence: float | None,
        factors: dict[str, Any],
        payload: dict[str, Any],
        ts: float,
    ) -> int:
        """상태가 같으면 열린 구간을 갱신하고, 바뀌면 닫고 새 구간을 연다."""
        factors_json = json.dumps(factors, ensure_ascii=False)
        payload_json = json.dumps(payload, ensure_ascii=False)
        with self._db.transaction() as conn:
            open_row = conn.execute(
                "SELECT id, state FROM context_history "
                "WHERE context_name = ? AND ended_at IS NULL "
                "ORDER BY started_at DESC, id DESC LIMIT 1",
                (name,),
            ).fetchone()
            if open_row is not None and open_row["state"] == state:
                conn.execute(
                    "UPDATE context_history SET confidence = ?, factors_json = ?, "
                    "payload_json = ?, updated_at = ? WHERE id = ?",
                    (confidence, factors_json, payload_json, ts, open_row["id"]),
                )
                return int(open_row["id"])
            if open_row is not None:
                conn.execute(
                    "UPDATE context_history SET ended_at = ? WHERE id = ?",
                    (ts, open_row["id"]),
                )
            cur = conn.execute(
                "INSERT INTO context_history (context_name, state, confidence, "
                "factors_json, payload_json, started_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (name, state, confidence, factors_json, payload_json, ts, ts),
            )
            return int(cur.lastrowid)

    def open_context_id(self, name: str) -> int | None:
        row = self._db.query_one(
            "SELECT id FROM context_history WHERE context_name = ? AND ended_at IS NULL "
            "ORDER BY started_at DESC, id DESC LIMIT 1",
            (name,),
        )
        return int(row["id"]) if row else None

    def get_context(self, record_id: int) -> ContextRecord | None:
        row = self._db.query_one("SELECT * FROM context_history WHERE id = ?", (record_id,))
        return _context(row) if row else None

    def list_explainable(self, limit: int = 20) -> list[ContextRecord]:
        """설명할 근거가 있는 판단만. suppression 은 판단 결과가 아니라 제외한다."""
        rows = self._db.query(
            "SELECT * FROM context_history WHERE context_name != 'suppression' "
            "AND (confidence IS NOT NULL OR (factors_json IS NOT NULL AND factors_json != '{}')) "
            "ORDER BY updated_at DESC, id DESC LIMIT ?",
            (limit,),
        )
        return [_context(r) for r in rows]

    # ------------------------------------------------------------ outcomes

    def add_outcome(
        self,
        notification_id: str | None,
        scenario: str | None,
        outcome: str,
        occurred_at: float,
        metadata: dict[str, Any],
    ) -> None:
        self._db.execute(
            "INSERT INTO intervention_outcomes (notification_id, scenario, outcome, "
            "occurred_at, metadata_json) VALUES (?, ?, ?, ?, ?)",
            (notification_id, scenario, outcome, occurred_at,
             json.dumps(metadata, ensure_ascii=False)),
        )

    def count_outcomes(self) -> int:
        row = self._db.query_one("SELECT COUNT(*) AS n FROM intervention_outcomes")
        return int(row["n"]) if row else 0
