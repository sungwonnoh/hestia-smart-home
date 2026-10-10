"""E2E 확인 도구 — broker 없이 검증 로직과 수신 흐름만 시험한다."""

import json

import pytest

import verify_model
from baseline import build_model
from kde_fixtures import all_distributions
from model_payload import PayloadError, build_kde_payload, to_json
from samples import KdeSample


@pytest.fixture(scope="module")
def payload():
    return build_kde_payload(build_model(all_distributions()))


def test_check_payload_ok(payload):
    summary = verify_model.check_payload(to_json(payload).encode("utf-8"))
    assert summary["missing"] == []
    assert summary["distributions"]["sleep_time"]["bins"] == 96
    assert "hydration_lag" not in summary["distributions"]
    assert summary["distributions"]["wake_time"]["sum"] == pytest.approx(1.0)


def test_check_payload_requires_sleep_and_wake():
    meal_only = [KdeSample("meal_time", float(m), f"2026-09-{i + 1:02d}", "sensor") for i, m in enumerate((480, 500, 520))]
    p = build_kde_payload(build_model(meal_only))
    with pytest.raises(PayloadError, match="sleep_time, wake_time"):
        verify_model.check_payload(to_json(p))
    verify_model.check_payload(to_json(p), required=())       # 요구하지 않으면 통과


@pytest.mark.parametrize("raw", [b"not json", b'{"version": 1}', b"\xff\xfe"])
def test_check_payload_rejects_broken(raw):
    with pytest.raises(PayloadError):
        verify_model.check_payload(raw)


class FakeMsg:
    def __init__(self, payload, retain):
        self.payload, self.retain = payload, retain


class FakeClient:
    """connect 시 on_connect, subscribe 시 retained 메시지를 바로 흘려준다."""

    def __init__(self, messages):
        self.messages = messages
        self.calls = []

    def connect(self, host, port, keepalive):
        self.calls.append("connect")
        self.on_connect(self, None, None, 0, None)

    def subscribe(self, topic, qos):
        self.calls.append(("subscribe", topic, qos))
        for m in self.messages:
            self.on_message(self, None, m)

    def loop_start(self):
        self.calls.append("loop_start")

    def loop_stop(self):
        self.calls.append("loop_stop")

    def disconnect(self):
        self.calls.append("disconnect")


def test_receive_retained(payload):
    body = to_json(payload).encode("utf-8")
    client = FakeClient([FakeMsg(body, True), FakeMsg(b"second", False)])
    raw, retained = verify_model.receive_retained(client_factory=lambda: client, timeout=0.1)
    assert (raw, retained) == (body, True)                     # 첫 메시지만
    assert ("subscribe", "hestia/model/kde", 1) in client.calls
    assert client.calls[-2:] == ["loop_stop", "disconnect"]


def test_receive_nothing():
    client = FakeClient([])
    assert verify_model.receive_retained(client_factory=lambda: client, timeout=0.05) is None


def test_main_exit_codes(monkeypatch, payload, capsys):
    body = to_json(payload).encode("utf-8")
    monkeypatch.setattr(verify_model, "receive_retained", lambda *a, **k: (body, True))
    assert verify_model.main([]) == 0
    assert "[OK]" in capsys.readouterr().out

    monkeypatch.setattr(verify_model, "receive_retained", lambda *a, **k: (b"{}", True))
    assert verify_model.main([]) == 1

    monkeypatch.setattr(verify_model, "receive_retained", lambda *a, **k: None)
    assert verify_model.main([]) == 2

    monkeypatch.setattr(verify_model, "receive_retained", lambda *a, **k: (body, False))
    assert verify_model.main([]) == 0
    assert "[WARN]" in capsys.readouterr().out
