import json
from pathlib import Path

import pytest

from hestia_engine.clock import ReplayClock
from hestia_engine.config import load, load_default
from hestia_engine.messages import parse
from hestia_engine.timers import Scheduler
from hestia_engine.world import (
    BedState,
    DeviceState,
    DoorState,
    LightState,
    MotionState,
    PowerState,
    PresenceState,
    WorldState,
    _event_type,
    _primary,
)

HOME = """
name = "test"
areas = ["living", "kitchen", "bedroom"]
roles = ["LIVING", "SLEEP", "MEAL"]

[[sensors]]
id = "vs-01"
type = "presence"
area = "living"
roles = ["LIVING"]
node = "esp32-1"

[[sensors]]
id = "vs-02"
type = "presence"
area = "kitchen"
roles = ["MEAL"]
node = "esp32-2"

[[sensors]]
id = "vs-03"
type = "power"
area = "kitchen"
roles = ["MEAL"]
power_profile = "induction"
node = "esp32-2"

[[sensors]]
id = "vs-04"
type = "motion"
area = "kitchen"
roles = ["MEAL"]
node = "esp32-2"

[[sensors]]
id = "vs-05"
type = "bed"
area = "bedroom"
roles = ["SLEEP"]
node = "esp32-1"

[[sensors]]
id = "vs-06"
type = "door"
area = "living"
roles = ["LIVING"]
node = "esp32-1"

[[sensors]]
id = "vs-07"
type = "light"
area = "living"
roles = ["LIVING"]

[[sensors]]
id = "vs-99"
type = "presence"
area = "living"
roles = ["LIVING"]
enabled = false

[[devices]]
id = "vd-01"
device_type = "smart_tv"
area = "living"
channel = true

[[devices]]
id = "vd-02"
device_type = "washer"
area = "kitchen"
"""

POLICY = """
[power.default]
standby_max_w = 5
on_min_w = 50
on_exit_w = 30
min_hold_sec = 10

[power.induction]
standby_max_w = 10
on_min_w = 300
on_exit_w = 150
min_hold_sec = 15

[presence.energy]
still_max = 10
active_min = 30
"""

T0 = 1_000_000.0


@pytest.fixture
def ctx(tmp_path):
    h = tmp_path / "home.toml"
    p = tmp_path / "policy.toml"
    h.write_text(HOME, encoding="utf-8")
    p.write_text(POLICY, encoding="utf-8")
    cfg = load(h, p)
    clock = ReplayClock(T0)
    sched = Scheduler(clock)
    return clock, sched, WorldState(clock, cfg, sched)


def feed(ctx, topic: str, payload: dict, *, at: float | None = None):
    """시계를 옮기고 메시지 한 건을 투입한다."""
    clock, sched, world = ctx
    if at is not None:
        clock.advance_to(at)
        sched.run_due()
    msg = parse(topic, json.dumps(payload), clock.now())
    assert msg is not None, f"파싱 실패: {topic} {payload}"
    world.apply(msg)
    return msg


def sensor(vid: str, **fields) -> tuple[str, dict]:
    return f"hestia/sensor/{vid}/state", {
        "version": 1, "sent_ts": 0, "src_id": vid, "seq": 1, **fields
    }


def presence(vid: str, present: bool, energy: int = 50) -> tuple[str, dict]:
    return sensor(vid, type="presence", present=present,
                  confidence="high", energy=energy, distance_cm=150)


def power(vid: str, watt: float, state: str | None = None) -> tuple[str, dict]:
    payload = {"type": "power", "watt": watt}
    if state is not None:
        payload["state"] = state
    return sensor(vid, **payload)


# ============================================================ 첫 수신


def test_first_message_stamps_changed_at_even_if_value_matches_default(ctx):
    """기본값과 같은 값이 첫 메시지로 와도 시각을 찍어야 한다.

    안 찍으면 changed_at 이 0.0 에 머물러 경과가 17억 초로 나온다.
    """
    _, _, world = ctx
    feed(ctx, *presence("vs-01", present=False))
    assert world.sensor("vs-01").changed_at == T0


def test_first_power_message_commits_immediately(ctx):
    """첫 수신은 전환이 아니라 초기 상태 확인 — min_hold 를 기다리지 않는다."""
    _, sched, world = ctx
    feed(ctx, *power("vs-03", watt=3))
    st = world.sensor("vs-03")
    assert st.state == "OFF"
    assert st.changed_at == T0
    assert sched.pending() == 0


