"""Context Engine.

MQTT 수신 -> World State 갱신 -> Context 판단 -> Policy -> MQTT 발행.

핵심 구조: Engine.ingest()가 MQTT를 전혀 모른다.
브로커 없이도 selftest.py가 같은 경로를 그대로 태울 수 있다.
"""

import argparse
import json
import sys

import config
import context as ctxmod
from clock import RealClock, ReplayClock, parse_duration
from policy import Policy
from world import WorldState


class Engine:
    def __init__(self, clock, publish=None):
        self.clock = clock
        self.world = WorldState(clock)
        self.policy = Policy(clock)
        self.context = ctxmod.UNKNOWN
        self._publish = publish or (lambda topic, payload: None)

    # ---------- 입력 한 건을 처리하는 유일한 경로 ----------

    def ingest(self, kind: str, ident: str, payload: dict) -> None:
        if kind == "sensor":
            events = self.world.apply_sensor(ident, payload)
        elif kind == "device_state":
            events = self.world.apply_device_state(ident, payload)
        elif kind == "device_event":
            events = self.world.apply_device_event(ident, payload)
        else:
            return

        # 1) 월드가 바뀌었으니 컨텍스트를 다시 판단한다
        new_context = ctxmod.judge(self.world)
        if new_context != self.context:
            self.context = new_context
            self._log(f"[CONTEXT] {self.context}  (변경됨)")
            self._publish("context/user", {"context": self.context,
                                           "ts": self.clock.now().isoformat()})

        # 2) 이번에 발생한 이벤트를 새 컨텍스트 기준으로 처리한다
        for ev in events:
            self._handle(ev)

    def _handle(self, ev) -> None:
        action = self.policy.decide(ev, self.context)
        if action is None:
            self._log(f"[CONTEXT] {self.context} [EVENT] {ev.name} "
                      f"[POLICY] NO_RULE")
            return
        if action.topic:
            self._publish(action.topic, action.payload)
        self._log(f"[CONTEXT] {self.context} [EVENT] {ev.name} "
                  f"[POLICY] {action.policy} [ACTION] {action.label}")

    # ---------- 개발용 시계 조작 ----------

    def clock_control(self, payload: dict) -> None:
        if not isinstance(self.clock, ReplayClock):
            self._log("[CLOCK] RealClock 이라 조작할 수 없음 (--replay 로 실행)")
            return
        op = payload.get("op")
        # 목 발행기에서 오타가 나도 엔진이 죽으면 안 된다
        try:
            if op == "advance":
                t = self.clock.advance(parse_duration(str(payload.get("value", "0"))))
            elif op == "set":
                t = self.clock.set(str(payload["value"]))
            else:
                return
        except (ValueError, KeyError) as e:
            self._log(f"[CLOCK] 잘못된 입력 {payload.get('value')!r}: {e}")
            self._log("[CLOCK] 형식: +30m / 2h / 90s / 3600 / 2026-01-01T07:00")
            return
        self._log(f"[CLOCK] now = {t.isoformat(timespec='seconds')}")

    def dump_state(self) -> None:
        self._log("[STATE] " + f"user.context = {self.context}")
        for line in self.world.dump():
            self._log("[STATE] " + line)

    def _log(self, line: str) -> None:
        ts = self.clock.now().strftime("%H:%M:%S")
        print(f"{ts} {line}", flush=True)


# ---------------- MQTT 배선 ----------------

def make_client():
    import paho.mqtt.client as mqtt
    try:
        return mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    except AttributeError:  # paho-mqtt 1.x
        return mqtt.Client()


def run(replay: bool) -> None:
    clock = ReplayClock() if replay else RealClock()
    client = make_client()

    def publish(topic, payload):
        full = f"{config.PREFIX}/{topic}"
        client.publish(full, json.dumps(payload, ensure_ascii=False))

    engine = Engine(clock, publish)
    p = config.PREFIX

    def on_connect(c, userdata, flags, rc, properties=None):
        for t in (f"{p}/sensor/+/state",
                  f"{p}/device/+/state",
                  f"{p}/device/+/event",
                  f"{p}/dev/clock",
                  f"{p}/dev/dump"):
            c.subscribe(t)
        print(f"[BOOT] connected, clock={clock.kind}, prefix={p}", flush=True)

    def on_message(c, userdata, msg):
        try:
            payload = json.loads(msg.payload.decode() or "{}")
        except json.JSONDecodeError:
            print(f"[WARN] JSON 아님: {msg.topic} {msg.payload!r}", flush=True)
            return
        parts = msg.topic.split("/")
        print(f"[RX] {msg.topic} {payload}", flush=True)
        if parts[1] == "sensor" and parts[3] == "state":
            engine.ingest("sensor", parts[2], payload)
        elif parts[1] == "device" and parts[3] == "state":
            engine.ingest("device_state", parts[2], payload)
        elif parts[1] == "device" and parts[3] == "event":
            engine.ingest("device_event", parts[2], payload)
        elif parts[1] == "dev" and parts[2] == "clock":
            engine.clock_control(payload)
        elif parts[1] == "dev" and parts[2] == "dump":
            engine.dump_state()

    client.on_connect = on_connect
    client.on_message = on_message
    client.connect(config.MQTT_HOST, config.MQTT_PORT, 60)
    client.loop_forever()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--replay", action="store_true",
                    help="ReplayClock 사용 (시간 이동 가능)")
    args = ap.parse_args()
    try:
        run(args.replay)
    except KeyboardInterrupt:
        sys.exit(0)