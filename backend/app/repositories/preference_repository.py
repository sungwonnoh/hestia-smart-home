"""사용자 설정 (단일 행)."""

from __future__ import annotations

import time

from ..schemas.preference import QuietHours, SafetyPreferences, UserPreferences
from .database import Database


class PreferenceRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    def get(self) -> UserPreferences:
        row = self._db.query_one("SELECT * FROM user_preferences WHERE id = 1")
        if row is None:
            return UserPreferences()
        return UserPreferences(
            notifications_enabled=bool(row["notification_enabled"]),
            quiet_hours=QuietHours(
                enabled=bool(row["quiet_hours_enabled"]),
                start=row["quiet_hours_start"],
                end=row["quiet_hours_end"],
            ),
            sensitivity=row["sensitivity"],
            safety=SafetyPreferences(
                enabled=bool(row["safety_enabled"]),
                emergency_contact=row["emergency_contact"],
            ),
        )

    def save(self, prefs: UserPreferences) -> UserPreferences:
        self._db.execute(
            "INSERT OR REPLACE INTO user_preferences (id, notification_enabled, "
            "quiet_hours_enabled, quiet_hours_start, quiet_hours_end, sensitivity, "
            "safety_enabled, emergency_contact, updated_at) "
            "VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                int(prefs.notifications_enabled),
                int(prefs.quiet_hours.enabled),
                prefs.quiet_hours.start,
                prefs.quiet_hours.end,
                prefs.sensitivity,
                int(prefs.safety.enabled),
                prefs.safety.emergency_contact,
                time.time(),
            ),
        )
        return prefs