def test_first_device_state_stamps_changed_at(ctx):
    _, _, world = ctx
    feed(ctx, "hestia/device/vd-01/state", {
        "version": 1, "sent_ts": 0, "src_id": "vd-01", "seq": 1,
        "device_type": "smart_tv", "source": "mock", "power": "OFF",
    })
    assert world.device("vd-01").changed_at == T0


# ============================================================ changed_at 규칙


def test_energy_change_does_not_reset_dwell(ctx):
    """부수 값이 흔들려도 체류 시간은 리셋되지 않는다."""
    _, _, world = ctx
    feed(ctx, *presence("vs-02", True, energy=40))
    feed(ctx, *presence("vs-02", True, energy=15), at=T0 + 300)
    feed(ctx, *presence("vs-02", True, energy=60), at=T0 + 600)
    assert world.sensor("vs-02").changed_at == T0
    assert world.dwell_sec("kitchen") == 600.0


def test_present_transition_updates_changed_at(ctx):
    _, _, world = ctx
    feed(ctx, *presence("vs-02", True))
    feed(ctx, *presence("vs-02", False), at=T0 + 300)
    assert world.sensor("vs-02").changed_at == T0 + 300
    assert world.dwell_sec("kitchen") == 0.0        # 재실 아님


def test_watt_change_does_not_reset_state_sec(ctx):
    clock, _, world = ctx
    feed(ctx, *power("vs-03", 1180))
    feed(ctx, *power("vs-03", 1650), at=T0 + 60)     # 둘 다 ON
    clock.advance_to(T0 + 120)
    assert world.sensor("vs-03").state_sec(clock.now()) == 120.0


def test_updated_at_moves_even_without_change(ctx):
    clock, _, world = ctx
    feed(ctx, *presence("vs-01", True))
    feed(ctx, *presence("vs-01", True), at=T0 + 300)
    st = world.sensor("vs-01")
    assert st.changed_at == T0
    assert st.updated_at == T0 + 300
    assert st.idle_sec(clock.now()) == 0.0


# ============================================================ still_since


def test_still_since_starts_when_energy_drops(ctx):
    clock, _, world = ctx
    feed(ctx, *presence("vs-02", True, energy=40))
    assert world.sensor("vs-02").still_since is None

    feed(ctx, *presence("vs-02", True, energy=8), at=T0 + 60)
    clock.advance_to(T0 + 360)
    assert world.still_sec("kitchen") == 300.0


def test_still_since_not_refreshed_while_still(ctx):
    """이미 정지 중이면 시각을 갱신하지 않는다. 갱신하면 경과가 영원히 0 이다."""
    clock, _, world = ctx
    feed(ctx, *presence("vs-02", True, energy=8))
    feed(ctx, *presence("vs-02", True, energy=6), at=T0 + 300)
    feed(ctx, *presence("vs-02", True, energy=9), at=T0 + 600)
    assert world.sensor("vs-02").still_since == T0
    assert world.still_sec("kitchen") == 600.0


def test_movement_clears_still_since(ctx):
    _, _, world = ctx
    feed(ctx, *presence("vs-02", True, energy=8))
    feed(ctx, *presence("vs-02", True, energy=45), at=T0 + 300)
    assert world.sensor("vs-02").still_since is None
    assert world.still_sec("kitchen") == 0.0


def test_absence_clears_still_since(ctx):
    """사람이 없으면 정지가 아니라 부재다."""
    _, _, world = ctx
    feed(ctx, *presence("vs-02", True, energy=8))
    feed(ctx, *presence("vs-02", False, energy=0), at=T0 + 300)
    assert world.sensor("vs-02").still_since is None


def test_still_threshold_is_inclusive(ctx):
    """still_max = 10. 경계값은 정지로 본다."""
    _, _, world = ctx
    feed(ctx, *presence("vs-02", True, energy=10))
    assert world.sensor("vs-02").still_since == T0
    feed(ctx, *presence("vs-02", True, energy=11), at=T0 + 60)
    assert world.sensor("vs-02").still_since is None


# ============================================================ 전력 판정


