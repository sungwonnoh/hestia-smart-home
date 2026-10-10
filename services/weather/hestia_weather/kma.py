"""기상청 공공데이터포털 API.

초단기실황 — 단기예보 조회서비스 getUltraSrtNcst
    관측은 매시 정시에 한 번 만들어지고, 몇 분 뒤부터 조회된다.
    그래서 이번 정시 자료가 아직 없으면(NO_DATA) 한 시간 전 자료를 받는다.

기상특보 — 기상특보 조회서비스 getWthrWrnMsg (통보문)
    전국 통보문(stnId=108)의 t6 이 '지금 발효 중인 특보 전체'다.
        t6 = "o 풍랑주의보 : 남해동부바깥먼바다, ... o 한파주의보 : 강원도(태백, 평창산지)"
        특보가 없으면 "o 없 음"
    가장 최근 통보문의 t6 에서 집 구역 이름(예: 서울)이 들어간 특보만 고른다.
    조회 기간은 오늘 기준 6일 전까지만 허용된다 (넘으면 resultCode 99).
"""

from __future__ import annotations

import json
import logging
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Sequence

log = logging.getLogger(__name__)

KST = timezone(timedelta(hours=9))

BASE_URL = "https://apis.data.go.kr/1360000/VilageFcstInfoService_2.0"
ULTRA_SRT_NCST = "getUltraSrtNcst"

WARNING_URL = "https://apis.data.go.kr/1360000/WthrWrnInfoService/getWthrWrnMsg"
WARNING_NATIONWIDE_STN = 108            # 기상청 본청 — 전국 특보 현황(t6)
WARNING_LOOKBACK_DAYS = 5               # 허용은 6일 전까지. 날짜 경계를 피해 하루 여유

RESULT_OK = "00"
RESULT_NO_DATA = "03"

# 실황 값이 이 크기 이상이면 결측 (기상청 결측값 -998.9, +900 이상 등)
MISSING_ABS = 900

# PTY 강수형태 (초단기실황)
PRECIP_TYPES = {
    0: "NONE",
    1: "RAIN",
    2: "RAIN_SNOW",
    3: "SNOW",
    5: "DRIZZLE",
    6: "DRIZZLE_SNOW",
    7: "SNOW_FLURRY",
}

HTTP_TIMEOUT_SEC = 10


class KmaError(RuntimeError):
    """조회 실패. 호출부가 잡아서 마지막 값을 유지한다."""


@dataclass(frozen=True)
class Observation:
    """한 정시의 실황."""

    observed_at: int                    # 관측 정시, epoch 초
    temperature_c: float | None         # T1H
    humidity_pct: float | None          # REH
    precip_1h_mm: float | None          # RN1
    precip_type: str | None             # PTY → PRECIP_TYPES
    wind_speed_ms: float | None         # WSD

    def to_payload(self) -> dict[str, Any]:
        return {
            "observed_at": self.observed_at,
            "temperature_c": self.temperature_c,
            "humidity_pct": self.humidity_pct,
            "precip_1h_mm": self.precip_1h_mm,
            "precip_type": self.precip_type,
            "wind_speed_ms": self.wind_speed_ms,
        }


# (url) → 응답 본문. 테스트에서 바꿔 끼운다.
Fetch = Callable[[str], bytes]


def http_get(url: str) -> bytes:
    try:
        with urllib.request.urlopen(url, timeout=HTTP_TIMEOUT_SEC) as resp:
            return resp.read()
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise KmaError(f"HTTP 실패: {exc}") from exc


def normalize_key(key: str) -> str:
    """공공데이터포털 키는 Encoding / Decoding 두 가지다.
    Encoding 키를 넣어도 이중 인코딩되지 않게 한 번 풀어 둔다."""

    key = key.strip()
    return urllib.parse.unquote(key) if "%" in key else key


def base_hour(now: float) -> datetime:
    """now 가 속한 정시 (KST)."""

    return datetime.fromtimestamp(now, KST).replace(minute=0, second=0, microsecond=0)


class KmaClient:
    def __init__(self, service_key: str, nx: int, ny: int, fetch: Fetch = http_get) -> None:
        if not service_key:
            raise ValueError("KMA_SERVICE_KEY 가 비었음")
        self._key = normalize_key(service_key)
        self._nx = nx
        self._ny = ny
        self._fetch = fetch

    def latest(self, now: float) -> Observation:
        """가장 최근 정시 실황. 이번 정시가 아직이면 한 시간 전."""

        hour = base_hour(now)
        obs = self.observation(hour)
        if obs is not None:
            return obs

        prev = hour - timedelta(hours=1)
        obs = self.observation(prev)
        if obs is None:
            raise KmaError(f"{hour:%H}시·{prev:%H}시 실황 모두 없음")
        return obs

    def observation(self, hour: datetime) -> Observation | None:
        """그 정시 실황. 아직 없으면 None."""

        items = _items(self._fetch(self._url(hour)))
        if items is None:
            return None
        return _observation(hour, {str(it.get("category")): it.get("obsrValue") for it in items})

    def warnings(self, now: float, area_keywords: Sequence[str]) -> Warnings:
        """지금 집 구역에 발효 중인 특보.

        최근 통보문이 하나도 없으면(NO_DATA) 전국에 특보 변동이 없었던 것으로 보고 빈 목록.
        """

        today = datetime.fromtimestamp(now, KST).date()
        params = {
            "serviceKey": self._key,
            "pageNo": 1,
            "numOfRows": 100,
            "dataType": "JSON",
            "stnId": WARNING_NATIONWIDE_STN,
            "fromTmFc": (today - timedelta(days=WARNING_LOOKBACK_DAYS)).strftime("%Y%m%d"),
            "toTmFc": today.strftime("%Y%m%d"),
        }
        items = _items(self._fetch(f"{WARNING_URL}?{urllib.parse.urlencode(params)}"))
        if not items:
            return Warnings(items=(), issued_at=None)

        latest = max(items, key=lambda it: (str(it.get("tmFc", "")), _int(it.get("tmSeq"))))
        return Warnings(
            items=tuple(
                w for w in parse_t6(str(latest.get("t6") or ""))
                if any(k and k in w.areas for k in area_keywords)
            ),
            issued_at=_tm(latest.get("tmFc")),
        )

    def _url(self, hour: datetime) -> str:
        params = {
            "serviceKey": self._key,
            "pageNo": 1,
            "numOfRows": 100,
            "dataType": "JSON",
            "base_date": hour.strftime("%Y%m%d"),
            "base_time": hour.strftime("%H00"),
            "nx": self._nx,
            "ny": self._ny,
        }
        return f"{BASE_URL}/{ULTRA_SRT_NCST}?{urllib.parse.urlencode(params)}"


