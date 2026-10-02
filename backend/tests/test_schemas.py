import pytest
from pydantic import ValidationError

from app.schemas.common import MqttEnvelope
from app.schemas.context import CONTEXT_SCHEMAS, to_domain
from app.services.device_service import to_status

from conftest import envelope


def test_envelope_ignores_unknown_fields_and_seq_is_optional():
    env = MqttEnvelope.model_validate({"version": 1, "sent_ts": 1755500000,
                                       "src_id": "vd-06", "new_field": 1})
    assert env.seq is None
    assert not hasattr(env, "new_field")


def test_envelope_requires_common_fields():
    with pytest.raises(ValidationError):
        MqttEnvelope.model_validate({"version": 1, "src_id": "vd-06"})


def test_activity_domain_drops_envelope_and_keeps_int_timestamps():
    model = CONTEXT_SCHEMAS["activity"].model_validate(envelope(
        "rpi5", state="EATING", since=1755499500, confidence=0.87,
        factors={"area": "kitchen", "presence": True},
    ))
    domain = to_domain(model)
    assert domain == {"state": "EATING", "since": 1755499500, "confidence": 0.87,
                      "factors": {"area": "kitchen", "presence": True}}
    assert isinstance(domain["since"], int)


def test_presence_has_area_instead_of_state():
    model = CONTEXT_SCHEMAS["presence"].model_validate(envelope(
        "rpi5", area="kitchen", areas={"kitchen": True, "living": False}, confidence=0.9,
    ))
    assert to_domain(model) == {"area": "kitchen",
                                "areas": {"kitchen": True, "living": False},
                                "confidence": 0.9, "factors": {}}


def test_suppression_keeps_flags_without_confidence():
    model = CONTEXT_SCHEMAS["suppression"].model_validate(envelope("rpi5", focus=True, sleep=False))
    assert to_domain(model) == {"focus": True, "sleep": False}


@pytest.mark.parametrize("device_type, payload, state", [
    ("smart_tv", {"power": "ON", "volume": 12}, "ON"),
    ("air_purifier", {"power": "ON", "mode": "AUTO"}, "AUTO"),
    ("air_purifier", {"power": "OFF", "mode": "AUTO"}, "OFF"),
    ("smart_fridge", {"door": "CLOSED"}, "ON"),
    ("washer", {"power": "ON", "cycle": "SPIN"}, "RUNNING"),
    ("washer", {"power": "ON", "cycle": "DONE"}, "DONE"),
    ("robot_cleaner", {"status": "CLEANING", "battery": 80}, "RUNNING"),
    ("robot_cleaner", {"status": "DOCKED"}, "STANDBY"),
])
def test_device_status_state(device_type, payload, state):
    status = to_status(device_type, envelope("vd-01", device_type=device_type, **payload))
    assert status["state"] == state
    assert "src_id" not in status and "device_type" not in status


def test_air_conditioner_exposes_target_temp_for_flutter():
    status = to_status("air_conditioner", {"power": "ON", "mode": "COOL", "temp_set": 24})
    assert status["state"] == "ON"
    assert status["targetTemp"] == 24
    assert status["mode"] == "COOL"
