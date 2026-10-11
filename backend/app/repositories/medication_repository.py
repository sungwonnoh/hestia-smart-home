"""복약 일정 저장."""

from __future__ import annotations

import sqlite3
import time
import uuid
from datetime import date

from ..schemas.medication import MedicationIn, MedicationOut, MedicationSchedule
from .database import Database


class MedicationRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    def list(self) -> list[MedicationOut]:
        rows = self._db.query("SELECT * FROM medications ORDER BY created_at, id")
        return [_medication(r) for r in rows]

    def get(self, medication_id: str) -> MedicationOut | None:
        row = self._db.query_one("SELECT * FROM medications WHERE id = ?", (medication_id,))
        return _medication(row) if row else None

    def add(self, m: MedicationIn) -> MedicationOut:
        out = MedicationOut.build(f"med-{uuid.uuid4().hex[:8]}", m, m.start_date or date.today())
        now = time.time()
        self._db.execute(
            "INSERT INTO medications (id, name, schedule_type, delay_min, times, days, "
            "start_date, refill_required, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (out.id, out.name, *_schedule_columns(out.schedule), out.days,
             out.start_date.isoformat(), int(out.refill_required), now, now),
        )
        return out

    def update(self, medication_id: str, m: MedicationIn) -> MedicationOut | None:
        """없으면 None. start_date 를 비우면 기존 시작일을 유지한다."""
        current = self.get(medication_id)
        if current is None:
            return None
        out = MedicationOut.build(medication_id, m, m.start_date or current.start_date)
        self._db.execute(
            "UPDATE medications SET name = ?, schedule_type = ?, delay_min = ?, times = ?, "
            "days = ?, start_date = ?, refill_required = ?, updated_at = ? WHERE id = ?",
            (out.name, *_schedule_columns(out.schedule), out.days,
             out.start_date.isoformat(), int(out.refill_required), time.time(), medication_id),
        )
        return out

    def delete(self, medication_id: str) -> bool:
        cur = self._db.execute("DELETE FROM medications WHERE id = ?", (medication_id,))
        return cur.rowcount > 0


def _schedule_columns(s: MedicationSchedule) -> tuple[str, int | None, str]:
    return s.type, s.delay_min, ",".join(s.times)


def _medication(row: sqlite3.Row) -> MedicationOut:
    m = MedicationIn(
        name=row["name"],
        schedule=MedicationSchedule(
            type=row["schedule_type"],
            delay_min=row["delay_min"],
            times=[t for t in row["times"].split(",") if t],
        ),
        days=row["days"],
        refill_required=bool(row["refill_required"]),
    )
    return MedicationOut.build(row["id"], m, date.fromisoformat(row["start_date"]))
