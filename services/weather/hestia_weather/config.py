"""집 위치 읽기.

    config/homes/<name>.toml 의 [location]

        [location]
        name = "서울"
        lat = 37.5665
        lon = 126.9780
        # nx / ny 를 적으면 위경도 변환 대신 그 값을 쓴다
        warning_areas = ["서울"]  # 특보 통보문에서 찾을 구역 이름. 없으면 [name], [] 면 특보 조회 안 함

Context Engine 과 같은 파일을 읽는다. Context Engine 로더는 [location] 을 쓰지 않는다.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

if sys.version_info >= (3, 11):
    import tomllib
else:                                           # RPi4 가 3.9 / 3.10 일 때
    import tomli as tomllib

from .grid import latlon_to_grid
from .service import Location


class ConfigError(ValueError):
    """설정이 없거나 잘못됨. 기동하지 않는다."""


def load_location(home_path: Path) -> Location:
    try:
        with home_path.open("rb") as fp:
            home = tomllib.load(fp)
    except FileNotFoundError:
        raise ConfigError(f"{home_path}: 파일 없음") from None
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{home_path}: TOML 오류 — {exc}") from None

    loc = home.get("location")
    if not isinstance(loc, dict):
        raise ConfigError(f"{home_path}: [location] 이 필요함")

    return location_from(loc, home_path)


def location_from(loc: dict[str, Any], where: Path | str = "[location]") -> Location:
    name = str(loc.get("name", ""))

    if "nx" in loc or "ny" in loc:
        nx, ny = _int(loc, "nx", where), _int(loc, "ny", where)
    else:
        lat, lon = _float(loc, "lat", where), _float(loc, "lon", where)
        if not (33 <= lat <= 39 and 124 <= lon <= 132):
            raise ConfigError(f"{where}: 한반도 밖 위경도 ({lat}, {lon})")
        nx, ny = latlon_to_grid(lat, lon)

    return Location(name=name, nx=nx, ny=ny, warning_areas=_warning_areas(loc, name, where))


def _warning_areas(loc: dict[str, Any], name: str, where: Path | str) -> tuple[str, ...]:
    if "warning_areas" not in loc:
        return (name,) if name else ()
    v = loc["warning_areas"]
    if not isinstance(v, list) or not all(isinstance(x, str) and x.strip() for x in v):
        raise ConfigError(f"{where}: location.warning_areas 는 비지 않은 문자열 배열이어야 함: {v!r}")
    return tuple(x.strip() for x in v)


def _float(loc: dict[str, Any], key: str, where: Path | str) -> float:
    v = loc.get(key)
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise ConfigError(f"{where}: location.{key} 는 숫자여야 함: {v!r}")
    return float(v)


def _int(loc: dict[str, Any], key: str, where: Path | str) -> int:
    v = loc.get(key)
    if isinstance(v, bool) or not isinstance(v, int):
        raise ConfigError(f"{where}: location.{key} 는 정수여야 함: {v!r}")
    return v
