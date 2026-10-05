"""Mock MQTT 시나리오 발행 — 로컬/RPi5 E2E 확인용.

실제 Context Engine 이 같은 브로커를 쓰고 있을 수 있으므로 안전한 쪽을 기본으로 한다.

  --mode devices (기본)  가전 상태만 발행한다. (ESP32 가전 Mock 역할)
  --mode full            가전 상태 + Context Engine 출력(context, 복약 알림)까지 흉내 낸다.
                         실제 엔진과 섞이므로 hestia-engine 을 멈추고 쓴다.

  - 기본은 retain 하지 않는다. 브로커에 메시지가 남지 않는다.
  - hestia/registry/devices 는 엔진의 역할 매핑을 덮어쓰므로 발행하지 않는다.
  - 엔진 출력을 흉내 낼 때 src_id 는 "mock-engine" 으로 구분한다.
  - --retain 으로 남긴 메시지는 --clear 로 지운다 (같은 --mode 로).

사용:
  python scripts/mock_mqtt.py                    # 가전 상태만
  python scripts/mock_mqtt.py --mode full        # 엔진 출력까지 (hestia-engine 정지 후)
  python scripts/mock_mqtt.py --mode full --clear
  MQTT_HOST=localhost MQTT_PORT=1883 python scripts/mock_mqtt.py --interval 2
"""

from __future__ import annotations

import argparse
import json
import os
import time
from typing import Any, Dict, Iterator, List, Tuple

import paho.mqtt.client as mqtt

MOCK_ENGINE_SRC = "mock-engine"

# 결과를 언제 확인해도 남아 있는 편이 나은 '상태' 토픽만 retain 대상이다.
# 알림(notify/push)은 이벤트라서 --retain 이어도 남기지 않는다.
Message = Tuple[str, Dict[str, Any], bool]

_seq = 0


def envelope(src_id: str, **fields: Any) -> Dict[str, Any]:
    global _seq
    _seq += 1
    return {"version": 1, "sent_ts": int(time.time()), "src_id": src_id, "seq": _seq, **fields}


def device(vid: str, device_type: str, **fields: Any) -> Message:
    return (f"hestia/device/{vid}/state",
            envelope(vid, device_type=device_type, source="mock", **fields), True)


def context(name: str, **fields: Any) -> Message:
    return f"hestia/context/{name}", envelope(MOCK_ENGINE_SRC, **fields), True


def device_messages() -> List[Message]:
    # virtual_id 와 device_type 은 config/homes/demo.toml 과 같다.
    return [
        device("vd-01", "smart_tv", power="ON", volume=12),
        device("vd-02", "smart_light", power="ON", brightness=70),
        device("vd-03", "air_conditioner", power="ON", mode="COOL", temp_set=24, temp_room=26.5),
        device("vd-05", "smart_fridge", door="CLOSED"),
        device("vd-08", "washer", power="ON", cycle="SPIN", remain_min=8),
    ]


def engine_messages() -> List[Message]:
    now = int(time.time())
    meal = {"area": "kitchen", "presence": True}
    return [
        context("away", state="HOME", confidence=0.95),
        context("occupancy", state="SINGLE"),
        context("presence", area="kitchen", since=now,
                areas={"living": False, "kitchen": True}, confidence=0.9),
        context("activity", state="MEAL_PREP", since=now, confidence=0.72, factors=meal),
        context("activity", state="EATING", since=now, confidence=0.84, factors=meal),
        context("activity", state="MEAL_DONE", since=now, confidence=0.87, factors=meal),
        ("hestia/notify/push", envelope(
            MOCK_ENGINE_SRC,
            notify_id=f"n-mock-{now}",
            scenario="MEDICATION_PROMPT",
            priority="normal",
            # 발송 대상 virtual_id. 공간은 Backend 가 registry 로 찾는다.
            channels=["vd-05", "voice"],
            requires_ack=True,
            ack_deadline=now + 600,
            escalation_level=1,
            payload={
                "title": "식사 후 복약 시간입니다.",
                "text": "HESTIA가 식사 완료를 감지했습니다.\n복약 시간을 확인해주세요.",
            },
        ), False),
    ]


def scenario(mode: str) -> Iterator[Message]:
    yield from device_messages()
    if mode == "full":
        yield from engine_messages()
    # 세탁 완료는 마지막에 와야 상태 변화가 보인다.
    yield device("vd-08", "washer", power="ON", cycle="DONE", remain_min=0)


def retained_topics(mode: str) -> List[str]:
    topics = [topic for topic, _, retainable in scenario(mode) if retainable]
    return sorted(set(topics))


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--mode", choices=("devices", "full"), default="devices")
    parser.add_argument("--retain", action="store_true",
                        help="상태 토픽을 retain 으로 발행 (기본: retain 안 함)")
    parser.add_argument("--clear", action="store_true",
                        help="이 모드가 retain 했을 수 있는 토픽을 지우고 종료")
    parser.add_argument("--interval", type=float, default=1.0, help="메시지 사이 간격(초)")
    args = parser.parse_args()

    host = os.getenv("MQTT_HOST", "localhost")
    port = int(os.getenv("MQTT_PORT", "1883"))
    client = mqtt.Client(callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
                         client_id=f"hestia-mock-{os.getpid()}")
    client.connect(host, port, keepalive=30)
    client.loop_start()
    try:
        if args.clear:
            for topic in retained_topics(args.mode):
                # 빈 payload + retain 은 브로커에 남은 retained 메시지를 지운다.
                client.publish(topic, payload=None, qos=1, retain=True).wait_for_publish()
                print(f"[CLEAR] {topic}")
            return

        if args.mode == "full":
            print("[주의] Context Engine 출력을 흉내 냅니다. hestia-engine 이 멈춰 있는지 확인하세요.")
        for topic, payload, retainable in scenario(args.mode):
            retain = args.retain and retainable
            client.publish(topic, json.dumps(payload, ensure_ascii=False),
                           qos=1, retain=retain).wait_for_publish()
            label = payload.get("state") or payload.get("title") or payload.get("power") or ""
            print(f"[PUB{' R' if retain else ''}] {topic}  {label}")
            time.sleep(args.interval)
    finally:
        client.loop_stop()
        client.disconnect()


if __name__ == "__main__":
    main()
