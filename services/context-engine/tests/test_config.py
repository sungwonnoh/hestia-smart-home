from pathlib import Path

import pytest

from hestia_engine.config import (
    Config,
    ConfigError,
    DeviceConfig,
    SensorConfig,
    load,
    load_default,
)
from hestia_engine.messages import RegistryEntry

HOME = """
name = "test"
areas = ["living", "kitchen", "bedroom"]
roles = ["LIVING", "SLEEP", "MEAL"]

[[sensors]]
id = "vs-01"
type = "presence"
area = "living"
roles = ["LIVING"]

[[sensors]]
id = "vs-02"
type = "power"
area = "kitchen"
roles = ["MEAL"]
power_profile = "induction"

[[sensors]]
id = "vs-03"
type = "power"
area = "kitchen"
roles = ["MEAL"]

[[sensors]]
id = "vs-04"
type = "bed"
area = "bedroom"
roles = ["SLEEP"]
enabled = false

[[devices]]
id = "vd-01"
device_type = "smart_tv"
area = "living"
channel = true

[[devices]]
id = "vd-02"
device_type = "air_conditioner"
area = "living"

[[devices]]
id = "vd-03"
device_type = "smart_fridge"
area = "kitchen"
channel = true
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

[activity.meal]
session_timeout_sec = 7200

[notify.default]
requires_ack = false
ack_deadline_sec = 600
cooldown_sec = 1800

[notify.SAFETY]
requires_ack = true
ack_deadline_sec = 120

[observe.default]
window_sec = 600

[observe.smart_light]
window_sec = 180

[profile.DEMO]
cooldown_scale = 0.1
timeout_scale = 0.1
observe_scale = 0.1
"""


@pytest.fixture
def cfg(tmp_path) -> Config:
    return _build(tmp_path, HOME, POLICY)


def _build(tmp_path: Path, home: str, policy: str) -> Config:
    h = tmp_path / "home.toml"
    p = tmp_path / "policy.toml"
    h.write_text(home, encoding="utf-8")
    p.write_text(policy, encoding="utf-8")
    return load(h, p)


# ============================================================ 개별 조회


def test_area_of_sensor_and_device(cfg):
    assert cfg.area_of("vs-01") == "living"
    assert cfg.area_of("vd-03") == "kitchen"
    assert cfg.area_of("vs-99") is None


def test_roles_of(cfg):
    assert cfg.roles_of("vs-02") == ("MEAL",)
    assert cfg.roles_of("vd-01") == ()          # 가전은 roles 없음


def test_sensor_and_device_lookup(cfg):
    assert isinstance(cfg.sensor("vs-01"), SensorConfig)
    assert isinstance(cfg.device("vd-01"), DeviceConfig)
    assert cfg.sensor("vd-01") is None          # 가전은 sensor 조회에 안 걸림


def test_is_enabled_and_is_known(cfg):
    assert cfg.is_enabled("vs-01") is True
    assert cfg.is_enabled("vs-04") is False     # enabled = false
    assert cfg.is_enabled("vs-99") is False     # 모르는 ID
    assert cfg.is_known("vs-04") is True        # 알지만 비활성
    assert cfg.is_known("vs-99") is False


# ============================================================ 역방향 조회


def test_sensors_in_area(cfg):
    assert cfg.sensors_in("kitchen") == ("vs-02", "vs-03")
    assert cfg.sensors_in("bathroom") == ()     # 없는 area


def test_disabled_sensor_excluded_from_index(cfg):
    """enabled=false 는 조회에서 빠진다. 판단에 들어가면 안 된다."""
    assert cfg.sensors_in("bedroom") == ()
    assert cfg.sensors_with_role("SLEEP") == ()


def test_sensors_with_role(cfg):
    assert cfg.sensors_with_role("MEAL") == ("vs-02", "vs-03")
    assert cfg.sensors_with_role("FOCUS") == ()


def test_devices_in_area(cfg):
    assert cfg.devices_in("living") == ("vd-01", "vd-02")


def test_channels_filters_by_flag(cfg):
    assert cfg.channels("living") == ("vd-01",)     # vd-02 는 channel=false
    assert set(cfg.channels()) == {"vd-01", "vd-03"}


# ============================================================ 두 파일 가로지르기


def test_power_rule_uses_label(cfg):
    rule = cfg.power_rule("vs-02")
    assert rule.on_min_w == 300.0                   # [power.induction]
    assert rule.standby_max_w == 10.0


def test_power_rule_falls_back_to_default(cfg):
    rule = cfg.power_rule("vs-03")                  # power_profile 없음
    assert rule.on_min_w == 50.0