def test_power_needs_min_hold_to_confirm(ctx):
    """후보가 된 뒤 min_hold_sec 지속돼야 확정한다."""
    clock, sched, world = ctx
    feed(ctx, *power("vs-03", 3))                    # OFF 확정
    feed(ctx, *power("vs-03", 1180), at=T0 + 100)    # ON 후보

    st = world.sensor("vs-03")
    assert st.state == "OFF"
    assert st.pending_state == "ON"
    assert sched.pending() == 1

    clock.advance_to(T0 + 115)                       # min_hold_sec = 15
    sched.run_due()
    assert st.state == "ON"
    assert st.pending_state is None


def test_changed_at_is_candidate_time_not_confirm_time(ctx):
    """t0 가 밀리면 KDE 분포가 통째로 틀어진다."""
    clock, sched, world = ctx
    feed(ctx, *power("vs-03", 3))
    feed(ctx, *power("vs-03", 1180), at=T0 + 100)
    clock.advance_to(T0 + 115)
    sched.run_due()
    assert world.sensor("vs-03").changed_at == T0 + 100      # 확정(115)이 아니라 후보(100)


def test_spike_is_filtered(ctx):
    """min_hold 안에 조건이 깨지면 후보를 버린다."""
    clock, sched, world = ctx
    feed(ctx, *power("vs-03", 3))
    feed(ctx, *power("vs-03", 1180), at=T0 + 100)    # ON 후보
    feed(ctx, *power("vs-03", 4), at=T0 + 105)       # 5초 만에 꺼짐

    st = world.sensor("vs-03")
    assert st.pending_state is None
    assert sched.pending() == 0

    clock.advance_to(T0 + 200)
    sched.run_due()
    assert st.state == "OFF"                         # 확정되지 않았다


def test_hysteresis_keeps_on_between_thresholds(ctx):
    """on_exit_w(150) <= watt < on_min_w(300) 구간에서는 현재 상태를 유지한다."""
    clock, sched, world = ctx
    feed(ctx, *power("vs-03", 3))
    feed(ctx, *power("vs-03", 1180), at=T0 + 100)
    clock.advance_to(T0 + 115)
    sched.run_due()
    assert world.sensor("vs-03").state == "ON"

    feed(ctx, *power("vs-03", 200), at=T0 + 200)     # 150 <= 200 < 300
    clock.advance_to(T0 + 300)
    sched.run_due()
    assert world.sensor("vs-03").state == "ON"       # 내려가지 않는다


def test_hysteresis_blocks_on_below_on_min(ctx):
    """꺼진 상태에서 200W 는 ON 이 아니다. 같은 값이라도 방향에 따라 다르다."""
    clock, sched, world = ctx
    feed(ctx, *power("vs-03", 3))
    feed(ctx, *power("vs-03", 200), at=T0 + 100)
    clock.advance_to(T0 + 200)
    sched.run_due()
    assert world.sensor("vs-03").state == "STANDBY"


def test_node_supplied_state_skips_classification(ctx):
    """명세: 노드가 판정 가능하면 채우고, RPi5 는 그대로 쓴다."""
    _, sched, world = ctx
    feed(ctx, *power("vs-03", 3, state="ON"))        # watt 로는 OFF 인데
    st = world.sensor("vs-03")
    assert st.state == "ON"                          # 노드 값이 이긴다
    assert sched.pending() == 0                      # min_hold 도 없다


def test_any_power_on_by_role(ctx):
    clock, sched, world = ctx
    assert world.any_power_on("MEAL") is False
    feed(ctx, *power("vs-03", 1180, state="ON"))
    assert world.any_power_on("MEAL") is True
    assert world.any_power_on("SLEEP") is False


def test_power_state_of_unknown_sensor(ctx):
    _, _, world = ctx
    assert world.power_state("vs-99") == "OFF"


# ============================================================ 타입별 상태


def test_motion_transition(ctx):
    clock, _, world = ctx
    feed(ctx, *sensor("vs-04", type="motion", motion=True, confidence="low"))
    st = world.sensor("vs-04")
    assert isinstance(st, MotionState) and st.motion is True
    clock.advance_to(T0 + 60)
    assert st.since_sec(clock.now()) == 60.0


def test_door_open_duration(ctx):
    clock, _, world = ctx
    feed(ctx, *sensor("vs-06", type="door", open=True))
    st = world.sensor("vs-06")
    clock.advance_to(T0 + 14)
    assert st.open_sec(clock.now()) == 14.0

    feed(ctx, *sensor("vs-06", type="door", open=False), at=T0 + 14)
    assert st.open_sec(clock.now()) == 0.0      # 닫혀 있으면 0


