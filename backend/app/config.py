"""실행 설정. 주소·경로는 코드에 고정하지 않고 환경변수로 받는다."""

from __future__ import annotations

import os
from dataclasses import dataclass

API_VERSION = "0.1.0"


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    # MQTT — RPi5 의 Mosquitto. 브로커 없이 API 만 띄울 때는 HESTIA_MQTT_ENABLED=0
    mqtt_enabled: bool = True
    mqtt_host: str = "localhost"
    mqtt_port: int = 1883
    mqtt_client_id: str = "hestia-api"

    # API 가 MQTT 로 발행할 때 쓰는 src_id (예: hestia/notify/ack)
    src_id: str = "rpi5-api"

    # SQLite 파일. 테스트는 ":memory:"
    db_path: str = "hestia.db"

    # 이 시간 동안 상태 보고가 없으면 가전을 offline 으로 본다.
    # 냉장고가 5분 주기로 보고하므로 그보다 넉넉하게 잡는다.
    device_stale_sec: float = 900.0

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            mqtt_enabled=_env_bool("HESTIA_MQTT_ENABLED", True),
            mqtt_host=os.getenv("MQTT_HOST", cls.mqtt_host),
            mqtt_port=int(os.getenv("MQTT_PORT", str(cls.mqtt_port))),
            mqtt_client_id=os.getenv("HESTIA_MQTT_CLIENT_ID", cls.mqtt_client_id),
            src_id=os.getenv("HESTIA_SRC_ID", cls.src_id),
            db_path=os.getenv("HESTIA_DB_PATH", cls.db_path),
            device_stale_sec=float(
                os.getenv("HESTIA_DEVICE_STALE_SEC", str(cls.device_stale_sec))
            ),
        )
