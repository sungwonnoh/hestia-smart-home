"""
hestia/model/kde 발행 (QoS 1, retained).

retained 이므로 RPi5 가 재시작해도 마지막 모델을 바로 받는다.
"""

from __future__ import annotations

import argparse
import logging
import os
from typing import Callable

from baseline import add_input_arguments, build_model, samples_from_args
from model_payload import (
    TOPIC,
    build_kde_payload,
    stamp_sent,
    to_json,
    validate_kde_payload,
)


log = logging.getLogger(__name__)


MQTT_HOST = os.getenv("MQTT_HOST", "localhost")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))

QOS = 1
RETAIN = True

# PUBACK 을 기다리는 최대 시간 (초)
PUBLISH_TIMEOUT_SEC = 10.0


class PublishError(RuntimeError):
    pass


def default_client_factory():
    import paho.mqtt.client as mqtt

    # Context Engine(runner.py)과 같은 콜백 API. VERSION1 은 paho 2.x 에서 deprecated.
    return mqtt.Client(
        mqtt.CallbackAPIVersion.VERSION2,
        client_id=f"hestia-learning-{os.getpid()}",
    )


def publish_kde_model(
    payload: dict,
    host: str = MQTT_HOST,
    port: int = MQTT_PORT,
    client_factory: Callable = default_client_factory,
    timeout: float = PUBLISH_TIMEOUT_SEC,
) -> dict:
    """
    payload 를 검증하고 sent_ts 를 찍어 발행한다. 실제로 보낸 payload 를 돌려준다.

    QoS 1 은 broker 의 PUBACK 을 받아야 완료된다.
    network loop 가 돌아야 PUBACK 을 처리하므로 loop_start 후 기다린다.
    """

    payload = stamp_sent(payload)
    validate_kde_payload(payload)
    body = to_json(payload)

    client = client_factory()
    client.connect(host, port, 60)
    client.loop_start()

    try:
        info = client.publish(TOPIC, body, qos=QOS, retain=RETAIN)

        if info.rc != 0:
            raise PublishError(f"publish 실패 rc={info.rc}")

        info.wait_for_publish(timeout=timeout)

        if not info.is_published():
            raise PublishError(f"{timeout}초 안에 PUBACK을 받지 못했습니다.")
    finally:
        client.loop_stop()
        client.disconnect()

    return payload


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="hestia/model/kde 발행")
    add_input_arguments(parser)
    parser.add_argument("--host", default=MQTT_HOST)
    parser.add_argument("--port", type=int, default=MQTT_PORT)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)

    sent = publish_kde_model(
        build_kde_payload(build_model(samples_from_args(args))),
        host=args.host,
        port=args.port,
    )

    print(f"[PUBLISHED] topic={TOPIC} qos={QOS} retain={RETAIN}")
    print(f"[MODEL] sample_days={sent['sample_days']}")

    for name, value in sent["predictability"].items():
        print(f"[MODEL] {name:<14} predictability={value:.3f}")