def test_bed_occupied_duration(ctx):
    clock, _, world = ctx
    feed(ctx, *sensor("vs-05", type="bed", occupied=True))
    clock.advance_to(T0 + 3600)
    assert world.sensor("vs-05").occupied_sec(clock.now()) == 3600.0


def test_light_has_no_changed_at(ctx):
    """연속값이라 전환 개념이 없다."""
    _, _, world = ctx
    feed(ctx, *sensor("vs-07", type="light", illuminance_lux=12))
    st = world.sensor("vs-07")
    assert isinstance(st, LightState)
    assert st.lux == 12.0
    assert not hasattr(st, "changed_at")


def test_history_is_bounded(ctx):
    _, _, world = ctx
    for i in range(50):
        feed(ctx, *presence("vs-01", i % 2 == 0), at=T0 + i * 10)
    assert len(world.sensor("vs-01").history) == 30


# ============================================================ 가전


def test_device_secondary_field_does_not_reset(ctx):
    """볼륨 조절로 changed_at 이 리셋되면 수면 판정이 망가진다."""
    clock, _, world = ctx
    tv = lambda **kw: ("hestia/device/vd-01/state", {
        "version": 1, "sent_ts": 0, "src_id": "vd-01", "seq": 1,
        "device_type": "smart_tv", "source": "mock", **kw
    })
    feed(ctx, *tv(power="ON", volume=12))
    feed(ctx, *tv(power="ON", volume=30), at=T0 + 600)
    st = world.device("vd-01")
    assert st.changed_at == T0
    assert st.get("volume") == 30
    assert st.state_sec(clock.now()) == 600.0


def test_washer_primary_field_is_cycle(ctx):
    """세탁기는 power 가 계속 ON 이고 의미 있는 변화는 cycle 이다."""
    _, _, world = ctx
    washer = lambda **kw: ("hestia/device/vd-02/state", {
        "version": 1, "sent_ts": 0, "src_id": "vd-02", "seq": 1,
        "device_type": "washer", "source": "mock", **kw
    })
    feed(ctx, *washer(power="ON", cycle="WASH"))
    feed(ctx, *washer(power="ON", cycle="RINSE"), at=T0 + 600)
    assert world.device("vd-02").changed_at == T0 + 600


def test_primary_field_helper():
    assert _primary("smart_fridge", {"door": "OPEN"}) == "OPEN"
    assert _primary("robot_cleaner", {"status": "CLEANING"}) == "CLEANING"
    assert _primary("smart_tv", {}) == ""


def test_device_event_records_last_time(ctx):
    clock, _, world = ctx
    feed(ctx, "hestia/device/vd-01/event", {
        "version": 1, "sent_ts": 0, "src_id": "vd-01", "seq": 1,
        "device_type": "smart_tv", "source": "mock",
        "event_type": "remote_input", "button": "VOL_UP",
    })
    clock.advance_to(T0 + 7200)
    assert world.since_device_event("vd-01", "remote_input") == 7200.0


def test_since_event_none_when_never_happened(ctx):
    """'방금 눌렀다(0초)'와 '누른 적 없다'는 다르다."""
    _, _, world = ctx
    feed(ctx, "hestia/device/vd-01/state", {
        "version": 1, "sent_ts": 0, "src_id": "vd-01", "seq": 1,
        "device_type": "smart_tv", "source": "mock", "power": "ON",
    })
    assert world.since_device_event("vd-01", "remote_input") is None
    assert world.since_device_event("vd-99", "remote_input") is None


def test_event_type_derivation():
    from hestia_engine.messages import (
        CleaningFinishedEvent,
        DispensedEvent,
        DoorOpenedEvent,
        RemoteInputEvent,
    )
    base = dict(recv_ts=0.0, src_id="x", sent_ts=0.0)
    assert _event_type(RemoteInputEvent(**base)) == "remote_input"
    assert _event_type(DoorOpenedEvent(**base)) == "door_opened"
    assert _event_type(DispensedEvent(**base)) == "dispensed"
    assert _event_type(CleaningFinishedEvent(**base)) == "cleaning_finished"


def test_device_optional_fields_omitted(ctx):
    """Mock 이 시뮬레이션 안 한 필드는 없는 것으로 둔다."""
    _, _, world = ctx
    feed(ctx, "hestia/device/vd-01/state", {
        "version": 1, "sent_ts": 0, "src_id": "vd-01", "seq": 1,
        "device_type": "smart_tv", "source": "mock", "power": "ON",
    })
    assert "volume" not in world.device("vd-01").fields


