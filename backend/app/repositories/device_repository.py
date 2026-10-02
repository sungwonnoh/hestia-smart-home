"""공간·가전·설정 완료 여부."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, Iterable

from ..schemas.device import DeviceIn
from ..schemas.setup import Room
from .database import Database


class ConflictError(Exception):
    pass


class NotFoundError(Exception):
    pass


@dataclass
class DeviceRecord:
    id: str
    name: str
    type: str
    room_id: str
    virtual_id: str | None
    last_state: dict[str, Any] | None
    last_seen_at: float | None


def _device(row) -> DeviceRecord:
    return DeviceRecord(
        id=row["id"],
        name=row["name"],
        type=row["type"],
        room_id=row["room_id"] or "",
        virtual_id=row["virtual_id"],
        last_state=json.loads(row["last_state"]) if row["last_state"] else None,
        last_seen_at=row["last_seen_at"],
    )


class DeviceRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    # ------------------------------------------------------------ rooms

    def list_rooms(self) -> list[Room]:
        rows = self._db.query("SELECT id, name FROM rooms ORDER BY created_at, rowid")
        roles: dict[str, list[str]] = {}
        for r in self._db.query("SELECT room_id, role FROM room_roles ORDER BY role"):
            roles.setdefault(r["room_id"], []).append(r["role"])
        return [Room(id=r["id"], name=r["name"], roles=roles.get(r["id"], [])) for r in rows]

    def create_room(self, room: Room) -> Room:
        with self._db.transaction() as conn:
            if conn.execute("SELECT 1 FROM rooms WHERE id = ?", (room.id,)).fetchone():
                raise ConflictError(f"이미 있는 공간입니다: {room.id}")
            self._insert_room(conn, room, time.time())
        return room

    @staticmethod
    def _insert_room(conn, room: Room, now: float) -> None:
        conn.execute(
            "INSERT INTO rooms (id, name, created_at, updated_at) VALUES (?, ?, ?, ?)",
            (room.id, room.name, now, now),
        )
        conn.executemany(
            "INSERT OR IGNORE INTO room_roles (room_id, role) VALUES (?, ?)",
            [(room.id, role) for role in room.roles],
        )

    # ------------------------------------------------------------ devices

    def list_devices(self) -> list[DeviceRecord]:
        return [_device(r) for r in self._db.query("SELECT * FROM devices ORDER BY id")]

    def get_device(self, device_id: str) -> DeviceRecord | None:
        row = self._db.query_one("SELECT * FROM devices WHERE id = ?", (device_id,))
        return _device(row) if row else None

    def get_by_virtual_id(self, virtual_id: str) -> DeviceRecord | None:
        row = self._db.query_one("SELECT * FROM devices WHERE virtual_id = ?", (virtual_id,))
        return _device(row) if row else None

    def update_device(self, device_id: str, fields: dict[str, Any]) -> DeviceRecord:
        allowed = {"name", "type", "room_id", "virtual_id"}
        updates = {k: v for k, v in fields.items() if k in allowed}
        with self._db.transaction() as conn:
            if not conn.execute("SELECT 1 FROM devices WHERE id = ?", (device_id,)).fetchone():
                raise NotFoundError(device_id)
            vid = updates.get("virtual_id")
            if vid:
                other = conn.execute(
                    "SELECT id FROM devices WHERE virtual_id = ? AND id != ?",
                    (vid, device_id),
                ).fetchone()
                if other:
                    raise ConflictError(f"{vid} 는 이미 {other['id']} 에 연결되어 있습니다.")
            if updates:
                cols = ", ".join(f"{k} = ?" for k in updates)
                conn.execute(
                    f"UPDATE devices SET {cols} WHERE id = ?",
                    (*updates.values(), device_id),
                )
        return self.get_device(device_id)  # type: ignore[return-value]

    def bind(self, device_id: str, virtual_id: str) -> None:
        self._db.execute(
            "UPDATE devices SET virtual_id = ? WHERE id = ? AND virtual_id IS NULL",
            (virtual_id, device_id),
        )

    def save_state(self, virtual_id: str, state: dict[str, Any], seen_at: float) -> bool:
        """연결된 가전이 있으면 상태를 저장하고 True."""
        cur = self._db.execute(
            "UPDATE devices SET last_state = ?, last_seen_at = ?, online = 1 "
            "WHERE virtual_id = ?",
            (json.dumps(state, ensure_ascii=False), seen_at, virtual_id),
        )
        return cur.rowcount > 0

    # ------------------------------------------------------------ setup

    def is_configured(self) -> bool:
        return self._db.query_one("SELECT 1 FROM app_meta WHERE key = 'setup_completed_at'") is not None

    def replace_setup(self, rooms: Iterable[Room], devices: Iterable[DeviceIn]) -> None:
        """공간·가전을 통째로 교체한다. 같은 id 의 가전은 마지막 상태를 유지한다."""
        now = time.time()
        devices = list(devices)
        with self._db.transaction() as conn:
            previous = {r["id"]: r for r in conn.execute("SELECT * FROM devices")}
            conn.execute("DELETE FROM room_roles")
            conn.execute("DELETE FROM rooms")
            for room in rooms:
                self._insert_room(conn, room, now)

            conn.execute("DELETE FROM devices")
            for d in devices:
                old = previous.get(d.id)
                vid = d.virtual_id or (old["virtual_id"] if old else None)
                conn.execute(
                    "INSERT INTO devices (id, name, type, room_id, virtual_id, online, "
                    "last_state, last_seen_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        d.id, d.name, d.type, d.room_id, vid,
                        old["online"] if old else 0,
                        old["last_state"] if old else None,
                        old["last_seen_at"] if old else None,
                    ),
                )
            conn.execute(
                "INSERT OR REPLACE INTO app_meta (key, value) VALUES ('setup_completed_at', ?)",
                (str(now),),
            )
