"""외부 날씨 최신 값과 요청.

    수신  hestia/external/weather       update(새 실황·특보 변경) / reply(get 응답)
    발행  hestia/external/weather/get   MQTT 에 (재)연결될 때마다. retained 가 아니라서
                                        재시작 직후에는 이렇게 받아야 바로 날씨가 생긴다
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable

from ..schemas.common import SCHEMA_VERSION
from ..schemas.weather import WEATHER_STALE_SEC, Weather, WeatherPayload, WeatherWarning
from .context_service import iso

log = logging.getLogger(__name__)

WEATHER_TOPIC = "hestia/external/weather"
WEATHER_GET_TOPIC = "hestia/external/weather/get"


class WeatherService:
    def __init__(self, mqtt: Any, src_id: str, clock: Callable[[], float] = time.time) -> None:
        self._mqtt = mqtt
        self._src_id = src_id
        self._clock = clock
        self._lock = threading.Lock()
        self._latest: WeatherPayload | None = None

    def on_payload(self, payload: WeatherPayload) -> bool:
        """더 오래된 관측이 늦게 와도 덮어쓰지 않는다. 반영했으면 True."""
        with self._lock:
            cur = self._latest
            if cur is not None and payload.observed_at < cur.observed_at:
                return False
            self._latest = payload
            return True

    def request(self) -> bool:
        """RPi4 에 최신 날씨를 요청한다. 응답은 hestia/external/weather 로 온다."""
        sent = self._mqtt.publish(
            WEATHER_GET_TOPIC,
            {"version": SCHEMA_VERSION, "sent_ts": int(self._clock()), "src_id": self._src_id},
            qos=1,
            retain=False,
        )
        if not sent:
            log.warning("날씨 요청 발행 실패")
        return sent

    def latest(self) -> Weather | None:
        with self._lock:
            p = self._latest
        if p is None:
            return None
        return Weather(
            location_name=p.location.name if p.location else "",
            observed_at=iso(float(p.observed_at)),
            temperature_c=p.temperature_c,
            humidity_pct=p.humidity_pct,
            precipitation_mm=p.precip_1h_mm,
            precip_type=p.precip_type,
            wind_speed_ms=p.wind_speed_ms,
            warnings=None if p.warnings is None else [
                WeatherWarning(name=w.name, type=w.type, level=w.level) for w in p.warnings
            ],
            stale=self._clock() - float(p.observed_at) > WEATHER_STALE_SEC,
        )