def test_unknown_label_warns_and_falls_back(tmp_path, caplog):
    home = HOME.replace('power_profile = "induction"', 'power_profile = "kettle"')
    cfg = _build(tmp_path, home, POLICY)
    with caplog.at_level("WARNING"):
        rule = cfg.power_rule("vs-02")
    assert rule.on_min_w == 50.0
    assert any("kettle" in r.getMessage() for r in caplog.records)


def test_power_rule_for_unknown_sensor(cfg):
    assert cfg.power_rule("vs-99").on_min_w == 50.0


# ============================================================ 정책 조회


def test_timeout_path(cfg):
    assert cfg.timeout("activity", "meal", "session_timeout_sec") == 7200.0


def test_timeout_missing_path_raises(cfg):
    with pytest.raises(ConfigError):
        cfg.timeout("activity", "nope", "x")


def test_value_returns_default(cfg):
    assert cfg.value("notify", "default", "requires_ack") is False
    assert cfg.value("nothing", "here", default=42) == 42


def test_observe_window_falls_back(cfg):
    assert cfg.observe_window("smart_light") == 180.0
    assert cfg.observe_window("washer") == 600.0        # default


def test_notify_policy_merges_default(cfg):
    safety = cfg.notify_policy("SAFETY")
    assert safety["requires_ack"] is True               # SAFETY 가 덮어씀
    assert safety["ack_deadline_sec"] == 120.0
    assert safety["cooldown_sec"] == 1800.0            # default 에서 상속


def test_notify_policy_unknown_scenario_is_default(cfg):
    assert cfg.notify_policy("NOPE")["ack_deadline_sec"] == 600.0


# ============================================================ 프로파일


def test_demo_profile_scales_time_values(cfg):
    assert cfg.timeout("activity", "meal", "session_timeout_sec") == 7200.0
    cfg.set_profile("DEMO")
    assert cfg.timeout("activity", "meal", "session_timeout_sec") == 720.0
    assert cfg.notify_policy("SAFETY")["ack_deadline_sec"] == 12.0
    assert cfg.observe_window("smart_light") == 18.0
    assert cfg.power_rule("vs-02").min_hold_sec == 1.5


def test_demo_does_not_scale_non_time_values(cfg):
    cfg.set_profile("DEMO")
    assert cfg.power_rule("vs-02").on_min_w == 300.0
    assert cfg.notify_policy("SAFETY")["requires_ack"] is True


def test_profile_round_trip(cfg):
    cfg.set_profile("DEMO")
    cfg.set_profile("REAL")
    assert cfg.timeout("activity", "meal", "session_timeout_sec") == 7200.0


def test_unknown_profile_rejected(cfg):
    with pytest.raises(ConfigError):
        cfg.set_profile("TEST")


# ============================================================ 기동 거부


def _expect_error(tmp_path, home=HOME, policy=POLICY):
    with pytest.raises(ConfigError):
        _build(tmp_path, home, policy)


def test_unknown_area_rejected(tmp_path):
    _expect_error(tmp_path, HOME.replace('area = "living"', 'area = "livng"', 1))


def test_unknown_role_rejected(tmp_path):
    _expect_error(tmp_path, HOME.replace('roles = ["LIVING"]', 'roles = ["LIVNG"]', 1))


def test_unknown_sensor_type_rejected(tmp_path):
    _expect_error(tmp_path, HOME.replace('type = "presence"', 'type = "radar"'))


def test_unknown_device_type_rejected(tmp_path):
    _expect_error(tmp_path, HOME.replace('device_type = "smart_tv"', 'device_type = "toaster"'))


def test_unknown_source_rejected(tmp_path):
    _expect_error(tmp_path, HOME + '\nsource = "aws"\n')


def test_duplicate_id_rejected(tmp_path):
    dup = HOME + """
[[sensors]]
id = "vs-01"
type = "motion"
area = "kitchen"
roles = ["MEAL"]
"""
    _expect_error(tmp_path, dup)


def test_missing_id_rejected(tmp_path):
    _expect_error(tmp_path, HOME.replace('id = "vs-01"', 'type_only = true'))


def test_missing_roles_rejected(tmp_path):
    _expect_error(tmp_path, HOME.replace('roles = ["LIVING"]\n', "", 1))


def test_power_profile_on_non_power_sensor_rejected(tmp_path):
    bad = HOME.replace(
        'id = "vs-01"\ntype = "presence"',
        'id = "vs-01"\ntype = "presence"\npower_profile = "induction"',
    )
    _expect_error(tmp_path, bad)


