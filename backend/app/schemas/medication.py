"""복약 일정. Flutter Medication 과 같은 구조.

앱은 일정만 저장한다. 언제 알림을 보낼지(식사 감지, 평소 식사 시간 대체)는
Context Engine 이 판단한다. 엔진 전달 방식은 아직 정해지지 않았다.
"""

from datetime import date, timedelta
from typing import List, Literal, Optional

from pydantic import Field, model_validator

from .common import ApiModel

# 식전 / 식사 직후 / 식후 30분
MealTiming = Literal["BEFORE_MEAL", "RIGHT_AFTER_MEAL", "AFTER_MEAL_30MIN"]
# 아침 / 점심 / 저녁 / 자기 전
DoseSlot = Literal["BREAKFAST", "LUNCH", "DINNER", "BEDTIME"]

SLOT_ORDER = ("BREAKFAST", "LUNCH", "DINNER", "BEDTIME")
MEAL_SLOTS = frozenset({"BREAKFAST", "LUNCH", "DINNER"})


class MedicationIn(ApiModel):
    name: str = Field(min_length=1, max_length=30)
    slots: List[DoseSlot] = Field(min_length=1)
    # 아침·점심·저녁 중 하나라도 있으면 필수. 자기 전만 있으면 식사와 무관하므로 비운다.
    meal_timing: Optional[MealTiming] = None
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
        self.slots = [s for s in SLOT_ORDER if s in self.slots]
        if MEAL_SLOTS.intersection(self.slots):
            if self.meal_timing is None:
                raise ValueError("아침·점심·저녁 약은 식사 기준(mealTiming)이 필요합니다.")
        else:
            self.meal_timing = None
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
            slots=m.slots,
            meal_timing=m.meal_timing,
            days=m.days,
            start_date=start,
            end_date=start + timedelta(days=m.days - 1),
            refill_required=m.refill_required,
        )
