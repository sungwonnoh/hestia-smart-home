"""MQTT 메시지 한 건을 받아 검증하고 cache/DB/Monitor 로 나눠 보낸다.

paho 에 의존하지 않으므로 테스트와 Mock MQTT 에서 그대로 부를 수 있다.
잘못된 메시지 하나가 수신 전체를 멈추지 않도록 실패는 기록하고 버린다.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections import Counter
from typing import Any, Callable

from pydantic import ValidationError

from ..repositories.history_repository import HistoryRepository
from ..schemas.context import CONTEXT_SCHEMAS, history_state, to_domain
from ..schemas.device import DeviceEventPayload, DeviceStatePayload, SensorStatePayload
from ..schemas.model import (
    MODEL_NAMES,
    ModelPayload,
    RegistryDevicesPayload,
    SystemProfilePayload,
)
from ..schemas.notification import (
    InterventionOutcomePayload,
    NotifyAckPayload,
    NotifyCancelPayload,
    NotifyPushPayload,
    payload_metadata,
)
from ..schemas.weather import WeatherPayload
from .context_service import StateCache
from .device_service import DeviceService
from .monitor_service import MonitorHub
from .notification_service import NotificationService, to_out
from .weather_service import WeatherService

log = logging.getLogger(__name__)

# FastAPI 가 구독하는 토픽 (명세 17-A.4 입력)
SUBSCRIPTIONS = (
    "hestia/sensor/+/state",
    "hestia/device/+/state",
    "hestia/device/+/event",
    "hestia/context/+",
    "hestia/model/+",
    "hestia/notify/push",
    "hestia/notify/ack",
    "hestia/notify/cancel",
    "hestia/intervention/outcome",
    "hestia/registry/devices",
    "hestia/system/profile",
    "hestia/external/weather",
)


# hestia/context/* 중 판단 결과가 아닌 것
NOT_JUDGMENTS = frozenset({"suppression", "day"})


class DropReason:
    DECODE = "decode"
    SCHEMA = "schema"
    SRC_MISMATCH = "src_mismatch"
    UNKNOWN_TOPIC = "unknown_topic"


class IngestService:
    def __init__(
        self,
        cache: StateCache,
        history: HistoryRepository,
        devices: DeviceService,
        notifications: NotificationService,
        monitor: MonitorHub,
        weather: WeatherService | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._cache = cache
        self._history = history
        self._devices = devices
        self._notifications = notifications
        self._monitor = monitor
        self._weather = weather
        self._clock = clock
        self._lock = threading.Lock()
        self.stats: Counter = Counter()

    def handle(self, topic: str, raw: bytes | str) -> bool:
        """처리했으면 True, 버렸으면 False."""
        try:
            payload = json.loads(raw)
        except (ValueError, UnicodeDecodeError) as exc:
            return self._drop(DropReason.DECODE, topic, exc)
        if not isinstance(payload, dict):
            return self._drop(DropReason.DECODE, topic, "최상위가 객체가 아님")

        parts = topic.split("/")
        try:
            handled = self._dispatch(parts, payload)
        except ValidationError as exc:
            return self._drop(DropReason.SCHEMA, topic, exc.errors()[0])
        except _SrcMismatch as exc:
            return self._drop(DropReason.SRC_MISMATCH, topic, exc)
        except Exception:  # 수신 스레드를 죽이지 않는다
            log.exception("메시지 처리 실패: topic=%s", topic)
            self._count("error")
            return False

        if not handled:
            self._count(DropReason.UNKNOWN_TOPIC)
            return False
        self._count("handled")
        return True

    # ------------------------------------------------------------ dispatch

    def _dispatch(self, parts: list[str], payload: dict[str, Any]) -> bool:
        if len(parts) < 2 or parts[0] != "hestia":
            return False
        kind = parts[1]
        recv_ts = self._clock()

        if kind == "sensor" and len(parts) == 4 and parts[3] == "state":
            vid = parts[2]
            msg = SensorStatePayload.model_validate(payload)
            _check_src(msg.src_id, vid)
            self._cache.set_sensor(vid, payload)
            self._emit("sensor_state", msg.sent_ts, "/".join(parts), payload,
                       sensor_state={vid: payload})
            return True

        if kind == "device" and len(parts) == 4 and parts[3] in {"state", "event"}:
            vid = parts[2]
            if parts[3] == "state":
                msg = DeviceStatePayload.model_validate(payload)
                _check_src(msg.src_id, vid)
                self._devices.on_state(vid, payload, recv_ts)
                self._emit("device_state", msg.sent_ts, "/".join(parts), payload,
                           device_state={vid: payload})
            else:
                event = DeviceEventPayload.model_validate(payload)
                _check_src(event.src_id, vid)
                self._cache.set_device_event(vid, payload)
                self._emit("device_event", event.sent_ts, "/".join(parts), payload)
            return True

        if kind == "context" and len(parts) == 3 and parts[2] in CONTEXT_SCHEMAS:
            self._on_context(parts[2], payload)
            return True

        if kind == "model" and len(parts) == 3 and parts[2] in MODEL_NAMES:
            model = ModelPayload.model_validate(payload)
            self._cache.set_model(parts[2], payload)
            self._emit("model_update", model.sent_ts, "/".join(parts),
                       {"name": parts[2], "trained_at": model.trained_at})
            return True

        if parts[1:] == ["notify", "push"]:
            push = NotifyPushPayload.model_validate(payload)
            record = self._notifications.on_push(push, payload)
            if record is not None:
                self._emit("notification", push.sent_ts, "hestia/notify/push",
                           to_out(record).model_dump(by_alias=True))
            return True

        if parts[1:] == ["notify", "ack"]:
            ack = NotifyAckPayload.model_validate(payload)
            if self._notifications.on_ack(ack, recv_ts):
                self._emit("notification_ack", ack.sent_ts, "hestia/notify/ack", payload)
            return True

        if parts[1:] == ["notify", "cancel"]:
            cancel = NotifyCancelPayload.model_validate(payload)
            self._notifications.on_cancel(cancel.notify_id)
            self._emit("notification_cancel", cancel.sent_ts, "hestia/notify/cancel", payload)
            return True

        if parts[1:] == ["intervention", "outcome"]:
            outcome = InterventionOutcomePayload.model_validate(payload)
            self._history.add_outcome(
                outcome.notify_id,
                outcome.scenario,
                outcome.outcome,
                float(outcome.occurred_at or outcome.sent_ts),
                payload_metadata(outcome),
            )
            self._emit("intervention_outcome", outcome.sent_ts,
                       "hestia/intervention/outcome", payload)
            return True

        if parts[1:] == ["registry", "devices"]:
            registry = RegistryDevicesPayload.model_validate(payload)
            self._cache.set_registry(registry.devices)
            self._devices.bind_unassigned()
            self._emit("registry_update", registry.sent_ts, "hestia/registry/devices", payload)
            return True

        if parts[1:] == ["external", "weather"] and self._weather is not None:
            weather = WeatherPayload.model_validate(payload)
            if self._weather.on_payload(weather):
                self._emit("weather_update", weather.sent_ts, "hestia/external/weather", payload)
            return True

        if parts[1:] == ["system", "profile"]:
            profile = SystemProfilePayload.model_validate(payload)
            self._cache.profile = profile.profile
            self._emit("profile_update", profile.sent_ts, "hestia/system/profile", payload)
            return True

        return False

    def _on_context(self, name: str, payload: dict[str, Any]) -> None:
        model = CONTEXT_SCHEMAS[name].model_validate(payload)
        self._cache.set_context(name, model, payload)
        domain = to_domain(model)
        # suppression 은 플래그 집합, day 는 하루 기록 — 판단 결과가 아니라 이력·설명에서 뺀다
        if name not in NOT_JUDGMENTS:
            self._history.record_context(
                name,
                history_state(name, model),
                domain.get("confidence"),
                domain.get("factors", {}),
                payload,
                float(model.sent_ts),
            )
        self._emit(
            "context_update",
            model.sent_ts,
            f"hestia/context/{name}",
            payload,
            context={
                "name": name,
                "state": history_state(name, model),
                "confidence": domain.get("confidence"),
                "factors": domain.get("factors", {}),
            },
        )

    # ------------------------------------------------------------ helpers

    def _emit(self, event_type: str, sent_ts: Any, topic: str, data: Any, **extra: Any) -> None:
        event = {"type": event_type, "sent_ts": sent_ts, "topic": topic, "data": data}
        event.update(extra)
        self._monitor.publish(event)

    def _drop(self, reason: str, topic: str, detail: Any) -> bool:
        log.warning("MQTT 메시지 폐기(%s): topic=%s %s", reason, topic, detail)
        self._count(f"dropped_{reason}")
        return False

    def _count(self, key: str) -> None:
        with self._lock:
            self.stats[key] += 1


class _SrcMismatch(Exception):
    pass


def _check_src(src_id: str, vid: str) -> None:
    """토픽의 virtual_id 와 payload 의 src_id 가 다르면 버린다 (Context Engine 과 동일)."""
    if src_id != vid:
        raise _SrcMismatch(f"토픽={vid} payload={src_id}")
