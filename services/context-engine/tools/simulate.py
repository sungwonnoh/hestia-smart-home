"""가전·센서 시뮬레이터.

엔진을 실기기 없이 돌려보기 위한 도구다. 패키지 밖에 두는 것은
엔진의 일부가 아니라 엔진을 테스트하는 물건이기 때문이다.

    cmd 수신 → 내부 상태 변경 → state 재발행 → 엔진이 결과를 본다

양방향인 것이 핵심이다. mosquitto_pub 으로는 state 를 쏠 수 있지만
cmd 를 받을 수 없어서, 제어가 먹혔는지 확인할 방법이 없다.

점진 변화는 중간값을 보내지 않는다 — transition_ms 만큼 기다렸다가
최종값 하나만 발행한다. 실제 기기도 대개 그렇고, 중간값이 오면
엔진의 되돌림 판정이 그것을 사용자 조작으로 오인한다.

실행:
    python tools/simulate.py
    python tools/simulate.py --host 192.168.0.14
"""

from __future__ import annotations

import argparse
import json
import os
import queue
import sys
import threading
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import paho.mqtt.client as mqtt

from hestia_engine.config import Config, ConfigError, load
from hestia_engine.messages import DeviceCommand, parse

SRC_VERSION = 1

# 가전마다 기본 상태가 다르다. 냉장고는 power 가 없고 문이 닫혀
# 있는 것이 기본이다 (명세).
INITIAL = {
    "smart_fridge": {"door": "CLOSED"},
    "washer": {"power": "OFF", "cycle": "DONE"},
    "robot_cleaner": {"status": "DOCKED", "battery": 100},
    "display_node": {"power": "ON", "display": ""},
}
DEFAULT_INITIAL = {"power": "OFF"}

# action 별로 바뀌는 필드 (명세). set 은 params 를 그대로 병합한다.
ACTION_EFFECT = {
    "start": {"status": "CLEANING"},
    "stop": {"status": "PAUSED"},
    "dock": {"status": "DOCKED"},
}

TRANSITION_KEY = "transition_ms"


