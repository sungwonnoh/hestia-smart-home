"""복약 일정 저장과 Context Engine 전달.

일정이 바뀔 때마다 전체 목록을 hestia/registry/medications 에 retained 로 발행한다.
엔진은 재시작해도 구독하자마자 최신 일정을 받는다. 전체 목록이므로 삭제도 그대로 반영되고,
일정이 없으면 빈 목록을 보낸다. 날짜 판단은 엔진이 end_date 로 스스로 한다 (매일 보내지 않는다).

형식은 Context Engine #40 의 parse_medications 와 같다.
    schedule  {"type": "after_meal", "delay_min": 30} / {"type": "fixed", "times": ["08:00", "20:00"]}
엔진은 모르는 schedule type 이 하나라도 있으면 목록 전체를 버리므로 이 두 가지만 보낸다.

medications 항목은 다른 MQTT 메시지처럼 snake_case 를 쓴다. REST 의 refillRequired 는
refill_notice (남은 일수가 적을 때 처방 안내가 필요한 약)로 보낸다.
형식을 바꾸면 envelope version 을 올린다.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, Protocol

from ..repositories.medication_repository import MedicationRepository
from ..schemas.common import SCHEMA_VERSION
from ..schemas.medication import MedicationIn, MedicationOut, MedicationSchedule

log = logging.getLogger(__name__)

SCHEDULE_TOPIC = "hestia/registry/medications"


class Publisher(Protocol):
    def publish(self, topic: str, payload: dict[str, Any], *, qos: int = 1,
                retain: bool = False) -> bool: ...


def schedule_to_mqtt(s: MedicationSchedule) -> dict[str, Any]:
    if s.type == "AFTER_MEAL":
        return {"type": "after_meal", "delay_min": s.delay_min}
    return {"type": "fixed", "times": list(s.times)}


def to_mqtt(m: MedicationOut) -> dict[str, Any]:
    return {
        "id": m.id,
        "name": m.name,
        "schedule": schedule_to_mqtt(m.schedule),
        "start_date": m.start_date.isoformat(),
        "days": m.days,
        "end_date": m.end_date.isoformat(),
        "refill_notice": m.refill_required,
    }


class MedicationService:
    def __init__(
        self,
        repo: MedicationRepository,
        publisher: Publisher,
        src_id: str,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._repo = repo
        self._publisher = publisher
        self._src_id = src_id
        self._clock = clock

    def list(self) -> list[MedicationOut]:
        return self._repo.list()

    def add(self, m: MedicationIn) -> MedicationOut:
        saved = self._repo.add(m)
        self.publish_schedule()
        return saved

    def update(self, medication_id: str, m: MedicationIn) -> MedicationOut | None:
        saved = self._repo.update(medication_id, m)
        if saved is not None:
            self.publish_schedule()
        return saved

    def delete(self, medication_id: str) -> bool:
        deleted = self._repo.delete(medication_id)
        if deleted:
            self.publish_schedule()
        return deleted

    def schedule_payload(self) -> dict[str, Any]:
        return {
            "version": SCHEMA_VERSION,
            "sent_ts": int(self._clock()),
            "src_id": self._src_id,
            "medications": [to_mqtt(m) for m in self._repo.list()],
        }

    def publish_schedule(self) -> bool:
        """브로커가 끊겨 있으면 False. 다시 연결될 때 MqttService 가 다시 부른다."""
        published = self._publisher.publish(SCHEDULE_TOPIC, self.schedule_payload(),
                                            qos=1, retain=True)
        if not published:
            log.warning("복약 일정을 MQTT 로 발행하지 못함 (연결되면 다시 발행)")
        return published
