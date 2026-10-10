"""외부 날씨: hestia/external/weather 수신 → GET /api/v1/weather, 연결 시 get 요청."""

from __future__ import annotations

import time

from conftest import envelope

from app.services.ingest_service import SUBSCRIPTIONS
from app.services.weather_service import WEATHER_GET_TOPIC, WEATHER_TOPIC


def weather(observed_at: float, **fields):
    return envelope("rpi4", **{
        "reason": "update",
        "source": "kma",
        "location": {"name": "서울", "nx": 60, "ny": 127},
        "observed_at": int(observed_at),
        "temperature_c": 31.2,
        "humidity_pct": 58.0,
        "precip_1h_mm": 0.0,
        "precip_type": "NONE",
        "wind_speed_ms": 1.8,
        "warnings": [{"name": "폭염경보", "type": "HEAT", "level": "WARNING"}],
        "warnings_issued_at": int(observed_at),
        **fields,
    })


def test_subscribed():
    assert WEATHER_TOPIC in SUBSCRIPTIONS


def test_404_before_any_weather(hestia):
    assert hestia.get("/weather").status_code == 404


def test_latest_weather_is_served(hestia):
    assert hestia.receive(WEATHER_TOPIC, weather(time.time() - 600))

    body = hestia.get("/weather").json()
    assert body["locationName"] == "서울"
    assert body["temperatureC"] == 31.2
    assert body["humidityPct"] == 58.0
    assert body["precipitationMm"] == 0.0
    assert body["precipType"] == "NONE"
    assert body["windSpeedMs"] == 1.8
    assert body["warnings"] == [{"name": "폭염경보", "type": "HEAT", "level": "WARNING"}]
    assert body["stale"] is False
    assert "T" in body["observedAt"]


def test_unknown_warnings_stay_null(hestia):
    hestia.receive(WEATHER_TOPIC, weather(time.time(), warnings=None))
    assert hestia.get("/weather").json()["warnings"] is None


def test_old_observation_is_stale(hestia):
    hestia.receive(WEATHER_TOPIC, weather(time.time() - 3 * 3600))
    assert hestia.get("/weather").json()["stale"] is True


def test_older_observation_does_not_overwrite(hestia):
    now = time.time()
    hestia.receive(WEATHER_TOPIC, weather(now, temperature_c=25.0))
    hestia.receive(WEATHER_TOPIC, weather(now - 3600, temperature_c=20.0))
    assert hestia.get("/weather").json()["temperatureC"] == 25.0


def test_invalid_payload_is_dropped(hestia):
    bad = weather(time.time())
    del bad["observed_at"]
    assert hestia.receive(WEATHER_TOPIC, bad) is False
    assert hestia.get("/weather").status_code == 404


def test_requests_weather_on_every_connect(hestia):
    hestia.mqtt.reconnect()
    hestia.mqtt.reconnect()

    gets = [p for p in hestia.mqtt.published if p[0] == WEATHER_GET_TOPIC]
    assert len(gets) == 2
    topic, payload, qos, retain = gets[0]
    assert (qos, retain) == (1, False)
    assert payload["src_id"] == "rpi5-api"