# ============================================================ 필터링


def test_disabled_sensor_is_ignored(ctx):
    _, _, world = ctx
    feed(ctx, *presence("vs-99", True))
    assert world.sensor("vs-99") is None


def test_unknown_sensor_is_ignored(ctx):
    """설정에 없는 ID — registry 로 제거됐거나 다른 집 센서다."""
    _, _, world = ctx
    feed(ctx, *presence("vs-77", True))
    assert world.sensor("vs-77") is None


def test_sensors_of_area_only_includes_received(ctx):
    """설정에는 4개지만 값이 들어온 것만 돌려준다."""
    _, _, world = ctx
    feed(ctx, *presence("vs-02", True))
    assert len(world.sensors_of("kitchen")) == 1


# ============================================================ 파생


def test_dwell_uses_oldest_sensor(ctx):
    """구역에 센서가 여럿이어도 체류는 하나 — 먼저 반응한 쪽 기준."""
    clock, _, world = ctx
    feed(ctx, *presence("vs-01", True))
    # living 에 presence 가 하나뿐이므로 값 확인만
    clock.advance_to(T0 + 500)
    assert world.dwell_sec("living") == 500.0
    assert world.dwell_sec("bedroom") == 0.0


def test_since_any_event_takes_most_recent(ctx):
    clock, _, world = ctx
    feed(ctx, "hestia/device/vd-01/event", {
        "version": 1, "sent_ts": 0, "src_id": "vd-01", "seq": 1,
        "device_type": "smart_tv", "source": "mock",
        "event_type": "remote_input", "button": "OK",
    })
    clock.advance_to(T0 + 100)
    assert world.since_any_event("smart_tv", "remote_input") == 100.0
    assert world.since_any_event("smart_fridge", "door_opened") is None


# ============================================================ 노드 LWT


def test_node_status_tracked(ctx):
    _, _, world = ctx
    feed(ctx, "hestia/node/esp32-1/status", {
        "version": 1, "sent_ts": 0, "src_id": "esp32-1",
        "online": True, "ts_synced": True,
    })
    assert world.nodes == {"esp32-1": True}
    assert world.offline_nodes() == ()


def test_offline_node_marks_its_sensors(ctx):
    """센서 고장을 부재로 오인하지 않기 위한 안전장치."""
    _, _, world = ctx
    feed(ctx, "hestia/node/esp32-2/status", {
        "version": 1, "sent_ts": 0, "src_id": "esp32-2",
        "online": False, "ts_synced": False,
    })
    assert world.offline_nodes() == ("esp32-2",)
    assert set(world.sensors_offline()) == {"vs-02", "vs-03", "vs-04"}


def test_sensor_without_node_not_reported_offline(ctx):
    """node 는 선택 필드다. None 이 죽은 노드로 잡히면 안 된다."""
    _, _, world = ctx
    feed(ctx, "hestia/node/esp32-2/status", {
        "version": 1, "sent_ts": 0, "src_id": "esp32-2",
        "online": False, "ts_synced": False,
    })
    assert "vs-07" not in world.sensors_offline()     # node 없음


# ============================================================ 실제 시나리오


def test_morning_scenario():
    """tests/data/morning.jsonl 전체를 흘려보낸 결과."""
    from hestia_engine.replay import Replay

    clock = ReplayClock(0)
    cfg = load_default()
    sched = Scheduler(clock)
    world = WorldState(clock, cfg, sched)

    class Feed:
        def ingest(self, topic, payload):
            msg = parse(topic, payload, clock.now())
            if msg:
                world.apply(msg)

    path = Path(__file__).parent / "data" / "morning.jsonl"
    result = Replay(clock, sched, Feed()).run(path)

    assert result.lines == 22
    assert result.timers_fired == 2                  # 인덕션 ON/OFF 확정

    induction = world.sensor("vs-06")
    assert induction.state == "OFF"                  # 3W <= standby_max_w(10)
    assert induction.changed_at == 1790296500.0      # 09:35 — 후보가 된 시각

    assert world.dwell_sec("kitchen") == 1790297100.0 - 1790295010.0
    assert world.device("vd-01").get("power") == "OFF"
    assert world.nodes == {"esp32-1": True}