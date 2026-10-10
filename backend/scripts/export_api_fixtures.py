"""Flutter 계약 테스트용 API 응답 fixture 생성.

브로커 없이 FastAPI 를 띄우고 registry 와 mock_mqtt 의 full 시나리오(가전 + context + 알림)를 넣은 뒤,
Flutter ApiHestiaRepository 가 호출하는 응답을 JSON 파일로 저장한다.
Flutter 는 손으로 쓴 JSON 대신 이 파일로 파싱을 검증한다.

요청 방향(Flutter → FastAPI)은 반대로 apps/test/fixtures/api/requests/ 의
파일을 Flutter 테스트가 만들고, backend/tests/test_contract.py 가 그대로 보낸다.

사용 (backend/ 에서):
  python scripts/export_api_fixtures.py
API 스키마를 바꾸면 다시 실행하고 Flutter 테스트를 돌린다.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

# 시각 문자열의 offset 이 실행 환경마다 달라지지 않게 RPi5 와 같은 시간대로 고정한다.
os.environ["TZ"] = "Asia/Seoul"
time.tzset()

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "scripts"))

from app.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402
from mock_mqtt import envelope, scenario  # noqa: E402

FIXTURES = BACKEND.parent / "apps" / "test" / "fixtures" / "api"
RESPONSES = FIXTURES / "responses"
REQUESTS = FIXTURES / "requests"

# 실제로는 Context Engine 이 retained 로 남겨 둔다. mock_mqtt 는 발행하지 않으므로
# 여기서 넣어 알림 channels(virtual_id) → roomId 변환이 fixture 에 드러나게 한다.
REGISTRY = (
    ("vd-01", "smart_tv", "living"),
    ("vd-02", "smart_light", "living"),
    ("vd-03", "air_conditioner", "living"),
    ("vd-05", "smart_fridge", "kitchen"),
    ("vd-08", "washer", "utility"),
)


class RecordingMqtt:
    """브로커 대신 발행만 기록한다. ack 가 forwarded=true 로 나오게 연결 상태로 둔다."""

    enabled = True
    connected = True

    def __init__(self) -> None:
        self.published: list[tuple[str, dict[str, Any]]] = []

    def set_handler(self, handler) -> None:
        pass

    def add_on_connect(self, callback) -> None:
        pass

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass

    def publish(self, topic, payload, *, qos=1, retain=False) -> bool:
        self.published.append((topic, payload))
        return True


def collect() -> dict[str, Any]:
    """파일 이름 → 응답 JSON. 테스트에서도 같은 함수로 현재 응답을 만든다."""
    app = create_app(Settings(db_path=":memory:"), mqtt=RecordingMqtt())
    out: dict[str, Any] = {}
    with TestClient(app) as client:
        api = "/api/v1"

        def get(path: str) -> Any:
            res = client.get(api + path)
            res.raise_for_status()
            return res.json()

        setup = json.loads((REQUESTS / "setup.json").read_text(encoding="utf-8"))
        client.put(api + "/setup", json=setup).raise_for_status()

        ingest = app.state.hestia.ingest
        registry = envelope("rpi5", devices=[
            {"virtual_id": vid, "device_type": dtype, "area": area}
            for vid, dtype, area in REGISTRY
        ])
        messages = [("hestia/registry/devices", registry, True), *scenario("full")]
        for topic, payload, _retain in messages:
            if not ingest.handle(topic, json.dumps(payload, ensure_ascii=False)):
                raise RuntimeError(f"시나리오 메시지가 버려졌다: {topic}")

        notifications = get("/notifications")
        out["setup"] = get("/setup")
        out["rooms"] = get("/rooms")
        out["devices"] = get("/devices")
        out["context_current"] = get("/context/current")
        out["notifications"] = notifications
        out["explanations_latest"] = get("/explanations/latest")
        # 알림 explanationId 는 decision_id 연결 확정 전까지 null 이다. 판단 id 로 직접 조회한다.
        out["explanation_by_id"] = get(f"/explanations/{out['explanations_latest']['id']}")
        out["preferences"] = get("/preferences")

        # RPi4 weather 서비스(services/weather)가 보내는 형태. 관측은 이번 정시 (stale=false)
        observed = int(time.time()) // 3600 * 3600
        weather = envelope(
            "rpi4", reason="reply", source="kma",
            location={"name": "서울", "nx": 60, "ny": 127},
            observed_at=observed, temperature_c=31.2, humidity_pct=58.0,
            precip_1h_mm=0.0, precip_type="NONE", wind_speed_ms=1.8,
            warnings=[{"name": "폭염경보", "type": "HEAT", "level": "WARNING"}],
            warnings_issued_at=observed,
        )
        if not ingest.handle("hestia/external/weather", json.dumps(weather, ensure_ascii=False)):
            raise RuntimeError("날씨 메시지가 버려졌다")
        out["weather"] = get("/weather")

        medication = json.loads((REQUESTS / "medication.json").read_text(encoding="utf-8"))
        client.post(api + "/medications", json=medication).raise_for_status()
        out["medications"] = get("/medications")

        ack = client.post(f"{api}/notifications/{notifications[0]['id']}/ack",
                          json={"ackType": "SEEN"})
        ack.raise_for_status()
        out["ack"] = ack.json()
    return out


def main() -> None:
    RESPONSES.mkdir(parents=True, exist_ok=True)
    for name, body in collect().items():
        path = RESPONSES / f"{name}.json"
        path.write_text(json.dumps(body, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"[OK] {path.relative_to(BACKEND.parent)}")


if __name__ == "__main__":
    main()
