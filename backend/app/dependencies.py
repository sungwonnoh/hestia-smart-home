"""서비스 조립과 FastAPI 의존성."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fastapi import Request

from .config import Settings
from .repositories.database import Database
from .repositories.device_repository import DeviceRepository
from .repositories.history_repository import HistoryRepository
from .repositories.preference_repository import PreferenceRepository
from .services.context_service import ExplanationService, StateCache
from .services.device_service import DeviceService
from .services.ingest_service import SUBSCRIPTIONS, IngestService
from .services.monitor_service import MonitorHub
from .services.mqtt_service import MqttService, NullMqttService
from .services.notification_service import NotificationService


@dataclass
class Container:
    settings: Settings
    db: Database
    device_repo: DeviceRepository
    preference_repo: PreferenceRepository
    history: HistoryRepository
    cache: StateCache
    devices: DeviceService
    notifications: NotificationService
    explanations: ExplanationService
    monitor: MonitorHub
    ingest: IngestService
    mqtt: Any  # MqttService | NullMqttService | 테스트용 가짜


def build_container(settings: Settings, mqtt: Any = None) -> Container:
    if mqtt is None:
        mqtt = (
            MqttService(settings.mqtt_host, settings.mqtt_port,
                        settings.mqtt_client_id, SUBSCRIPTIONS)
            if settings.mqtt_enabled
            else NullMqttService()
        )
    db = Database(settings.db_path)
    device_repo = DeviceRepository(db)
    history = HistoryRepository(db)
    cache = StateCache()
    monitor = MonitorHub()
    devices = DeviceService(device_repo, cache, settings.device_stale_sec)
    notifications = NotificationService(history, mqtt, settings.src_id,
                                        area_of=cache.registry_area)
    ingest = IngestService(cache, history, devices, notifications, monitor)
    mqtt.set_handler(ingest.handle)
    return Container(
        settings=settings,
        db=db,
        device_repo=device_repo,
        preference_repo=PreferenceRepository(db),
        history=history,
        cache=cache,
        devices=devices,
        notifications=notifications,
        explanations=ExplanationService(history),
        monitor=monitor,
        ingest=ingest,
        mqtt=mqtt,
    )


def get_container(request: Request) -> Container:
    return request.app.state.hestia
