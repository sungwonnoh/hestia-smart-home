"""
hestia/model/kde payload 생성과 검증.

    {
      "version": 1, "sent_ts": ..., "src_id": "rpi4", "trained_at": ...,
      "sample_days": ...,
      "distributions": {name: {"grid_min", "grid_step", "density"}},
      "predictability": {name: 0~1}
    }

발행 전에 Context Engine(model.validate_kde)이 거부할 payload를 먼저 걸러낸다.
RPi5는 깨진 모델을 받으면 직전 모델을 유지하므로, 여기서 막지 않으면
학습이 조용히 반영되지 않는다.
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import time

from baseline import (
    add_input_arguments,
    build_model,
    grid_for,
    samples_from_args,
)
from samples import DISTRIBUTION_KIND, MINUTES_PER_DAY, is_time_of_day


log = logging.getLogger(__name__)


TOPIC = "hestia/model/kde"
VERSION = 1
SRC_ID = "rpi4"

PAYLOAD_KEYS = (
    "version",
    "sent_ts",
    "src_id",
    "trained_at",
    "sample_days",
    "distributions",
    "predictability",
)

# density 합 허용 오차. baseline이 정규화하므로 float 오차만 허용한다.
DENSITY_SUM_TOLERANCE = 1e-6


class PayloadError(ValueError):
    """hestia/model/kde 명세를 만족하지 않는 payload"""


def _is_number(value) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def build_kde_payload(
    model: dict | None = None,
    trained_at: float | None = None,
    sent_ts: float | None = None,
) -> dict:
    """
    baseline.build_model() 결과를 hestia/model/kde payload로 바꾼다.

    trained_at 은 학습 시각, sent_ts 는 발행 시각이다.
    mqtt_publisher 는 보내기 직전에 stamp_sent() 로 sent_ts 를 다시 찍는다.
    """

    if model is None:
        model = build_model()

    now = int(time.time())

    payload = {
        "version": VERSION,
        "sent_ts": int(sent_ts) if sent_ts is not None else now,
        "src_id": SRC_ID,
        "trained_at": int(trained_at) if trained_at is not None else now,
        "sample_days": model["sample_days"],
        "distributions": model["distributions"],
        "predictability": model["predictability"],
    }

    validate_kde_payload(payload)

    missing = missing_distributions(payload)

    if missing:
        log.warning("payload에 없는 distribution: %s", ", ".join(missing))

    return payload


def stamp_sent(payload: dict, sent_ts: float | None = None) -> dict:
    """발행 시각만 바꾼 사본"""

    return {
        **payload,
        "sent_ts": int(sent_ts) if sent_ts is not None else int(time.time()),
    }


def missing_distributions(payload: dict) -> list[str]:
    return [
        name
        for name in DISTRIBUTION_KIND
        if name not in payload["distributions"]
    ]


def to_json(payload: dict) -> str:
    """
    NaN / inf 는 JSON 표준이 아니다. 받는 쪽 파서가 거부할 수 있으므로 막는다.
    """

    try:
        return json.dumps(payload, allow_nan=False, ensure_ascii=False)
    except (TypeError, ValueError) as exc:
        raise PayloadError(f"JSON 직렬화 실패: {exc}") from exc


# ==================================================================== 검증


def validate_kde_payload(payload: dict, require_all: bool = False) -> None:
    """
    hestia/model/kde 명세 검증. 실패하면 PayloadError.

    require_all=True 면 4개 distribution이 모두 있어야 한다.
    기본은 일부만 있어도 된다 — 학습 불가 분포는 빈 배열 대신 키를 뺀다.
    """

    if not isinstance(payload, dict):
        raise PayloadError("payload가 객체가 아닙니다.")

    missing = [k for k in PAYLOAD_KEYS if k not in payload]
    extra = [k for k in payload if k not in PAYLOAD_KEYS]

    if missing:
        raise PayloadError(f"필수 필드 누락: {', '.join(missing)}")

    if extra:
        raise PayloadError(f"명세에 없는 필드: {', '.join(extra)}")

    if payload["version"] != VERSION or isinstance(payload["version"], bool):
        raise PayloadError(f"version은 {VERSION}이어야 합니다: {payload['version']!r}")

    if payload["src_id"] != SRC_ID:
        raise PayloadError(f"src_id는 {SRC_ID!r}이어야 합니다: {payload['src_id']!r}")

    for key in ("sent_ts", "trained_at"):
        if not _is_number(payload[key]) or payload[key] <= 0:
            raise PayloadError(f"{key}는 양수 epoch seconds여야 합니다: {payload[key]!r}")

    days = payload["sample_days"]
    if not isinstance(days, int) or isinstance(days, bool) or days < 0:
        raise PayloadError(f"sample_days는 0 이상의 정수여야 합니다: {days!r}")

    distributions = payload["distributions"]
    if not isinstance(distributions, dict) or not distributions:
        raise PayloadError("distributions가 비었거나 객체가 아닙니다.")

    for name, dist in distributions.items():
        validate_distribution(name, dist)

    if require_all and missing_distributions(payload):
        raise PayloadError(
            f"distribution 누락: {', '.join(missing_distributions(payload))}"
        )

    predictability = payload["predictability"]
    if not isinstance(predictability, dict):
        raise PayloadError("predictability가 객체가 아닙니다.")

    if set(predictability) != set(distributions):
        raise PayloadError(
            "predictability와 distributions의 이름이 다릅니다: "
            f"{sorted(predictability)} != {sorted(distributions)}"
        )

    for name, value in predictability.items():
        if not _is_number(value) or not 0 <= value <= 1:
            raise PayloadError(f"{name} predictability는 0~1이어야 합니다: {value!r}")

    to_json(payload)


def validate_distribution(name: str, dist) -> None:
    if name not in DISTRIBUTION_KIND:
        raise PayloadError(f"알 수 없는 distribution: {name!r}")

    if not isinstance(dist, dict) or set(dist) != {"grid_min", "grid_step", "density"}:
        raise PayloadError(f"{name}: grid_min / grid_step / density만 있어야 합니다.")

    grid = grid_for(name)

    if dist["grid_min"] != grid.grid_min or dist["grid_step"] != grid.grid_step:
        raise PayloadError(
            f"{name}: 격자는 grid_min={grid.grid_min}, grid_step={grid.grid_step}이어야 합니다 "
            f"(받음 {dist['grid_min']!r}, {dist['grid_step']!r})"
        )

    density = dist["density"]

    if not isinstance(density, list) or not density:
        raise PayloadError(f"{name}: density가 비었거나 배열이 아닙니다.")

    if not all(_is_number(v) for v in density):
        raise PayloadError(f"{name}: density에 숫자가 아니거나 NaN/inf인 값이 있습니다.")

    if any(v < 0 for v in density):
        raise PayloadError(f"{name}: density에 음수가 있습니다.")

    # 시각 분포는 하루를 덮어야 한다. hydration_lag는 기준점이 기상이라 제외한다.
    if is_time_of_day(name):
        expected = int(MINUTES_PER_DAY // dist["grid_step"])
        if len(density) != expected:
            raise PayloadError(f"{name}: density는 {expected}칸이어야 합니다 (받음 {len(density)}칸)")

    total = math.fsum(density)
    if abs(total - 1.0) > DENSITY_SUM_TOLERANCE:
        raise PayloadError(f"{name}: density 합이 1이 아닙니다 ({total})")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="hestia/model/kde payload 생성")
    add_input_arguments(parser)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)

    payload = build_kde_payload(build_model(samples_from_args(args)))

    print(
        json.dumps(
            payload,
            indent=2,
            ensure_ascii=False,
        )
    )
