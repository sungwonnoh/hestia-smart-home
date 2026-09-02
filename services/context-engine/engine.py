"""HESTIA Context Engine.

MQTT로 센서 이벤트를 받아 판단 로직에 넘긴다.
시각은 항상 self.clock을 통해서만 얻는다. time.time() 직접 호출 금지.
"""

import json
import sys

import paho.mqtt.client as mqtt

from clock import Clock, RealClock

BROKER = "127.0.0.1"
PORT = 1883
TOPIC = "hestia/sensor/#"


class ContextEngine:
    def __init__(self, clock: Clock):
        self.clock = clock

    def handle_event(self, topic: str, payload: dict) -> None:
        """센서 이벤트 하나를 처리한다.

        MQTT와 무관한 순수 함수에 가깝게 유지한다.
        리플레이 하네스는 이 메서드를 직접 호출한다.
        """
        now = self.clock.now()

        # 이벤트 발생 시각. 페이로드에 없으면 수신 시각으로 대체한다.
        # 스키마 확정 후 필드명을 맞출 것.
        event_ts = payload.get("ts", now)

        print(f"[{now:.3f}] {topic} ts={event_ts} {payload}", flush=True)

        # TODO: FSM 상태 전이
        # TODO: Baseline KDE 조회
        # TODO: 이상 판정


def make_mqtt_client(engine: ContextEngine) -> mqtt.Client:
    def on_connect(client, userdata, flags, reason_code, properties):
        print(f"connected rc={reason_code}", flush=True)
        client.subscribe(TOPIC)

    def on_message(client, userdata, msg):
        try:
            payload = json.loads(msg.payload.decode())
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            print(f"payload 파싱 실패 {msg.topic}: {e}", flush=True)
            return
        engine.handle_event(msg.topic, payload)

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.on_connect = on_connect
    client.on_message = on_message
    return client


def main() -> int:
    engine = ContextEngine(clock=RealClock())
    client = make_mqtt_client(engine)
    client.connect(BROKER, PORT, keepalive=60)
    client.loop_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())