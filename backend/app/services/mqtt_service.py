"""Mosquitto 연결.

FastAPI 시작 시 연결하고 필요한 토픽을 구독한다. 받은 메시지는 handler
(IngestService.handle)로 넘긴다. 끊기면 paho 가 자동으로 다시 연결하고,
연결될 때마다 다시 구독한다.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Callable, Iterable

log = logging.getLogger(__name__)

Handler = Callable[[str, bytes], Any]


class MqttService:
    def __init__(
        self,
        host: str,
        port: int,
        client_id: str,
        subscriptions: Iterable[str],
    ) -> None:
        self._host = host
        self._port = port
        self._client_id = client_id
        self._subscriptions = tuple(subscriptions)
        self._handler: Handler | None = None
        self._client = None
        self._connected = False

    enabled = True

    @property
    def connected(self) -> bool:
        return self._connected

    def set_handler(self, handler: Handler) -> None:
        self._handler = handler

    def start(self) -> None:
        import paho.mqtt.client as mqtt

        client = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            client_id=self._client_id,
        )
        client.on_connect = self._on_connect
        client.on_disconnect = self._on_disconnect
        client.on_message = self._on_message
        client.reconnect_delay_set(min_delay=1, max_delay=30)
        self._client = client
        # 브로커가 아직 없어도 API 는 떠야 하므로 비동기 연결 + 백그라운드 재시도
        client.connect_async(self._host, self._port, keepalive=30)
        client.loop_start()
        log.info("MQTT 연결 시작: %s:%s", self._host, self._port)

    def stop(self) -> None:
        client = self._client
        if client is None:
            return
        try:
            client.disconnect()
        finally:
            client.loop_stop()
            self._client = None
            self._connected = False
        log.info("MQTT 연결 종료")

    def publish(self, topic: str, payload: dict[str, Any], *, qos: int = 1,
                retain: bool = False) -> bool:
        """연결되어 있고 발행 요청이 받아들여지면 True. cmd/ack 는 retain 하지 않는다."""
        client = self._client
        if client is None or not self._connected:
            return False
        import paho.mqtt.client as mqtt

        info = client.publish(topic, json.dumps(payload, ensure_ascii=False),
                              qos=qos, retain=retain)
        return info.rc == mqtt.MQTT_ERR_SUCCESS

    # ------------------------------------------------------------ paho 콜백

    def _on_connect(self, client, userdata, flags, reason_code, properties) -> None:
        if reason_code.is_failure:
            log.warning("MQTT 연결 거부: %s", reason_code)
            return
        self._connected = True
        client.subscribe([(topic, 1) for topic in self._subscriptions])
        log.info("MQTT 연결됨, %d개 토픽 구독", len(self._subscriptions))

    def _on_disconnect(self, client, userdata, flags, reason_code, properties) -> None:
        self._connected = False
        log.warning("MQTT 연결 끊김: %s (자동 재연결)", reason_code)

    def _on_message(self, client, userdata, msg) -> None:
        handler = self._handler
        if handler is None:
            return
        try:
            handler(msg.topic, msg.payload)
        except Exception:  # paho 스레드가 죽지 않게 한다
            log.exception("MQTT handler 오류: %s", msg.topic)


class NullMqttService:
    """HESTIA_MQTT_ENABLED=0 일 때. API 만 띄워 확인할 수 있다."""

    enabled = False
    connected = False

    def set_handler(self, handler: Handler) -> None:
        pass

    def start(self) -> None:
        log.info("MQTT 비활성화 (HESTIA_MQTT_ENABLED=0)")

    def stop(self) -> None:
        pass

    def publish(self, topic: str, payload: dict[str, Any], *, qos: int = 1,
                retain: bool = False) -> bool:
        return False
