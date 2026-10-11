"""복약 일정. Flutter Medication 과 같은 구조.

앱은 일정만 저장한다. 언제 알림을 보낼지는 Context Engine 이 판단한다 (#40).

복약 시점(schedule)은 두 가지다.
    AFTER_MEAL  식후. 끼니를 고르지 않는다 — 엔진이 그 사람의 끼니 구간에 하루 최대 세 번 건다.
                delay_min 은 식사가 끝나고 몇 분 뒤 (앱은 0 = 식사 직후, 30 = 식후 30분)
    FIXED       정해진 시각 ("08:00"). '아침·저녁만', 식전, 자기 전 약은 이쪽으로 등록한다.

끼니 이름(아침·저녁)을 받지 않는 이유는 엔진의 끼니 구간에 이름이 없어서다.
"""

from datetime import date, timedelta
from typing import List, Literal, Optional, Tuple

from pydantic import Field, model_validator

from .common import ApiModel

ScheduleType = Literal["AFTER_MEAL", "FIXED"]

# 식후 약 기본 지연 (엔진도 delay_min 이 없으면 30분)
DEFAULT_DELAY_MIN = 30
MAX_DELAY_MIN = 180
MAX_TIMES = 6


def normalize_time(value: str) -> str:
    """'8:0' / '08:00' → '08:00'. 시각이 아니면 ValueError."""
    try:
        hour, minute = value.strip().split(":")
        h, m = int(hour), int(minute)
    except (ValueError, AttributeError):
        raise ValueError(f"시각은 HH:MM 이어야 합니다: {value!r}") from None
    if not (0 <= h < 24 and 0 <= m < 60):
        raise ValueError(f"시각이 아닙니다: {value!r}")
    return f"{h:02d}:{m:02d}"


class MedicationSchedule(ApiModel):
    type: ScheduleType
    # AFTER_MEAL 만. 비우면 30분
    delay_min: Optional[int] = Field(default=None, ge=0, le=MAX_DELAY_MIN)
    # FIXED 만. 하루 시각 순으로 정렬, 중복 제거
    times: List[str] = []

    @model_validator(mode="after")
    def _normalize(self) -> "MedicationSchedule":
        if self.type == "AFTER_MEAL":
            if self.delay_min is None:
                self.delay_min = DEFAULT_DELAY_MIN
            self.times = []
        else:
            times = sorted({normalize_time(t) for t in self.times})
            if not times:
                raise ValueError("정해진 시각 약은 시각이 하나 이상 필요합니다.")
            if len(times) > MAX_TIMES:
                raise ValueError(f"시각은 하루 {MAX_TIMES}개까지입니다.")
            self.times = times
            self.delay_min = None
        return self


class MedicationIn(ApiModel):
    name: str = Field(min_length=1, max_length=30)
    schedule: MedicationSchedule
    # 며칠분
    days: int = Field(ge=1, le=365)
    # 복용 시작일. 비우면 등록한 날.
    start_date: Optional[date] = None
    # 주기적으로 처방받아야 하는 약 (남은 일수 알림 대상)
    refill_required: bool = False

    @model_validator(mode="after")
    def _normalize(self) -> "MedicationIn":
        self.name = self.name.strip()
        if not self.name:
            raise ValueError("약 이름이 비어 있습니다.")
        return self


class MedicationOut(MedicationIn):
    id: str
    start_date: date
    # 마지막 복용일 (start_date + days - 1)
    end_date: date

    @classmethod
    def build(cls, id: str, m: MedicationIn, start: date) -> "MedicationOut":
        return cls(
            id=id,
            name=m.name,
            schedule=m.schedule,
            days=m.days,
            start_date=start,
            end_date=start + timedelta(days=m.days - 1),
            refill_required=m.refill_required,
        )


# ------------------------------------------------------------------ 이전 형식 변환

# 이전 앱(아침·점심·저녁·자기 전 + 식전/식사 직후/식후 30분)으로 저장한 일정.
# 식후 세 끼는 AFTER_MEAL 로, 나머지(일부 끼니, 식전, 자기 전)는 끼니별 기본 시각의 FIXED 로 바꾼다.
LEGACY_DEFAULT_TIMES = {
    "BREAKFAST": "08:00",
    "LUNCH": "12:00",
    "DINNER": "18:00",
    "BEDTIME": "22:00",
}
LEGACY_AFTER_MEAL_DELAY = {"RIGHT_AFTER_MEAL": 0, "AFTER_MEAL_30MIN": 30}


def legacy_schedule(slots: List[str], meal_timing: Optional[str]) -> Tuple[str, Optional[int], List[str]]:
    """(type, delay_min, times)."""
    chosen = [s for s in LEGACY_DEFAULT_TIMES if s in slots]
    if chosen == ["BREAKFAST", "LUNCH", "DINNER"] and meal_timing in LEGACY_AFTER_MEAL_DELAY:
        return "AFTER_MEAL", LEGACY_AFTER_MEAL_DELAY[meal_timing], []
    return "FIXED", None, [LEGACY_DEFAULT_TIMES[s] for s in chosen] or ["08:00"]
