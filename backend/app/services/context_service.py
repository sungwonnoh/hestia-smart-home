"""최신 MQTT 상태 cache 와 Context → API 변환.

- 원본 MQTT payload 는 그대로 보존한다 (Monitor 와 디버깅용).
- API 응답은 Envelope 를 뺀 Domain 표현으로 만든다.
- 판단 근거(factors)를 사람이 읽는 label 로 바꾸는 것만 한다. 새로 판단하지 않는다.
"""

from __future__ import annotations

import threading
from datetime import datetime
from typing import Any

from ..repositories.history_repository import ContextRecord, HistoryRepository
from ..schemas.common import MqttEnvelope
from ..schemas.context import CONTEXT_NAMES, to_domain
from ..schemas.model import Explanation, ExplanationFactor, RegistryEntry

AREA_NAMES = {
    "living": "거실",
    "kitchen": "주방",
    "bedroom": "침실",
    "bathroom": "욕실",
    "utility": "다용도실",
    "entrance": "현관",
}

FACTOR_NAMES = {
    "presence": "재실",
    "area": "위치",
}


def iso(ts: float | None) -> str | None:
    """epoch 초 → 로컬 타임존 ISO 8601 (예: 2026-09-30T19:30:00+09:00)."""
    if ts is None:
        return None
    return datetime.fromtimestamp(ts).astimezone().isoformat(timespec="seconds")


def factor_label(key: str, value: Any) -> str:
    if key == "area" and isinstance(value, str):
        return f"{AREA_NAMES.get(value, value)} 재실 감지"
    name = FACTOR_NAMES.get(key, key)
    if isinstance(value, bool):
        return f"{name} 감지" if value else f"{name} 없음"
    return f"{name}: {value}"


def to_factors(factors: dict[str, Any]) -> list[ExplanationFactor]:
    return [
        ExplanationFactor(
            key=key,
            value=value,
            label=factor_label(key, value),
            satisfied=value if isinstance(value, bool) else True,
        )
        for key, value in factors.items()
    ]


class StateCache:
    """MQTT 로 받은 최신 상태. 여러 스레드에서 읽고 쓴다."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._contexts: dict[str, MqttEnvelope] = {}
        self._context_raw: dict[str, dict[str, Any]] = {}
        self._devices: dict[str, dict[str, Any]] = {}
        self._device_seen: dict[str, float] = {}
        self._device_events: dict[str, dict[str, Any]] = {}
        self._sensors: dict[str, dict[str, Any]] = {}
        self._models: dict[str, dict[str, Any]] = {}
        self._registry: dict[str, RegistryEntry] = {}
        self.profile: str | None = None

    # ------------------------------------------------------------ writes

    def set_context(self, name: str, model: MqttEnvelope, raw: dict[str, Any]) -> None:
        with self._lock:
            self._contexts[name] = model
            self._context_raw[name] = raw

    def set_device(self, vid: str, raw: dict[str, Any], recv_ts: float) -> None:
        with self._lock:
            self._devices[vid] = raw
            self._device_seen[vid] = recv_ts

    def device(self, vid: str) -> tuple[dict[str, Any], float] | None:
        """마지막 상태 payload 와 수신 시각."""
        with self._lock:
            if vid not in self._devices:
                return None
            return self._devices[vid], self._device_seen[vid]

    def set_device_event(self, vid: str, raw: dict[str, Any]) -> None:
        with self._lock:
            self._device_events[vid] = raw

    def set_sensor(self, vid: str, raw: dict[str, Any]) -> None:
        with self._lock:
            self._sensors[vid] = raw

    def set_model(self, name: str, raw: dict[str, Any]) -> None:
        with self._lock:
            self._models[name] = raw

    def set_registry(self, entries: list[RegistryEntry]) -> None:
        with self._lock:
            self._registry = {e.virtual_id: e for e in entries}

    # ------------------------------------------------------------ reads

    def current_context(self) -> dict[str, dict[str, Any]]:
        """GET /api/v1/context/current — 받은 context 만, 명세 순서대로."""
        with self._lock:
            return {
                name: to_domain(self._contexts[name])
                for name in CONTEXT_NAMES
                if name in self._contexts
            }

    def known_devices(self) -> dict[str, dict[str, Any]]:
        """virtual_id → {device_type, area}. registry 와 실제 수신 기록을 합친다."""
        with self._lock:
            known: dict[str, dict[str, Any]] = {}
            for vid, entry in self._registry.items():
                if entry.device_type and entry.enabled:
                    known[vid] = {"device_type": entry.device_type, "area": entry.area}
            for vid, raw in self._devices.items():
                known.setdefault(vid, {"device_type": raw.get("device_type"), "area": None})
            return known

    def snapshot(self) -> dict[str, Any]:
        """Monitor 접속 직후 한 번 보내는 전체 상태 (원본 payload)."""
        with self._lock:
            return {
                "context": dict(self._context_raw),
                "device_state": dict(self._devices),
                "device_event": dict(self._device_events),
                "sensor_state": dict(self._sensors),
                "model": {
                    name: {"trained_at": raw.get("trained_at"), "sent_ts": raw.get("sent_ts")}
                    for name, raw in self._models.items()
                },
                "profile": self.profile,
            }


class ExplanationService:
    def __init__(self, history: HistoryRepository) -> None:
        self._history = history

    def latest(self) -> Explanation | None:
        rows = self._history.list_explainable(limit=1)
        return self._build(rows[0]) if rows else None

    def list(self, limit: int = 20) -> list[Explanation]:
        return [self._build(r) for r in self._history.list_explainable(limit=limit)]

    def get(self, explanation_id: str) -> Explanation | None:
        if not explanation_id.isdigit():
            return None
        row = self._history.get_context(int(explanation_id))
        return self._build(row) if row else None

    def _build(self, row: ContextRecord) -> Explanation:
        notification = self._history.latest_notification_for(str(row.id))
        action = f"'{notification.title}' 알림을 보냈습니다." if notification else None
        return Explanation(
            id=str(row.id),
            context_name=row.context_name,
            state=row.state or "UNKNOWN",
            confidence=row.confidence,
            factors=to_factors(row.factors),
            action=action,
            created_at=iso(row.updated_at),
        )
