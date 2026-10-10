"""
hestia/model/kde 수신 확인 (E2E).

retained 이므로 구독하자마자 broker 가 마지막 모델을 준다.
RPi4 발행 직후, 또는 RPi5 재시작 후 retained 복구 확인에 쓴다.

    python3 services/learning-engine/verify_model.py --host <broker>

종료 코드: 0 통과 / 1 검증 실패 / 2 수신 없음
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
from typing import Callable

from model_payload import TOPIC, PayloadError, missing_distributions, validate_kde_payload
from mqtt_publisher import MQTT_HOST, MQTT_PORT, default_client_factory


# SLEEP.md §10 — sleep / wake 가 반드시 있어야 한다
DEFAULT_REQUIRED = ("sleep_time", "wake_time")


def check_payload(raw: bytes | str, required=DEFAULT_REQUIRED) -> dict:
    """
    받은 payload 를 명세대로 검증하고 요약을 돌려준다. 실패하면 PayloadError.
    """

    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise PayloadError(f"JSON이 아닙니다: {exc}") from exc

    validate_kde_payload(payload)

    absent = [name for name in required if name not in payload["distributions"]]

    if absent:
        raise PayloadError(f"필요한 distribution 없음: {', '.join(absent)}")

    return {
        "trained_at": payload["trained_at"],
        "sent_ts": payload["sent_ts"],
        "sample_days": payload["sample_days"],
        "missing": missing_distributions(payload),
        "distributions": {
            name: {
                "bins": len(d["density"]),
                "grid_step": d["grid_step"],
                "sum": sum(d["density"]),
                "predictability": payload["predictability"][name],
                "peaks": d.get("peaks"),
            }
            for name, d in payload["distributions"].items()
        },
    }


def receive_retained(
    host: str = MQTT_HOST,
    port: int = MQTT_PORT,
    timeout: float = 10.0,
    client_factory: Callable = default_client_factory,
):
    """
    TOPIC 을 구독해 첫 메시지를 기다린다. 없으면 None.
    (payload bytes, retained 여부) 를 돌려준다.
    """

    got = threading.Event()
    box: dict = {}

    def on_connect(client, userdata, flags, reason_code, properties=None):
        client.subscribe(TOPIC, qos=1)

    def on_message(client, userdata, msg):
        if "payload" not in box:
            box["payload"] = msg.payload
            box["retain"] = bool(msg.retain)
            got.set()

    client = client_factory()
    client.on_connect = on_connect
    client.on_message = on_message
    client.connect(host, port, 60)
    client.loop_start()

    try:
        got.wait(timeout)
    finally:
        client.loop_stop()
        client.disconnect()

    if "payload" not in box:
        return None

    return box["payload"], box["retain"]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="hestia/model/kde 수신 확인")
    parser.add_argument("--host", default=MQTT_HOST)
    parser.add_argument("--port", type=int, default=MQTT_PORT)
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument(
        "--require",
        nargs="*",
        default=list(DEFAULT_REQUIRED),
        help="반드시 있어야 하는 distribution (기본: sleep_time wake_time)",
    )
    args = parser.parse_args(argv)

    received = receive_retained(args.host, args.port, args.timeout)

    if received is None:
        print(f"[FAIL] {args.timeout}초 안에 {TOPIC} 메시지를 받지 못했습니다 (retained 없음?)")
        return 2

    raw, retained = received

    try:
        summary = check_payload(raw, args.require)
    except PayloadError as exc:
        print(f"[FAIL] {exc}")
        return 1

    print(f"[OK] {TOPIC} retained={retained} trained_at={summary['trained_at']} "
          f"sample_days={summary['sample_days']}")

    for name, d in summary["distributions"].items():
        print(f"  {name:<14} {d['bins']:>3}칸 step={d['grid_step']} "
              f"sum={d['sum']:.6f} predictability={d['predictability']:.3f}")

        for p in d["peaks"] or []:
            pred = "null" if p["predictability"] is None else f"{p['predictability']:.3f}"
            print(f"    끼니 center={p['center']} [{p['from']}, {p['to']}) predictability={pred}")

    if summary["missing"]:
        print(f"  없음: {', '.join(summary['missing'])}")

    if not retained:
        print("[WARN] retained 가 아닌 실시간 메시지입니다 — retained 복구 확인이 아닙니다.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
