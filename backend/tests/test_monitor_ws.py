"""WS /ws/monitor"""

from conftest import envelope


def test_monitor_sends_snapshot_then_live_events(hestia):
    hestia.receive("hestia/context/away", envelope("rpi5", state="HOME"))

    with hestia.client.websocket_connect("/ws/monitor") as ws:
        snapshot = ws.receive_json()
        assert snapshot["type"] == "snapshot"
        assert snapshot["context"]["away"]["state"] == "HOME"

        hestia.receive("hestia/context/activity", envelope(
            "rpi5", state="EATING", confidence=0.87, factors={"area": "kitchen"}))
        event = ws.receive_json()
        assert event["type"] == "context_update"
        assert event["topic"] == "hestia/context/activity"
        assert event["context"] == {"name": "activity", "state": "EATING",
                                    "confidence": 0.87, "factors": {"area": "kitchen"}}
        assert event["data"]["src_id"] == "rpi5"  # Monitor 에는 원본 payload

        hestia.receive("hestia/device/vd-01/state",
                       envelope("vd-01", device_type="smart_tv", power="ON"))
        event = ws.receive_json()
        assert event["type"] == "device_state"
        assert event["device_state"]["vd-01"]["power"] == "ON"

    assert hestia.container.monitor.client_count == 0
