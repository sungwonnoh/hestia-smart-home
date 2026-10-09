"""SQLite 연결과 스키마.

DB 엔진은 확정되지 않았으므로 SQL 은 repositories/ 안에만 둔다.
MQTT 수신 스레드와 API 스레드가 함께 쓰므로 연결 하나를 잠금으로 보호한다.
실시간 원시 센서 데이터는 저장하지 않는다 (판단 결과·알림·설정·이력만).
"""

from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from typing import Any, Iterator, Sequence

SCHEMA = """
CREATE TABLE IF NOT EXISTS rooms (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS room_roles (
    room_id  TEXT NOT NULL REFERENCES rooms(id) ON DELETE CASCADE,
    role     TEXT NOT NULL,
    PRIMARY KEY (room_id, role)
);

-- room_id 는 비어 있을 수 있다 (공간을 지운 뒤 위치 미지정).
CREATE TABLE IF NOT EXISTS devices (
    id            TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    type          TEXT NOT NULL,
    room_id       TEXT NOT NULL DEFAULT '',
    virtual_id    TEXT UNIQUE,
    online        INTEGER NOT NULL DEFAULT 0,
    last_state    TEXT,
    last_seen_at  REAL
);

CREATE TABLE IF NOT EXISTS notifications (
    id              TEXT PRIMARY KEY,
    scenario        TEXT NOT NULL DEFAULT '',
    type            TEXT NOT NULL DEFAULT 'INFO',
    priority        TEXT NOT NULL DEFAULT 'normal',
    title           TEXT NOT NULL,
    message         TEXT NOT NULL DEFAULT '',
    room_id         TEXT,
    explanation_id  TEXT,
    created_at      REAL NOT NULL,
    delivered_at    REAL,
    seen_at         REAL,
    status          TEXT NOT NULL DEFAULT 'ACTIVE',
    payload_json    TEXT
);

CREATE TABLE IF NOT EXISTS context_history (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    context_name  TEXT NOT NULL,
    state         TEXT,
    confidence    REAL,
    factors_json  TEXT,
    payload_json  TEXT,
    started_at    REAL NOT NULL,
    updated_at    REAL NOT NULL,
    ended_at      REAL
);
CREATE INDEX IF NOT EXISTS ix_context_history_name
    ON context_history (context_name, started_at);

CREATE TABLE IF NOT EXISTS user_preferences (
    id                    INTEGER PRIMARY KEY CHECK (id = 1),
    notification_enabled  INTEGER NOT NULL,
    quiet_hours_enabled   INTEGER NOT NULL,
    quiet_hours_start     TEXT NOT NULL,
    quiet_hours_end       TEXT NOT NULL,
    sensitivity           TEXT NOT NULL,
    safety_enabled        INTEGER NOT NULL,
    emergency_contact     TEXT,
    updated_at            REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS intervention_outcomes (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    notification_id  TEXT,
    scenario         TEXT,
    outcome          TEXT NOT NULL,
    occurred_at      REAL NOT NULL,
    metadata_json    TEXT
);

-- 앱에서 등록한 복약 일정. slots 는 쉼표로 이은 값 (BREAKFAST,DINNER).
CREATE TABLE IF NOT EXISTS medications (
    id               TEXT PRIMARY KEY,
    name             TEXT NOT NULL,
    slots            TEXT NOT NULL,
    meal_timing      TEXT,
    days             INTEGER NOT NULL,
    start_date       TEXT NOT NULL,
    refill_required  INTEGER NOT NULL DEFAULT 0,
    created_at       REAL NOT NULL,
    updated_at       REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS app_meta (
    key    TEXT PRIMARY KEY,
    value  TEXT
);
"""


class Database:
    def __init__(self, path: str) -> None:
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(
            path,
            check_same_thread=False,
            isolation_level=None,  # 트랜잭션은 transaction() 으로 직접 연다
        )
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA foreign_keys = ON")
            self._conn.executescript(SCHEMA)

    def query(self, sql: str, params: Sequence[Any] = ()) -> list[sqlite3.Row]:
        with self._lock:
            return list(self._conn.execute(sql, params))

    def query_one(self, sql: str, params: Sequence[Any] = ()) -> sqlite3.Row | None:
        with self._lock:
            return self._conn.execute(sql, params).fetchone()

    def execute(self, sql: str, params: Sequence[Any] = ()) -> sqlite3.Cursor:
        with self._lock:
            return self._conn.execute(sql, params)

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            self._conn.execute("BEGIN")
            try:
                yield self._conn
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
            self._conn.execute("COMMIT")

    def close(self) -> None:
        with self._lock:
            self._conn.close()