# ==================================================================== 특보


# 특보 이름 앞부분 → type. 12개 현상 + 황사
WARNING_TYPES = {
    "폭염": "HEAT",
    "한파": "COLD_WAVE",
    "호우": "HEAVY_RAIN",
    "대설": "HEAVY_SNOW",
    "강풍": "WIND",
    "건조": "DRY",
    "태풍": "TYPHOON",
    "풍랑": "HIGH_SEAS",
    "폭풍해일": "STORM_SURGE",
    "지진해일": "TSUNAMI",
    "안개": "FOG",
    "황사": "YELLOW_DUST",
}

# 긴 것부터 본다 (중대경보 ⊃ 경보)
WARNING_LEVELS = (("중대경보", "SEVERE_WARNING"), ("주의보", "ADVISORY"), ("경보", "WARNING"))

# "o 이름 : 구역" — 구역 안에는 'o ' 가 나오지 않는다
_T6_ENTRY = re.compile(r"(?:^|\s)o\s+([^:]+?)\s*:\s*(.*?)(?=\s+o\s|$)")


@dataclass(frozen=True)
class WeatherWarning:
    name: str           # 통보문 원문, 예: 폭염경보
    type: str           # HEAT / COLD_WAVE / ... / OTHER
    level: str          # ADVISORY / WARNING / SEVERE_WARNING / OTHER
    areas: str          # 원문 구역 (집 구역 매칭에만 쓴다)

    def to_payload(self) -> dict[str, Any]:
        return {"name": self.name, "type": self.type, "level": self.level}


@dataclass(frozen=True)
class Warnings:
    items: tuple[WeatherWarning, ...]
    issued_at: int | None           # 근거 통보문 발표 시각, epoch 초. 최근 통보문이 없으면 None


def parse_t6(t6: str) -> list[WeatherWarning]:
    """t6(특보 현황) → 특보 목록. '없음' 이면 빈 목록."""

    out = []
    for m in _T6_ENTRY.finditer(" ".join(t6.split())):
        name, areas = m.group(1).strip(), m.group(2).strip()
        out.append(WeatherWarning(name=name, type=_warning_type(name), level=_warning_level(name), areas=areas))
    return out


def _warning_type(name: str) -> str:
    for prefix, code in sorted(WARNING_TYPES.items(), key=lambda kv: -len(kv[0])):
        if name.startswith(prefix):
            return code
    return "OTHER"


def _warning_level(name: str) -> str:
    for suffix, code in WARNING_LEVELS:
        if name.endswith(suffix):
            return code
    return "OTHER"


def _tm(value: Any) -> int | None:
    """YYYYMMDDHHMM (KST) → epoch 초."""
    try:
        return int(datetime.strptime(str(value).strip(), "%Y%m%d%H%M").replace(tzinfo=KST).timestamp())
    except ValueError:
        return None


def _int(value: Any) -> int:
    try:
        return int(str(value).strip())
    except ValueError:
        return 0


# ==================================================================== 파싱


def _items(raw: bytes) -> list[dict[str, Any]] | None:
    """응답 → item 목록. NO_DATA 거나 비었으면 None, 그 밖의 오류는 KmaError.

    키 오류 등은 dataType=JSON 이어도 XML(OpenAPI_ServiceResponse)로 온다.
    """

    text = raw.decode("utf-8", errors="replace").strip()
    try:
        doc = json.loads(text)
    except json.JSONDecodeError:
        raise KmaError(f"JSON 이 아닌 응답 (키·트래픽 오류일 수 있음): {text[:200]}") from None

    try:
        header = doc["response"]["header"]
        code = str(header["resultCode"])
    except (KeyError, TypeError):
        raise KmaError(f"응답 형식이 다름: {text[:200]}") from None

    if code == RESULT_NO_DATA:
        return None
    if code != RESULT_OK:
        raise KmaError(f"resultCode={code} {header.get('resultMsg', '')}")

    body = doc["response"].get("body") or {}
    items = (body.get("items") or {}).get("item") or []
    if isinstance(items, dict):             # 1건이면 배열이 아닐 수 있다
        items = [items]
    return items or None


def _number(value: Any) -> float | None:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return None if abs(v) >= MISSING_ABS else v


def _observation(hour: datetime, values: dict[str, Any]) -> Observation:
    pty = _number(values.get("PTY"))
    return Observation(
        observed_at=int(hour.timestamp()),
        temperature_c=_number(values.get("T1H")),
        humidity_pct=_number(values.get("REH")),
        precip_1h_mm=_number(values.get("RN1")),
        precip_type=None if pty is None else PRECIP_TYPES.get(int(pty)),
        wind_speed_ms=_number(values.get("WSD")),
    )