class Simulator:
    """가전 상태를 들고 cmd 에 반응한다. 센서는 손으로 쏜다."""

    def __init__(self, config: Config, host: str, port: int) -> None:
        self._config = config
        self._host = host
        self._port = port
        self._running = False

        self.devices: dict[str, dict[str, Any]] = {
            d.id: dict(INITIAL.get(d.device_type, DEFAULT_INITIAL))
            for d in config.all_devices()
            if d.enabled
        }
        self._seq: dict[str, int] = {}
        # (적용할 시각, vid, params) — transition_ms 를 블로킹 없이 기다린다
        self._pending: list[tuple[float, str, dict[str, Any]]] = []
        self._input: queue.Queue[str] = queue.Queue()

        self._client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2, client_id=f"hestia-sim-{os.getpid()}"
        )
        self._client.on_connect = self._on_connect
        self._client.on_message = self._on_message

    # ------------------------------------------------------------ 생애

    def run(self) -> None:
        """MQTT 루프와 입력 큐를 번갈아 돈다.

        입력은 별도 스레드가 읽어 큐에 넣는다 — 상태는 메인 루프에서만
        만지므로 락이 필요 없다.
        """
        self._running = True
        threading.Thread(target=self._read_input, daemon=True).start()

        try:
            self._client.connect(self._host, self._port, keepalive=60)
        except OSError as exc:
            print(f"브로커 연결 실패: {exc}")
            return

        print(f"브로커 {self._host}:{self._port} — help 로 명령 목록")
        while self._running:
            self._client.loop(timeout=0.1)
            self._apply_due()
            self._drain_input()

        self._client.disconnect()

    def stop(self) -> None:
        self._running = False

    # ------------------------------------------------------------ MQTT

    def _on_connect(self, client, userdata, flags, reason_code, properties) -> None:
        if reason_code != 0:
            print(f"연결 거부 rc={reason_code}")
            return
        client.subscribe("hestia/device/+/cmd", qos=1)

        # 브로커가 껐다 켜지면 retained 가 사라진다. 다시 쏴야
        # 엔진이 현재 상태를 복원한다.
        for vid in self.devices:
            self._publish_state(vid)
        print(f"연결됨 — 가전 {len(self.devices)}대 상태 발행")

    def _on_message(self, client, userdata, msg) -> None:
        try:
            cmd = parse(msg.topic, msg.payload, time.time())
        except Exception as exc:                      # noqa: BLE001
            print(f"파싱 실패: {exc}")
            return
        if not isinstance(cmd, DeviceCommand):
            return
        self._handle_cmd(cmd)

    def _handle_cmd(self, cmd: DeviceCommand) -> None:
        if cmd.target not in self.devices:
            print(f"모르는 기기: {cmd.target}")
            return

        if cmd.action == "set":
            params = {k: v for k, v in cmd.params.items() if k != TRANSITION_KEY}
            delay_ms = cmd.params.get(TRANSITION_KEY, 0)
        else:
            params = dict(ACTION_EFFECT.get(cmd.action, {}))
            delay_ms = 0

        if not params:
            return

        label = " ".join(f"{k}={v}" for k, v in params.items())
        if delay_ms:
            # 전환 중에는 아무것도 보내지 않는다. 노드가 보간하고
            # 끝난 뒤 최종값만 보고하는 것이 실제 기기의 동작이다.
            print(f"  cmd {cmd.target} {cmd.action} {label} ({delay_ms}ms 전환)")
            self._pending.append((time.time() + delay_ms / 1000.0, cmd.target, params))
        else:
            print(f"  cmd {cmd.target} {cmd.action} {label}")
            self._set(cmd.target, **params)

    def _apply_due(self) -> None:
        if not self._pending:
            return
        now = time.time()
        due = [p for p in self._pending if p[0] <= now]
        self._pending = [p for p in self._pending if p[0] > now]
        for _, vid, params in due:
            print(f"  전환 완료 {vid} {params}")
            self._set(vid, **params)

    # ------------------------------------------------------------ 발행

    def _set(self, vid: str, **changes: Any) -> None:
        """내부 상태를 바꾸고 state 를 발행한다."""
        self.devices.setdefault(vid, {}).update(changes)
        self._publish_state(vid)

    def _publish_state(self, vid: str) -> None:
        d = self._config.device(vid)
        if d is None:
            return
        payload = {
            "version": SRC_VERSION,
            "sent_ts": int(time.time()),
            "src_id": vid,
            "seq": self._next_seq(vid),
            "device_type": d.device_type,
            "source": d.source,
            **self.devices[vid],
        }
        # state 는 retained — 엔진이 재시작해도 현재 상태를 안다 (명세)
        self._client.publish(
            f"hestia/device/{vid}/state", json.dumps(payload, ensure_ascii=False),
            qos=1, retain=True,
        )

    def _publish_event(self, vid: str, event_type: str, **fields: Any) -> None:
        d = self._config.device(vid)
        if d is None:
            print(f"모르는 기기: {vid}")
            return
        payload = {
            "version": SRC_VERSION,
            "sent_ts": int(time.time()),
            "src_id": vid,
            "seq": self._next_seq(vid),
            "device_type": d.device_type,
            "source": d.source,
            "event_type": event_type,
            **fields,
        }
        # 이벤트는 retained 가 아니다 — 순간의 사건이라 보관하면
        # 재연결 때 과거 급수가 방금 일어난 것처럼 보인다 (명세)
        self._client.publish(
            f"hestia/device/{vid}/event", json.dumps(payload, ensure_ascii=False),
            qos=1, retain=False,
        )

    def _publish_sensor(self, vid: str, **fields: Any) -> None:
        s = self._config.sensor(vid)
        if s is None:
            print(f"모르는 센서: {vid}")
            return
        payload = {
            "version": SRC_VERSION,
            "sent_ts": int(time.time()),
            "src_id": vid,
            "seq": self._next_seq(vid),
            "type": s.type,
            **fields,
        }
        retain = s.type != "motion"      # motion 은 순간값이라 보관 안 함 (명세)
        self._client.publish(
            f"hestia/sensor/{vid}/state", json.dumps(payload, ensure_ascii=False),
            qos=1, retain=retain,
        )

    def _next_seq(self, vid: str) -> int:
        self._seq[vid] = self._seq.get(vid, 0) + 1
        return self._seq[vid]

    # ------------------------------------------------------------ 찾기

    def _first_of_type(self, device_type: str) -> str | None:
        for d in self._config.all_devices():
            if d.device_type == device_type and d.enabled:
                return d.id
        return None

    def _sensor_in(self, area: str, stype: str) -> str | None:
        """그 구역의 그 종류 센서. ID 에 묶이지 않으려고 종류로 찾는다."""
        for vid in self._config.sensors_in(area):
            s = self._config.sensor(vid)
            if s is not None and s.type == stype:
                return vid
        return None

    def _sensor_of_type(self, stype: str) -> str | None:
        for s in self._config.all_sensors():
            if s.type == stype and s.enabled:
                return s.id
        return None

    # ------------------------------------------------------------ 입력

    def _read_input(self) -> None:
        """별도 스레드. 상태를 만지지 않고 큐에만 넣는다."""
        for line in sys.stdin:
            self._input.put(line.strip())

    def _drain_input(self) -> None:
        while True:
            try:
                line = self._input.get_nowait()
            except queue.Empty:
                return
            if line:
                self._command(line)

    def _command(self, line: str) -> None:
        parts = line.split()
        verb, args = parts[0].lower(), parts[1:]

        match verb:
            case "quit" | "exit" | "q":
                self.stop()
            case "help" | "?":
                print(HELP)
            case "state":
                self._show()
            case "tv":
                self._device_power("smart_tv", args)
            case "light":
                self._light(args)
            case "fridge":
                self._fridge(args)
            case "washer":
                self._washer(args)
            case "clean":
                self._clean(args)
            case "dispense":
                vid = self._first_of_type("water_purifier")
                if vid:
                    self._publish_event(
                        vid, "dispensed", water_type="AMBIENT", amount_ml=200
                    )
                    print(f"  {vid} dispensed")
            case "remote":
                vid = self._first_of_type("smart_tv")
                if vid:
                    self._publish_event(vid, "remote_input", button="OK")
                    print(f"  {vid} remote_input")
            case "presence":
                self._presence(args, energy=45)
            case "still":
                self._presence(args, energy=3)
            case "bed":
                self._bed(args)
            case "door":
                self._door(args)
            case "power":
                self._power(args)
            case _:
                print(f"모르는 명령: {verb} (help)")

    # ------------------------------------------------------------ 가전 명령

    def _device_power(self, device_type: str, args: list[str]) -> None:
        vid = self._first_of_type(device_type)
        if vid is None:
            print(f"{device_type} 가 설정에 없음")
            return
        on = not args or args[0] != "off"
        self._set(vid, power="ON" if on else "OFF")
        print(f"  {vid} power={'ON' if on else 'OFF'}")

    def _light(self, args: list[str]) -> None:
        vid = self._first_of_type("smart_light")
        if vid is None:
            print("smart_light 가 설정에 없음")
            return
        if not args or args[0] == "off":
            self._set(vid, power="OFF")
            print(f"  {vid} power=OFF")
            return
        if args[0] == "on":
            self._set(vid, power="ON", brightness=100)
            print(f"  {vid} power=ON brightness=100")
            return
        try:
            level = int(args[0])
        except ValueError:
            print("사용법: light <0-100|on|off>")
            return
        self._set(vid, power="ON" if level else "OFF", brightness=level)
        print(f"  {vid} brightness={level}")

    def _fridge(self, args: list[str]) -> None:
        vid = self._first_of_type("smart_fridge")
        if vid is None:
            return
        opened = bool(args) and args[0] == "open"
        self._set(vid, door="OPEN" if opened else "CLOSED")
        if opened:
            self._publish_event(vid, "door_opened")
        else:
            self._publish_event(vid, "door_closed", open_duration_sec=12.0)
        print(f"  {vid} door={'OPEN' if opened else 'CLOSED'}")

    def _washer(self, args: list[str]) -> None:
        vid = self._first_of_type("washer")
        if vid is None:
            return
        cycle = args[0].upper() if args else "WASH"
        self._set(vid, power="ON", cycle=cycle, remain_min=30)
        print(f"  {vid} cycle={cycle}")

    def _clean(self, args: list[str]) -> None:
        vid = self._first_of_type("robot_cleaner")
        if vid is None:
            print("robot_cleaner 가 설정에 없음")
            return
        what = args[0] if args else "start"
        status = {"start": "CLEANING", "stop": "PAUSED", "dock": "DOCKED"}.get(what)
        if status is None:
            print("사용법: clean <start|stop|dock>")
            return
        self._set(vid, status=status)
        print(f"  {vid} status={status}")

    # ------------------------------------------------------------ 센서 명령

    def _presence(self, args: list[str], *, energy: int) -> None:
        if not args:
            print("사용법: presence <area> [off]")
            return
        area = args[0]
        vid = self._sensor_in(area, "presence")
        if vid is None:
            print(f"{area} 에 presence 센서가 없음")
            return
        present = len(args) < 2 or args[1] != "off"
        self._publish_sensor(
            vid,
            present=present,
            confidence="high",
            energy=energy if present else 0,
            distance_cm=150 if present else 0,
        )
        print(f"  {vid} present={present} energy={energy if present else 0}")

    def _bed(self, args: list[str]) -> None:
        vid = self._sensor_of_type("bed")
        if vid is None:
            print("bed 센서가 없음")
            return
        occupied = not args or args[0] != "off"
        self._publish_sensor(vid, occupied=occupied)
        print(f"  {vid} occupied={occupied}")

    def _door(self, args: list[str]) -> None:
        vid = self._sensor_of_type("door")
        if vid is None:
            print("door 센서가 없음")
            return
        opened = bool(args) and args[0] == "open"
        self._publish_sensor(vid, open=opened)
        print(f"  {vid} open={opened}")

    def _power(self, args: list[str]) -> None:
        if len(args) < 2:
            print("사용법: power <vid> <watt>")
            return
        vid = args[0]
        try:
            watt = float(args[1])
        except ValueError:
            print("watt 가 숫자가 아님")
            return
        # state 를 채워 보낸다 — 노드가 채우면 엔진이 그대로 쓴다 (명세).
        # 히스테리시스를 거치려면 state 를 빼면 된다.
        state = "ON" if watt >= 300 else ("STANDBY" if watt > 10 else "OFF")
        self._publish_sensor(vid, watt=watt, state=state)
        print(f"  {vid} watt={watt} state={state}")

    # ------------------------------------------------------------ 출력

    def _show(self) -> None:
        for vid in sorted(self.devices):
            d = self._config.device(vid)
            fields = " ".join(f"{k}={v}" for k, v in self.devices[vid].items())
            print(f"  {vid:8} {d.device_type:16} {fields}")
        if self._pending:
            print(f"  전환 대기 {len(self._pending)}건")


