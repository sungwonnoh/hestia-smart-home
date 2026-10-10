"""위경도 → 기상청 동네예보 격자 (nx, ny).

기상청 단기예보 조회서비스 활용가이드의 Lambert Conformal Conic 변환.
격자 간격 5km. 서울시청(37.5665, 126.9780) → (60, 127).
"""

from __future__ import annotations

import math

EARTH_RADIUS_KM = 6371.00877
GRID_KM = 5.0
STANDARD_LAT_1 = 30.0
STANDARD_LAT_2 = 60.0
ORIGIN_LON = 126.0
ORIGIN_LAT = 38.0
ORIGIN_X = 43
ORIGIN_Y = 136


def latlon_to_grid(lat: float, lon: float) -> tuple[int, int]:
    """위도·경도(도) → (nx, ny)."""

    deg = math.pi / 180.0
    re = EARTH_RADIUS_KM / GRID_KM
    slat1 = STANDARD_LAT_1 * deg
    slat2 = STANDARD_LAT_2 * deg
    olon = ORIGIN_LON * deg
    olat = ORIGIN_LAT * deg

    sn = math.log(math.cos(slat1) / math.cos(slat2)) / math.log(
        math.tan(math.pi * 0.25 + slat2 * 0.5) / math.tan(math.pi * 0.25 + slat1 * 0.5)
    )
    sf = math.tan(math.pi * 0.25 + slat1 * 0.5) ** sn * math.cos(slat1) / sn
    ro = re * sf / math.tan(math.pi * 0.25 + olat * 0.5) ** sn

    ra = re * sf / math.tan(math.pi * 0.25 + lat * deg * 0.5) ** sn
    theta = lon * deg - olon
    if theta > math.pi:
        theta -= 2.0 * math.pi
    if theta < -math.pi:
        theta += 2.0 * math.pi
    theta *= sn

    nx = math.floor(ra * math.sin(theta) + ORIGIN_X + 0.5)
    ny = math.floor(ro - ra * math.cos(theta) + ORIGIN_Y + 0.5)
    return nx, ny
