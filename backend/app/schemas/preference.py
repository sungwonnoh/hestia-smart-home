"""사용자 알림/개인화 설정. Flutter UserPreferences 와 같은 구조."""

from typing import Literal, Optional

from pydantic import Field

from .common import ApiModel

HHMM = r"^([01]\d|2[0-3]):[0-5]\d$"


class QuietHours(ApiModel):
    enabled: bool = True
    start: str = Field("22:00", pattern=HHMM)
    end: str = Field("07:00", pattern=HHMM)


class SafetyPreferences(ApiModel):
    """안전 알림은 일반 알림과 분리한다."""

    enabled: bool = True
    emergency_contact: Optional[str] = None


class UserPreferences(ApiModel):
    notifications_enabled: bool = True
    quiet_hours: QuietHours = QuietHours()
    sensitivity: Literal["low", "normal", "high"] = "normal"
    safety: SafetyPreferences = SafetyPreferences()
