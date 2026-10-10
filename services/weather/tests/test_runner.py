from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from conftest import FakeKma, kst, ncst_response, warning_response
from hestia_weather.config import ConfigError, load_location, location_from
from hestia_weather.kma import KmaClient, KmaError
from hestia_weather.runner import GET_TOPIC, WEATHER_TOPIC, Runner
from hestia_weather.service import Location, WeatherService

REPO = Path(__file__).resolve().parents[3]


@dataclass
class Msg:
    topic: str
    payload: bytes = b"{}"
    retain: bool = False


class FakeInfo:
    rc = 0


class FakeClient:
    def __init__(self) -> None:
        self.published: list[tuple[str, dict, int, bool]] = []
        self.subscribed: list[str] = []

    def publish(self, topic, payload, qos=0, retain=False):
        self.published.append((topic, json.loads(payload), qos, retain))
        return FakeInfo()

    def subscribe(self, topic, qos=0):
        self.subscribed.append(topic)


class Clock:
    def __init__(self, t: float) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


def make(fake: FakeKma, t: float, warning_areas: tuple[str, ...] = ()):
    clock = Clock(t)
    client = FakeClient()
    svc = WeatherService(KmaClient("key", 60, 127, fetch=fake), Location("서울", 60, 127, warning_areas))
    return Runner(svc, clock=clock, client=client), client, clock


def test_new_observation_is_pushed_immediately_not_retained():
    fake = FakeKma({"1400": ncst_response("20261010", "1400")})
    runner, client, clock = make(fake, kst(2026, 10, 10, 14, 20))

    runner.tick()
    assert len(client.published) == 1
    topic, payload, qos, retain = client.published[0]
    assert (topic, qos, retain) == (WEATHER_TOPIC, 1, False)
    assert payload["reason"] == "update"

    # 같은 정시 동안은 다시 보내지 않는다
    clock.t = kst(2026, 10, 10, 14, 50)
    runner.tick()
    assert len(client.published) == 1


def test_new_heat_warning_is_pushed():
    none = warning_response({"tmFc": "202610101400", "tmSeq": "1", "t6": "o 없 음"})
    fake = FakeKma({"1400": ncst_response("20261010", "1400")}, warning=none)
    runner, client, clock = make(fake, kst(2026, 10, 10, 14, 20), warning_areas=("서울",))
    runner.tick()
    assert client.published[-1][1]["warnings"] == []

    fake.warning = warning_response({"tmFc": "202610101425", "tmSeq": "2", "t6": "o 폭염주의보 : 서울"})
    clock.t = kst(2026, 10, 10, 14, 30)
    runner.tick()
    assert len(client.published) == 2
    assert client.published[-1][1]["warnings"] == [{"name": "폭염주의보", "type": "HEAT", "level": "ADVISORY"}]


def test_get_request_gets_reply_from_cache():
    fake = FakeKma({"1400": ncst_response("20261010", "1400")})
    runner, client, clock = make(fake, kst(2026, 10, 10, 14, 20))
    runner.tick()
    calls = len(fake.urls)

    clock.t = kst(2026, 10, 10, 14, 30)
    runner._on_message(client, None, Msg(GET_TOPIC))
    runner.tick()

    assert client.published[-1][1]["reason"] == "reply"
    assert len(fake.urls) == calls          # 캐시로 응답, API 호출 없음


def test_retained_get_is_ignored():
    fake = FakeKma({"1400": ncst_response("20261010", "1400")})
    runner, client, clock = make(fake, kst(2026, 10, 10, 14, 20))
    runner.tick()

    runner._on_message(client, None, Msg(GET_TOPIC, retain=True))
    runner.tick()
    assert [p[1]["reason"] for p in client.published] == ["update"]


def test_get_before_any_data_is_not_answered():
    fake = FakeKma()
    fake.fail = KmaError("down")
    runner, client, clock = make(fake, kst(2026, 10, 10, 14, 20))

    runner._on_message(client, None, Msg(GET_TOPIC))
    runner.tick()
    assert client.published == []


def test_reconnect_subscribes_and_resends_cache():
    fake = FakeKma({"1400": ncst_response("20261010", "1400")})
    runner, client, clock = make(fake, kst(2026, 10, 10, 14, 20))

    runner._on_connect(client, None, None, 0, None)
    assert client.subscribed == [GET_TOPIC]
    assert client.published == []           # 아직 캐시 없음

    runner.tick()
    runner._on_connect(client, None, None, 0, None)
    assert [p[1]["reason"] for p in client.published] == ["update", "update"]


# ==================================================================== config


def test_demo_home_is_seoul():
    loc = load_location(REPO / "config" / "homes" / "demo.toml")
    assert (loc.name, loc.nx, loc.ny, loc.warning_areas) == ("서울", 60, 127, ("서울",))


def test_grid_override_and_default_warning_area():
    assert location_from({"name": "x", "nx": 61, "ny": 125}) == Location("x", 61, 125, ("x",))
    assert location_from({"nx": 61, "ny": 125, "warning_areas": []}).warning_areas == ()


@pytest.mark.parametrize("loc", [
    {},
    {"lat": "37.5", "lon": 127.0},
    {"lat": 40.0, "lon": 140.0},
    {"nx": 60.5, "ny": 127},
    {"nx": 60, "ny": 127, "warning_areas": "서울"},
    {"nx": 60, "ny": 127, "warning_areas": [""]},
])
def test_bad_location(loc):
    with pytest.raises(ConfigError):
        location_from(loc)


def test_missing_location_table(tmp_path):
    p = tmp_path / "home.toml"
    p.write_text('name = "x"\n', encoding="utf-8")
    with pytest.raises(ConfigError, match=r"\[location\]"):
        load_location(p)
