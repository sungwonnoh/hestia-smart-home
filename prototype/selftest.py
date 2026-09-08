"""브로커 없이 CASE 1 시퀀스를 그대로 태워보는 테스트.

engine.ingest()가 MQTT를 모르기 때문에 가능하다.
RPi에 붙기 전에 노트북에서 로직만 먼저 확인할 때 쓴다.
"""

from clock import ReplayClock
from engine import Engine


def main():
    published = []
    clock = ReplayClock()
    engine = Engine(clock, lambda t, p: published.append((t, p)))

    print("--- CASE 1: 거실에서 TV 보는 중 세탁 완료 ---")
    engine.ingest("sensor", "livingroom_presence", {"value": True})
    engine.ingest("device_state", "tv", {"state": "ON"})
    engine.dump_state()
    engine.ingest("device_state", "washer", {"state": "COMPLETE"})

    print("\n--- 쿨다운 확인: 같은 이벤트 즉시 재발생 ---")
    engine.ingest("device_state", "washer", {"state": "RUNNING"})
    engine.ingest("device_state", "washer", {"state": "COMPLETE"})

    print("\n--- 시간 이동 후 재발생 ---")
    clock.advance(3600)
    engine.ingest("device_state", "washer", {"state": "RUNNING"})
    engine.ingest("device_state", "washer", {"state": "COMPLETE"})

    print("\n--- 외출 중 세탁 완료 ---")
    clock.advance(3600)
    engine.ingest("device_state", "tv", {"state": "OFF"})
    engine.ingest("sensor", "livingroom_presence", {"value": False})
    engine.ingest("device_state", "washer", {"state": "RUNNING"})
    engine.ingest("device_state", "washer", {"state": "COMPLETE"})

    print("\n발행된 메시지:")
    for t, pl in published:
        print(f"  {t}  {pl}")


if __name__ == "__main__":
    main()