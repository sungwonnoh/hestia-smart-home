"""외부 날씨. RPi4 weather 서비스(services/weather)가 hestia/external/weather 로 보낸다.

retained 가 아니므로 Backend 는 연결될 때마다 hestia/external/weather/get 을 보내 최신 값을 받는다.
"""

from typing import List, Optional

from pydantic import BaseModel, ConfigDict

from .common import ApiModel, MqttEnvelope, Number

# 관측이 매시 정시 한 번이라, 이보다 오래되면 갱신이 끊긴 것으로 본다 (weather 서비스와 같은 값)
WEATHER_STALE_SEC = 5400


# ------------------------------------------------------------------ MQTT


class WeatherLocation(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str = ""


class WeatherWarningPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str
    type: str
    level: str


class WeatherPayload(MqttEnvelope):
    """hestia/external/weather"""

    observed_at: Number
    location: Optional[WeatherLocation] = None
    temperature_c: Optional[float] = None
    humidity_pct: Optional[float] = None
    precip_1h_mm: Optional[float] = None
    precip_type: Optional[str] = None
    wind_speed_ms: Optional[float] = None
    # null = 모름, [] = 집 구역에 특보 없음
    warnings: Optional[List[WeatherWarningPayload]] = None
    warnings_issued_at: Optional[Number] = None


# ------------------------------------------------------------------ API


class WeatherWarning(ApiModel):
    name: str            # 기상청 원문, 예: 폭염경보
    type: str            # HEAT / COLD_WAVE / HEAVY_RAIN / ...
    level: str           # ADVISORY / WARNING / SEVERE_WARNING


class Weather(ApiModel):
    location_name: str
    observed_at: str
    temperature_c: Optional[float] = None
    humidity_pct: Optional[float] = None
    precipitation_mm: Optional[float] = None       # 1시간 강수량
    precip_type: Optional[str] = None              # NONE / RAIN / RAIN_SNOW / SNOW / ...
    wind_speed_ms: Optional[float] = None
    # null = 특보를 아직 모름, [] = 특보 없음
    warnings: Optional[List[WeatherWarning]] = None
    # 관측이 WEATHER_STALE_SEC 보다 오래됨 — RPi4 갱신이 끊겼을 수 있다
    stale: bool = False
