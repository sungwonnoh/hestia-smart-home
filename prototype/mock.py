"""터미널을 센서/가전 대신 쓰는 목 발행기.

engine.py 와 별도 터미널에서 실행한다.

  > presence livingroom on
  > tv on
  > washer complete
  > time +30m
  > dump
"""

import json
import sys

import config
from engine import make_client

HELP = """\
명령:
  presence <livingroom|kitchen|bedroom> <on|off>
  <tv|washer|light> <state>        예) tv on / washer complete
  event <device> <EVENT>           예) event washer COMPLETE
  time <+30m|+2h|90s|2026-01-01T07:00>
  dump                             엔진의 World State 출력
  case1                            CASE 1 시퀀스 한 번에
  quit
"""

AREA_SENSOR = {
    "livingroom": "livingroom_presence",
    "kitchen": "kitchen_presence",
    "bedroom": "bedroom_presence",
}


def main():
    client = make_client()
    client.connect(config.MQTT_HOST, config.MQTT_PORT, 60)
    client.loop_start()
    p = config.PREFIX

    def pub(topic, payload):
        client.publish(f"{p}/{topic}", json.dumps(payload, ensure_ascii=False))
        print(f"  -> {p}/{topic} {payload}")

    def run(line: str) -> bool:
        parts = line.split()
        if not parts:
            return True
        cmd = parts[0].lower()

        if cmd in ("quit", "exit"):
            return False
        if cmd in ("help", "?"):
            print(HELP)
        elif cmd == "presence" and len(parts) == 3:
            sid = AREA_SENSOR.get(parts[1].lower())
            if not sid:
                print("  알 수 없는 공간")
            else:
                pub(f"sensor/{sid}/state", {"value": parts[2].lower() == "on"})
        elif cmd == "event" and len(parts) == 3:
            pub(f"device/{parts[1]}/event", {"event": parts[2].upper()})
        elif cmd == "time" and len(parts) == 2:
            arg = parts[1]
            op = "set" if "-" in arg and ":" in arg else "advance"
            pub("dev/clock", {"op": op, "value": arg})
        elif cmd == "dump":
            pub("dev/dump", {})
        elif cmd == "case1":
            for c in ("presence livingroom on", "tv on", "dump", "washer complete"):
                print(f"> {c}")
                run(c)
        elif cmd in config.DEVICES and len(parts) == 2:
            pub(f"device/{cmd}/state", {"state": parts[1].upper()})
        else:
            print("  ? help 입력")
        return True

    print(HELP)
    try:
        while True:
            try:
                line = input("> ")
            except EOFError:
                break
            if not run(line):
                break
    except KeyboardInterrupt:
        pass
    client.loop_stop()


if __name__ == "__main__":
    main()