HELP = """
가전
  tv [on|off]              TV 전원
  light <0-100|on|off>     조명 밝기
  fridge [open|close]      냉장고 문
  washer [WASH|RINSE|SPIN|DONE]
  clean <start|stop|dock>  로봇 청소기
  dispense                 정수기 급수 이벤트
  remote                   TV 리모컨 입력 이벤트

센서
  presence <area> [off]    재실 (energy 45)
  still <area>             정지 (energy 3) — 낙상 판정용
  bed [off]                침대 점유
  door [open|close]        현관문
  power <vid> <watt>       전력 센서

기타
  state                    현재 가전 상태
  help / quit
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="HESTIA 가전·센서 시뮬레이터")
    parser.add_argument(
        "--config", default=os.environ.get("HESTIA_CONFIG", "./config"),
        help="homes/ 와 policy.toml 이 있는 디렉터리",
    )
    parser.add_argument("--home", default=os.environ.get("HESTIA_HOME", "demo"))
    parser.add_argument("--host", default=os.environ.get("MQTT_HOST", "localhost"))
    parser.add_argument(
        "--port", type=int, default=int(os.environ.get("MQTT_PORT", "1883"))
    )
    args = parser.parse_args(argv)

    root = Path(args.config)
    try:
        config = load(root / "homes" / f"{args.home}.toml", root / "policy.toml")
    except ConfigError as exc:
        print(f"설정 오류: {exc}")
        return 1

    sim = Simulator(config, args.host, args.port)
    try:
        sim.run()
    except KeyboardInterrupt:
        sim.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())