def test_policy_without_power_default_rejected(tmp_path):
    _expect_error(tmp_path, policy=POLICY.replace("[power.default]", "[power.other]"))


def test_missing_file_rejected(tmp_path):
    with pytest.raises(ConfigError):
        load(tmp_path / "nope.toml", tmp_path / "also-nope.toml")


def test_broken_toml_rejected(tmp_path):
    _expect_error(tmp_path, "this is not = = toml")


def test_empty_areas_rejected(tmp_path):
    _expect_error(tmp_path, HOME.replace('areas = ["living", "kitchen", "bedroom"]', "areas = []"))


# ============================================================ registry


def _entry(**kw) -> RegistryEntry:
    return RegistryEntry(**kw)


def test_registry_replaces_wholesale(cfg):
    """부분 갱신이 아니라 전체 교체. 사용자가 센서를 추가·삭제할 수 있다."""
    cfg.apply_registry([
        _entry(virtual_id="vs-01", type="presence", area="kitchen", roles=("MEAL",)),
    ])
    assert cfg.area_of("vs-01") == "kitchen"        # living -> kitchen
    assert cfg.sensor("vs-02") is None              # 목록에 없으면 사라짐
    assert cfg.sensors_in("living") == ()


def test_registry_can_add_unknown_sensor(cfg):
    """TOML 에 없던 센서도 받는다."""
    cfg.apply_registry([
        _entry(virtual_id="vs-90", type="motion", area="bedroom", roles=("SLEEP",)),
    ])
    assert cfg.sensors_with_role("SLEEP") == ("vs-90",)


def test_registry_preserves_power_profile(cfg):
    """registry 에 power_profile 이 비어 있으면 기존 값을 살린다."""
    cfg.apply_registry([
        _entry(virtual_id="vs-02", type="power", area="kitchen", roles=("MEAL",)),
    ])
    assert cfg.power_rule("vs-02").on_min_w == 300.0


def test_registry_power_profile_overrides(cfg):
    cfg.apply_registry([
        _entry(virtual_id="vs-02", type="power", area="kitchen",
               roles=("MEAL",), power_profile="unknown-label"),
    ])
    assert cfg.power_rule("vs-02").on_min_w == 50.0


def test_registry_skips_unknown_area(cfg):
    cfg.apply_registry([
        _entry(virtual_id="vs-01", type="presence", area="garage", roles=("LIVING",)),
        _entry(virtual_id="vs-02", type="power", area="kitchen", roles=("MEAL",)),
    ])
    assert cfg.is_known("vs-01") is False
    assert cfg.is_known("vs-02") is True


def test_registry_filters_unknown_roles(cfg):
    cfg.apply_registry([
        _entry(virtual_id="vs-01", type="presence", area="living",
               roles=("LIVING", "NOPE")),
    ])
    assert cfg.roles_of("vs-01") == ("LIVING",)


def test_registry_handles_devices(cfg):
    cfg.apply_registry([
        _entry(virtual_id="vd-01", device_type="smart_tv", area="living", channel=True),
        _entry(virtual_id="vd-02", device_type="smart_light", area="bedroom", channel=False),
    ])
    assert cfg.channels("living") == ("vd-01",)
    assert cfg.devices_in("bedroom") == ("vd-02",)


def test_registry_respects_enabled(cfg):
    """명세: enabled=false 로 '이 집엔 정수기가 없다'를 표현."""
    cfg.apply_registry([
        _entry(virtual_id="vd-03", device_type="water_purifier",
               area="kitchen", enabled=False),
    ])
    assert cfg.is_known("vd-03") is True
    assert cfg.is_enabled("vd-03") is False
    assert cfg.devices_in("kitchen") == ()


def test_registry_keeps_policy_and_profile(cfg):
    cfg.set_profile("DEMO")
    cfg.apply_registry([
        _entry(virtual_id="vs-01", type="presence", area="living", roles=("LIVING",)),
    ])
    assert cfg.profile == "DEMO"
    assert cfg.timeout("activity", "meal", "session_timeout_sec") == 720.0


# ============================================================ 실제 파일


def test_real_demo_config_loads():
    """config/homes/demo.toml 과 policy.toml 이 스키마를 만족하는지."""
    cfg = load_default()
    assert cfg.name == "demo"
    assert len(cfg.all_sensors()) == 16
    assert len(cfg.all_devices()) == 9
    assert cfg.power_rule("vs-06").on_min_w == 300.0      # 인덕션
    assert "vd-01" in cfg.channels("living")