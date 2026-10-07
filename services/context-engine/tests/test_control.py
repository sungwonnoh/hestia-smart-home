import json
from dataclasses import fields as dc_fields

import pytest

from hestia_engine import messages as m
from hestia_engine.clock import ReplayClock
from hestia_engine.config import load
from hestia_engine.control import (
    EMPTY_PARAM_ACTIONS,
    SET_PARAMS,
    TRANSITION_KEY,
    Controller,
)
from hestia_engine.engine import RecordingPublisher
from hestia_engine.messages import parse
from hestia_engine.timers import Scheduler
from hestia_engine.world import WorldState

from test_activity import HOME, POLICY, MORNING


class Ctx:
    def __init__(self, tmp_path, start: float = MORNING):
        h = tmp_path / "home.toml"
        p = tmp_path / "policy.toml"
        h.write_text(HOME, encoding="utf-8")
        p.write_text(POLICY, encoding="utf-8")
        self.config = load(h, p)
        self.clock = ReplayClock(start)
        self.sched = Scheduler(self.clock)
        self.world = WorldState(self.clock, self.config, self.sched)
        self.pub = RecordingPublisher()
        self.control = Controller(
            self.clock, self.config, self.world,
            publish=lambda t, p, r: self.pub.publish(t, p, retain=r),
        )

    def state(self, vid: str, device_type: str, **fields):
        """가전 state 를 World 에 넣는다. 노드가 명령을 실행한 결과다."""
        body = {
            "version": 1, "sent_ts": 0, "src_id": vid, "seq": 1,
            "device_type": device_type, "source": "mock", **fields,
        }
        msg = parse(f"hestia/device/{vid}/state", json.dumps(body), self.clock.now())
        assert msg is not None, f"파싱 실패: {body}"
        self.world.apply(msg)
        return self

    def cmds(self, vid: str | None = None) -> tuple[dict, ...]:
        out = tuple(
            p for _, t, p in self.pub.published if t.endswith("/cmd")
        )
        if vid is None:
            return out
        return tuple(p for p in out if p.get("device_type") is not None
                     and any(f"/{vid}/cmd" == t[-len(f'/{vid}/cmd'):]
                             for _, t, q in self.pub.published if q is p))

    def topics(self) -> list[str]:
        return [t for _, t, _ in self.pub.published]


@pytest.fixture
def c(tmp_path):
    return Ctx(tmp_path)


# ============================================================ 발행


def test_send_publishes_cmd(c):
    cmd_id = c.control.set("vd-01", power="OFF", reason="SLEEP_ROUTINE")

    assert cmd_id is not None
    assert "hestia/device/vd-01/cmd" in c.topics()

    p = c.cmds()[0]
    assert p["cmd_id"] == cmd_id
    assert p["device_type"] == "smart_tv"
    assert p["action"] == "set"
    assert p["params"] == {"power": "OFF"}
    assert p["reason"] == "SLEEP_ROUTINE"


def test_cmd_is_never_retained(c):
    """재부팅 시 과거 명령이 재실행된다 (명세)."""
    seen: list[bool] = []
    c.control._publish = lambda t, p, r: seen.append(r)
    c.control.set("vd-01", power="OFF")
    assert seen == [False]


def test_cmd_has_no_seq(c):
    """seq 가 없다 — cmd_id 가 식별자 역할을 한다 (명세)."""
    c.control.set("vd-01", power="OFF")
    assert "seq" not in c.cmds()[0]


def test_cmd_id_is_unique(c):
    a = c.control.set("vd-01", power="OFF")
    b = c.control.set("vd-01", power="ON")
    assert a != b


def test_sent_ts_is_integer(c):
    """명세의 Envelope 는 Unix timestamp (초)."""
    c.control.set("vd-01", power="OFF")
    assert isinstance(c.cmds()[0]["sent_ts"], int)


def test_transition_merged_into_params(c):
    """목표값과 소요 시간만 전달하고 보간은 노드가 한다 (명세).

    RPi5 가 밝기를 1씩 올리며 연달아 보내면 메시지가 폭증하고
    네트워크 지연에 따라 점등이 끊겨 보인다.
    """
    c.control.set("vd-02", brightness=40, transition_ms=30000)

    params = c.cmds()[0]["params"]
    assert params["brightness"] == 40
    assert params[TRANSITION_KEY] == 30000


def test_caller_params_not_mutated(c):
    """호출부의 딕셔너리를 고치면 다음 호출에 섞인다."""
    mine = {"brightness": 40}
    c.control.send(target="vd-02", params=mine, transition_ms=1000)
    assert mine == {"brightness": 40}


def test_empty_param_action(c):
    """start / stop / dock 은 params 가 빈 객체다 (명세)."""
    cmd_id = c.control.send(target="vd-09", action="stop", reason="SLEEP_ROUTINE")

    assert cmd_id is not None
    p = c.cmds()[0]
    assert p["action"] == "stop"
    assert p["params"] == {}


# ============================================================ 검증


def test_rejects_unknown_target(c):
    """설정에 없는 ID 는 보내지 않는다."""
    assert c.control.set("vd-99", power="OFF") is None
    assert c.cmds() == ()


def test_rejects_unknown_action(c):
    assert c.control.send(target="vd-01", action="toggle") is None
    assert c.cmds() == ()


def test_rejects_unknown_param(c):
    """오타를 여기서 잡는다. 노드는 조용히 무시할 뿐이다."""
    assert c.control.set("vd-01", volum=10) is None
    assert c.cmds() == ()


def test_rejects_param_of_other_type(c):
    """TV 에는 brightness 가 없다."""
    assert c.control.set("vd-01", brightness=40) is None


def test_rejects_empty_set(c):
    assert c.control.send(target="vd-01", action="set", params={}) is None


