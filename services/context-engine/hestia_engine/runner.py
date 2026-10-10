"""실시간 실행기.

조립과 루프만 한다.

    Replay          Runner
    ReplayClock  →  RealClock
    JSONL        →  on_message
    Recording    →  MqttPublisher

실행:
    python -m hestia_engine.runner
    HESTIA_CONFIG=/data/hestia MQTT_HOST=localhost python -m hestia_engine.runner
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import sys
import time
from pathlib import Path
from typing import Any

import paho.mqtt.client as mqtt

from .clock import RealClock
from .config import Config, ConfigError, load
from .engine import Engine, MqttPublisher
from .fsm import FileT0Log
from .timers import Scheduler
from .world import WorldState

log = logging.getLogger(__name__)

NODE_ID = "rpi5"
STATUS_TOPIC = f"hestia/node/{NODE_ID}/status"
SCHEMA_VERSION = 1

# RPi5 구독 목록. context 는 구독하지 않는다 —
# 자신이 발행하며 판단은 메모리의 context 객체를 직접 읽는다.
SUBSCRIBE = (
    "hestia/sensor/+/state",
    "hestia/device/+/state",
    "hestia/device/+/event",
    "hestia/notify/ack",
    "hestia/node/+/announce",
    "hestia/node/+/status",
    "hestia/model/+",
    "hestia/registry/devices",
    "hestia/registry/medications",
    "hestia/system/profile",
)

LOOP_TIMEOUT_SEC = 0.5      # 소켓 대기. 이만큼마다 타이머를 확인


class Runner:
    """브로커에 붙어 엔진을 돌린다."""

    def __init__(
        self,
        config: Config,
        *,
        host: str = "localhost",
        port: int = 1883,
        data_dir: Path | None = None,
    ) -> None:
        self._host = host
        self._port = port
        self._running = False

        self.clock = RealClock()
        self.sched = Scheduler(self.clock)
        self.world = WorldState(self.clock, config, self.sched)

        t0log = FileT0Log((data_dir or Path("/data/hestia")) / "t0_log.jsonl")
        self.client = self._make_client()
        self.publisher = MqttPublisher(self.client)
        self.engine = Engine(
            self.clock, config, self.world, self.sched, self.publisher, t0log
        )

    # ------------------------------------------------------------ 생애

    def start(self) -> None:
        """브로커가 늦게 기동해도 무한 재시도한다 (명세)."""
        self._running = True
        try:
            self.client.connect(self._host, self._port, keepalive=60)
            log.info("브로커 연결: %s:%s", self._host, self._port)
        except OSError as exc:
            log.warning("브로커 연결 실패 (%s) — 루프에서 재시도한다", exc)
            self.client.connect_async(self._host, self._port, keepalive=60)

    def run(self) -> None:
        """메시지와 타이머를 번갈아 처리한다."""
        self.start()
        while self._running:
            try:
                self.client.loop(timeout=LOOP_TIMEOUT_SEC)
                self.sched.run_due()
            except KeyboardInterrupt:
                self.stop()
            except Exception:                         # noqa: BLE001
                # 한 번의 예외가 엔진을 멈추게 해서는 안 된다
                log.exception("루프 예외 — 계속한다")
                time.sleep(LOOP_TIMEOUT_SEC)

    def stop(self) -> None:
        """계획된 종료. LWT 대신 online=false 를 스스로 발행한다."""
        if not self._running:
            return
        self._running = False
        log.info("종료 — %s", self.engine.summary())
        try:
            self._publish_status(online=False)
            self.client.disconnect()
        except Exception:                             # noqa: BLE001
            log.warning("종료 중 예외", exc_info=True)

    # ------------------------------------------------------------ 콜백

    def _make_client(self) -> mqtt.Client:
        client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=f"hestia-engine-{os.getpid()}",
        )
        client.on_connect = self._on_connect
        client.on_disconnect = self._on_disconnect
        client.on_message = self._on_message

        # 죽으면 브로커가 대신 발행한다. (대시보드가 엔진 다운을 알 수 있도록)
        client.will_set(
            STATUS_TOPIC,
            json.dumps(self._status_payload(online=False)),
            qos=1,
            retain=True,
        )
        client.reconnect_delay_set(min_delay=1, max_delay=30)
        return client

    def _on_connect(self, client, userdata, flags, reason_code, properties) -> None:
        """paho가 브로커와 "연결이 성립할 때마다" / 재연결 때마다 호출된다.
           구독을 여기서 해야 끊겼다 붙어도 살아난다.
           
           기동 → connect_async → 연결 성립 → _on_connect
           네트워크 끊김 → 자동 재연결 → 연결 성립 → _on_connect (다시)
        """
        if reason_code != 0:        # reason_code: 연결 결과. 0이면 성공
            log.warning("연결 거부 rc=%s", reason_code)
            return

        for topic in SUBSCRIBE:     # 구독, RPi5 구독 목록 9개에 대해 구독
            client.subscribe(topic, qos=1)
        log.info("연결됨 — 구독 %d개", len(SUBSCRIBE))

        self._publish_status(online=True)       # 자기 상태 발행 (hestia/node/rpi5/status)

        # 재시작 직후 모든 context 는 UNKNOWN 이다.
        # 대시보드가 옛 retained 를 보고 있지 않도록 현재 상태(UNKNOWN)를 즉시 알린다.
        self.engine.publish_snapshot()          # hestia/context/* 여섯 개를 전량 적재 (UNKNOWN 발행도 적재)

    def _on_disconnect(self, client, userdata, flags, reason_code, properties) -> None:
        if self._running:
            log.warning("연결 끊김 rc=%s — 재연결 대기", reason_code)

    def _on_message(self, client, userdata, msg) -> None:
        """retain 플래그를 그대로 넘긴다."""
        try:
            self.engine.ingest(msg.topic, msg.payload, retained=bool(msg.retain))
        except Exception:                             # noqa: BLE001
            # 한 메시지의 문제가 엔진을 멈추게 해서는 안 된다
            log.exception("수신 처리 실패: %s", msg.topic)

    # ------------------------------------------------------------ 내부

    def _status_payload(self, *, online: bool) -> dict[str, Any]:
        return {
            "version": SCHEMA_VERSION,
            "sent_ts": int(time.time()),
            "src_id": NODE_ID,
            "online": online,
            "ts_synced": True,          # RPi5 는 RTC 와 NTP 가 있다
        }

    def _publish_status(self, *, online: bool) -> None:
        self.client.publish(
            STATUS_TOPIC,
            json.dumps(self._status_payload(online=online)),
            qos=1,
            retain=True,
        )


# ==================================================================== 조립


def build(argv: argparse.Namespace) -> Runner:
    """설정을 읽어 Runner 를 만든다."""
    root = Path(os.environ.get("HESTIA_CONFIG", argv.config))
    config = load(
        root / "homes" / f"{argv.home}.toml",
        root / "policy.toml",
    )
    if argv.profile:
        config.set_profile(argv.profile)

    log.info("설정: %s (profile=%s)", config, config.profile)
    runner = Runner(
        config,
        host=argv.host,
        port=argv.port,
        data_dir=Path(argv.data),
    )

    # retained 로 돌아오지 않는 것들을 디스크에서 되살린다.
    runner.engine.restore()
    return runner


def main(argv: list[str] | None = None) -> int:
    # 인자 파서 구성, ArgumentParser: 명령줄 인자를 받는 객체
    parser = argparse.ArgumentParser(description="HESTIA Context Engine")

    # 인자 일곱개
    parser.add_argument(
        "--config",
        default=os.environ.get("HESTIA_CONFIG", "/data/hestia/config"),
        help="homes/ 와 policy.toml 이 있는 디렉터리",
    )
    parser.add_argument("--home", default=os.environ.get("HESTIA_HOME", "demo"))
    parser.add_argument("--host", default=os.environ.get("MQTT_HOST", "localhost"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("MQTT_PORT", "1883")))
    parser.add_argument("--data", default=os.environ.get("HESTIA_DATA", "/data/hestia"))
    parser.add_argument("--profile", choices=("REAL", "DEMO"))
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)      # 실제로 파싱

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )

    try:
        runner = build(args)        # build()가 설정을 읽고 Runner 객체를 만듦
    except ConfigError as exc:
        log.error("설정 오류 — 기동하지 않는다: %s", exc)
        return 1

    # systemd 가 SIGTERM 을 보낸다. 받으면 깨끗이 닫는다.
    signal.signal(signal.SIGTERM, lambda *_: runner.stop())
    signal.signal(signal.SIGINT, lambda *_: runner.stop())

    runner.run()        # run() 안에서 start()를 호출하고 무한 루프를 돈다.
    return 0


if __name__ == "__main__":
    sys.exit(main())