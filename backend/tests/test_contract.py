"""Flutter ↔ FastAPI 계약 — apps/test/fixtures/api/ 를 양쪽이 같이 쓴다.

- responses/: 이 서버가 만든 응답. Flutter 테스트가 파싱한다.
  API 응답 구조가 바뀌었는데 fixture 를 다시 만들지 않으면 여기서 실패한다.
- requests/: Flutter 모델의 toJson 결과. 이 서버가 그대로 받아 저장해야 한다.
  ApiModel 은 모르는 키를 버리므로(422 가 아님) 저장 후 다시 읽어 값을 비교한다.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from conftest import envelope, make_hestia, Hestia

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND / "scripts"))

from export_api_fixtures import REQUESTS, RESPONSES, collect  # noqa: E402

REGENERATE = "backend/ 에서 python scripts/export_api_fixtures.py 로 다시 만든 뒤 Flutter 테스트를 돌린다."


def load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def drop_nulls(value: Any) -> Any:
    """Flutter 는 null 필드를 빼고 보내고, 서버는 null 로 돌려준다. 둘은 같은 뜻이다."""
    if isinstance(value, dict):
        return {k: drop_nulls(v) for k, v in value.items() if v is not None}
    if isinstance(value, list):
        return [drop_nulls(v) for v in value]
    return value


def shape(value: Any) -> Any:
    """값은 버리고 키와 타입만 남긴다. 시각·id 처럼 매번 바뀌는 값은 비교하지 않는다."""
    if isinstance(value, dict):
        return {k: shape(v) for k, v in sorted(value.items())}
    if isinstance(value, list):
        return [shape(v) for v in value]
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, (int, float)):
        return "number"
    return type(value).__name__


def test_response_fixtures_match_current_api():
    current = collect()
    saved = {p.stem: load(p) for p in RESPONSES.glob("*.json")}
    assert set(saved) == set(current), REGENERATE
    for name, body in current.items():
        assert shape(saved[name]) == shape(body), f"{name}.json 이 현재 응답과 다르다. {REGENERATE}"


def test_flutter_setup_request_is_stored_as_sent():
    sent = load(REQUESTS / "setup.json")
    client, mqtt = make_hestia()
    with client:
        h = Hestia(client, mqtt)
        assert h.put("/setup", json=sent).status_code == 200
        body = h.get("/setup").json()

    assert body["rooms"] == sent["rooms"]
    assert drop_nulls(body["preferences"]) == sent["preferences"]
    keys = ("id", "name", "type", "roomId")
    stored = sorted(({k: d[k] for k in keys} for d in body["devices"]), key=lambda d: d["id"])
    expected = sorted(({k: d[k] for k in keys} for d in sent["devices"]), key=lambda d: d["id"])
    assert stored == expected


def test_flutter_preferences_request_is_stored_as_sent():
    sent = load(REQUESTS / "preferences.json")
    client, mqtt = make_hestia()
    with client:
        h = Hestia(client, mqtt)
        assert h.put("/preferences", json=sent).status_code == 200
        assert drop_nulls(h.get("/preferences").json()) == sent


def test_flutter_ack_request_marks_seen(hestia):
    hestia.receive("hestia/notify/push", envelope("rpi5", notify_id="n-001",
                                                      payload={"title": "복약"}))
    res = hestia.post("/notifications/n-001/ack", json=load(REQUESTS / "ack_seen.json"))
    assert res.json() == {"success": True, "forwarded": True}
    assert hestia.get("/notifications").json()[0]["seen"] is True
    topic, payload, _qos, _retain = hestia.mqtt.published[-1]
    assert topic == "hestia/notify/ack"
    assert payload["ack_type"] == "SEEN"