def test_rejects_transition_only(c):
    """transition_ms 는 실행 옵션이지 목표 상태가 아니다."""
    assert c.control.send(target="vd-01", action="set", transition_ms=1000) is None


def test_display_node_is_not_controllable(c):
    """알림 채널이지 제어 대상이 아니다."""
    assert c.control.set("vd-10", power="OFF") is None


def test_params_are_state_fields():
    """params 키는 그 기기 state 의 필드명과 같아야 한다 (명세).

    명령 후 state 를 비교해 반영 여부를 검증할 수 있어야 한다.
    읽기 전용 필드(remain_min, battery, pm25)는 set 에서 빠진다.
    """
    classes = {
        "smart_tv": m.SmartTvState,
        "smart_light": m.SmartLightState,
        "air_conditioner": m.AirConditionerState,
        "air_purifier": m.AirPurifierState,
        "water_purifier": m.WaterPurifierState,
        "robot_cleaner": m.RobotCleanerState,
    }
    for dtype, keys in SET_PARAMS.items():
        state_fields = {f.name for f in dc_fields(classes[dtype])}
        assert keys <= state_fields, f"{dtype}: {sorted(keys - state_fields)}"


# ============================================================ 보낸 기록


def test_last_for_keeps_latest(c):
    c.control.set("vd-02", brightness=70)
    c.control.set("vd-02", brightness=40)

    cmd = c.control.last_for("vd-02")
    assert cmd is not None
    assert cmd.params["brightness"] == 40


def test_last_for_none_without_command(c):
    assert c.control.last_for("vd-02") is None


def test_last_for_is_per_device(c):
    c.control.set("vd-01", power="OFF")
    c.control.set("vd-02", brightness=40)

    assert c.control.last_for("vd-01").params == {"power": "OFF"}
    assert c.control.last_for("vd-02").params == {"brightness": 40}


# ============================================================ 되돌림


def test_not_reverted_when_applied(c):
    """명령대로 됐으면 되돌림이 아니다."""
    c.control.set("vd-02", brightness=40)
    c.state("vd-02", "smart_light", power="ON", brightness=40)
    assert c.control.reverted("vd-02") is False


def test_reverted_when_user_raises(c):
    """엔진이 내리고 사람이 올리는 싸움이 되면 개입이 아니라 방해다."""
    c.control.set("vd-02", brightness=40)
    c.state("vd-02", "smart_light", power="ON", brightness=100)
    assert c.control.reverted("vd-02") is True


def test_reverted_when_lower(c):
    """더 어두워진 것도 되돌림이다. 엔진이 정한 값을 사람이 바꿨다."""
    c.control.set("vd-02", brightness=40)
    c.state("vd-02", "smart_light", power="ON", brightness=20)
    assert c.control.reverted("vd-02") is True

def test_reverted_on_rising_command(c):
    """기상 시 조명을 밝히는 명령도 같다 — 방향을 보지 않는다."""
    c.control.set("vd-02", brightness=80, reason="WAKE_ROUTINE")
    c.state("vd-02", "smart_light", power="ON", brightness=30)
    assert c.control.reverted("vd-02") is True


def test_reverted_on_power_string(c):
    """문자열은 방향이 없다. 다르면 되돌림이다."""
    c.control.set("vd-01", power="OFF")
    c.state("vd-01", "smart_tv", power="ON")
    assert c.control.reverted("vd-01") is True


def test_reverted_any_field(c):
    """여러 필드를 보냈으면 하나라도 어긋나면 되돌림이다."""
    c.control.set("vd-03", power="ON", mode="COOL", temp_set=24)
    c.state("vd-03", "air_conditioner", power="ON", mode="DRY", temp_set=24)
    assert c.control.reverted("vd-03") is True


def test_transition_key_not_compared(c):
    """실행 옵션이라 state 에 없다. 비교 대상이 아니다."""
    c.control.set("vd-02", brightness=40, transition_ms=30000)
    c.state("vd-02", "smart_light", power="ON", brightness=40)
    assert c.control.reverted("vd-02") is False


def test_not_reverted_without_state(c):
    """state 를 한 번도 못 받았으면 판정할 수 없다.

    명령은 나갔는데 노드가 응답을 안 한 것이고, 그것은 되돌림이
    아니라 무응답이다.
    """
    c.control.set("vd-02", brightness=40)
    assert c.control.reverted("vd-02") is False


def test_not_reverted_without_command(c):
    assert c.control.reverted("vd-02") is False


def test_reverted_when_cleaning_stopped(c):
    """청소를 시작시켰는데 멈춰 있으면 사용자가 멈춘 것이다.

    조명 되돌림과 달리 '일이 안 됐다' 는 사실이라 재시도의 근거다.
    """
    c.control.send(target="vd-09", action="start")
    c.state("vd-09", "robot_cleaner", status="PAUSED")
    assert c.control.reverted("vd-09") is True


def test_not_reverted_when_cleaning(c):
    c.control.send(target="vd-09", action="start")
    c.state("vd-09", "robot_cleaner", status="CLEANING")
    assert c.control.reverted("vd-09") is False


# ============================================================ 대상 찾기


def test_devices_of_type(c):
    assert "vd-02" in c.control.devices_of_type("smart_light")


def test_devices_of_type_in_area(c):
    living = c.control.devices_of_type("smart_light", "living")
    for vid in living:
        assert c.config.device(vid).area == "living"


def test_devices_of_type_ignores_channel_flag(c):
    """알림을 띄울 수 없는 기기도 제어는 된다."""
    found = c.control.devices_of_type("smart_light")
    assert found                      # channel=false 여도 나온다


def test_devices_of_type_empty(c):
    assert c.control.devices_of_type("doorbell", "bathroom") == ()