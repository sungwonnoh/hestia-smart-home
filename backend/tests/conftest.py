from __future__ import annotations

import json
import time
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


class FakeMqtt:
    """브로커 없이 발행 내용을 기록한다. 수신은 ingest.handle 을 직접 부른다."""

    enabled = True

    def __init__(self) -> None:
        self.connected = True
        self.published: list[tuple[str, dict[str, Any], int, bool]] = []
        self.handler = None

    def set_handler(self, handler) -> None:
        self.handler = handler

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass

    def publish(self, topic, payload, *, qos=1, retain=False) -> bool:
        if not self.connected:
            return False
        self.published.append((topic, payload, qos, retain))
        return True


def envelope(src_id: str, **fields: Any) -> dict[str, Any]:
    return {"version": 1, "sent_ts": int(time.time()), "src_id": src_id, "seq": 1, **fields}


class Hestia:
    """테스트용 묶음: REST client + 가짜 MQTT 수신/발행."""

    def __init__(self, client: TestClient, mqtt: FakeMqtt) -> None:
        self.client = client
        self.mqtt = mqtt

    @property
    def container(self):
        return self.client.app.state.hestia

    def receive(self, topic: str, payload: Any) -> bool:
        raw = payload if isinstance(payload, (str, bytes)) else json.dumps(payload)
        return self.container.ingest.handle(topic, raw)

    def get(self, path: str, **kw):
        return self.client.get(f"/api/v1{path}", **kw)

    def put(self, path: str, **kw):
        return self.client.put(f"/api/v1{path}", **kw)

    def post(self, path: str, **kw):
        return self.client.post(f"/api/v1{path}", **kw)


def make_hestia(**settings: Any):
    mqtt = FakeMqtt()
    app = create_app(Settings(db_path=":memory:", **settings), mqtt=mqtt)
    return TestClient(app), mqtt


@pytest.fixture
def hestia():
    client, mqtt = make_hestia()
    with client:
        yield Hestia(client, mqtt)


SETUP = {
    "rooms": [
        {"id": "living", "name": "거실", "roles": ["LIVING"]},
        {"id": "kitchen", "name": "주방", "roles": ["MEAL"]},
    ],
    "devices": [
        {"id": "tv-01", "name": "거실 TV", "type": "TV", "roomId": "living"},
        {"id": "light-01", "name": "주방 조명", "type": "LIGHT", "roomId": "kitchen"},
        {"id": "air-conditioner-01", "name": "거실 에어컨", "type": "AIR_CONDITIONER",
         "roomId": "living",
         # Flutter 가 함께 보내는 값은 무시한다
         "status": {"state": "ON"}, "online": True},
    ],
    "preferences": {
        "notificationsEnabled": True,
        "quietHours": {"enabled": True, "start": "23:00", "end": "06:30"},
        "sensitivity": "high",
        "safety": {"enabled": True, "emergencyContact": "010-0000-0000"},
    },
}
