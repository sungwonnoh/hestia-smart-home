"""Mock MQTT 시나리오 발행 — 로컬 E2E 확인용.

Context Engine/ESP32 없이 Mosquitto 에 데모 메시지를 순서대로 발행한다.
  registry → 가전 상태 → 식사 준비/식사/식사 완료 context → 복약 알림

사용:
  MQTT_HOST=localhost MQTT_PORT=1883 python scripts/mock_mqtt.py [--interval 2]
"""

from __future__ import annotations

import argparse
import json
import os
import time

import paho.mqtt.client as mqtt

seq = 0


def envelope(src_id: str, **fields):
    global seq
    seq += 1
    return {"version": 1, "sent_ts": int(time.time()), "src_id": src_id, "seq": seq, **fields}


def scenario():
    now = int(time.time())
    yield "hestia/registry/devices", envelope("rpi4", devices=[
        {"virtual_id": "vd-01", "device_type": "smart_tv", "area": "living", "channel": True},
        {"virtual_id": "vd-02", "device_type": "smart_light", "area": "living", "channel": True},
        {"virtual_id": "vd-03", "device_type": "air_conditioner", "area": "living"},
        {"virtual_id": "vd-05", "device_type": "smart_fridge", "area": "kitchen", "channel": True},
        {"virtual_id": "vd-08", "device_type": "washer", "area": "utility"},
    ]), True
    yield "hestia/device/vd-01/state", envelope("vd-01", device_type="smart_tv", source="mock", power="ON", volume=12), True
    yield "hestia/device/vd-02/state", envelope("vd-02", device_type="smart_light", source="mock", power="ON", brightness=70), True
    yield "hestia/device/vd-03/state", envelope("vd-03", device_type="air_conditioner", source="mock", power="ON", mode="COOL", temp_set=24, temp_room=26.5), True
    yield "hestia/device/vd-05/state", envelope("vd-05", device_type="smart_fridge", source="mock", door="CLOSED"), True
    yield "hestia/device/vd-08/state", envelope("vd-08", device_type="washer", source="mock", power="ON", cycle="SPIN", remain_min=8), True
    yield "hestia/context/away", envelope("rpi5", state="HOME", confidence=0.95), True
    yield "hestia/context/occupancy", envelope("rpi5", state="SINGLE"), True
    yield "hestia/context/presence", envelope("rpi5", area="kitchen", since=now,
                                              areas={"living": False, "kitchen": True}, confidence=0.9), True
    yield "hestia/context/activity", envelope("rpi5", state="MEAL_PREP", since=now, confidence=0.72,
                                              factors={"area": "kitchen", "presence": True}), True
    yield "hestia/context/activity", envelope("rpi5", state="EATING", since=now + 600, confidence=0.84,
                                              factors={"area": "kitchen", "presence": True}), True
    yield "hestia/context/activity", envelope("rpi5", state="MEAL_DONE", since=now + 1800, confidence=0.87,
                                              factors={"area": "kitchen", "presence": True}), True
    yield "hestia/notify/push", envelope("rpi5", notify_id=f"n-demo-{now}", scenario="MEDICATION",
                                         type="REMINDER", priority="normal", title="식사 후 복약 시간입니다.",
                                         message="HESTIA가 식사 완료를 감지했습니다.\n복약 시간을 확인해주세요.",
                                         area="living", context="activity"), False
    yield "hestia/device/vd-08/state", envelope("vd-08", device_type="washer", source="mock", power="ON", cycle="DONE", remain_min=0), True


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--interval", type=float, default=1.0, help="메시지 사이 간격(초)")
    args = parser.parse_args()

    host = os.getenv("MQTT_HOST", "localhost")
    port = int(os.getenv("MQTT_PORT", "1883"))
    client = mqtt.Client(callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
                         client_id="hestia-mock-publisher")
    client.connect(host, port, keepalive=30)
    client.loop_start()
    try:
        for topic, payload, retain in scenario():
            client.publish(topic, json.dumps(payload, ensure_ascii=False), qos=1, retain=retain).wait_for_publish()
            print(f"[PUB] {topic}  {payload.get('state') or payload.get('title') or ''}")
            time.sleep(args.interval)
    finally:
        client.loop_stop()
        client.disconnect()


if __name__ == "__main__":
    main()
