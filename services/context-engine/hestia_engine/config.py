"""설정 로더.

두 파일을 읽어 하나의 Config 로 합친다.

    config/homes/<name>.toml   이 집에 뭐가 어디 있나 (집마다 다름)
    config/policy.toml         어떤 기준으로 판단하나 (공통)

엔진 코드는 센서 ID 를 직접 알지 않는다. area 와 roles 로만 판단하고, 그 매핑을 여기서 제공한다. 
같은 엔진이 Aruba 재생과 실제 집 양쪽에서 도는 근거가 이 층이다.

검증 실패는 기동 거부(잘못된 매핑으로 도는 것보다 안 뜨는 편이 나음)
"""

from __future__ import annotations

import logging
import tomllib      #TOML 설정 파일을 빠르게 읽어 파이썬 딕셔너리로 변환
from dataclasses import dataclass, field, replace
from pathlib import Path        #파일 및 폴더 경로 객체
from typing import Any

log = logging.getLogger(__name__)

# 명세의 센서 7종 / 가전 9종. 오타를 기동 시점에 잡는다.
SENSOR_TYPES = frozenset(
    {"presence", "motion", "door", "power", "bed", "light", "climate"}
)
DEVICE_TYPES = frozenset(
    {
        "smart_tv", "smart_light", "air_conditioner", "air_purifier",
        "smart_fridge", "water_purifier", "washer", "robot_cleaner", "doorbell",
        "display_node",     # ESP32 디스플레이 + 버튼 노드(알림 채널이자 ack 입력 수단)
    }
)
SOURCES = frozenset({"thinq", "mock", "esp32", "tasmota"})


class ConfigError(ValueError):
    """설정이 잘못됨. 기동을 멈춘다."""


# ==================================================================== 항목

"""TOML 파일에서 읽어온 개별 센서와 가전제품의 설정 데이터를 다루기 위한 "불변 데이터 규격(DTO/Value Object)"을 정의"""
@dataclass(frozen=True, slots=True)      #센서 설정 데이터
class SensorConfig:
    id: str
    type: str
    area: str
    roles: tuple[str, ...]
    source: str = "mock"
    node: str | None = None
    power_profile: str | None = None     # 전력 센서만. policy 의 [power.*] 키
    enabled: bool = True
    note: str = ""


@dataclass(frozen=True, slots=True)     #가전 설정 데이터
class DeviceConfig:
    id: str
    device_type: str
    area: str
    source: str = "mock"
    channel: bool = False                # 알림 채널로 쓸 수 있나
    enabled: bool = True
    note: str = ""


@dataclass(frozen=True, slots=True)
class PowerRule:
    """watt → OFF/STANDBY/ON 판정 기준.

    on_min_w 와 on_exit_w 를 다르게 두는 것이 히스테리시스다.
    경계가 하나면 그 근처에서 상태가 초마다 뒤집힌다.
    """

    standby_max_w: float
    on_min_w: float
    on_exit_w: float
    min_hold_sec: float


# ==================================================================== Config


class Config:
    """집 배치 + 공통 정책. 엔진이 설정을 읽는 유일한 통로."""

    def __init__(
        self,
        *,
        name: str,
        areas: tuple[str, ...],
        roles: tuple[str, ...],
        sensors: tuple[SensorConfig, ...],
        devices: tuple[DeviceConfig, ...],
        policy: dict[str, Any],
    ) -> None:
        self.name = name
        self.areas = areas
        self.roles = roles
        self.policy = policy
        self._profile = "REAL"
        self._install(sensors, devices)

    # ------------------------------------------------------------ 인덱스

    def _install(
        self, sensors: tuple[SensorConfig, ...], devices: tuple[DeviceConfig, ...]
    ) -> None:
        """조회는 메시지마다 일어난다. 역인덱스를 미리 만들어 순회를 없앤다."""
        self._sensors = {s.id: s for s in sensors}      #센서와 가전 리스트를 받아 {"vs-01": SensorConfig, ...} 형태의 딕셔너리로 변환
        self._devices = {d.id: d for d in devices}

        """역방향 조회를 위한 임시 딕셔너리 준비
        예: {
            "livingroom": ["vs-01", "motion-01"],
             "bedroom": ["bed-sensor-01"],
            "kitchen": ["door-kitchen"]
            }
        """
        by_area: dict[str, list[str]] = {}              #구역별 센서 ID 목록
        by_role: dict[str, list[str]] = {}              #역할별 센서 ID 목록
        dev_by_area: dict[str, list[str]] = {}          #구역별 가전 ID 목록

        """활성화된(enabled=True) 항목만 분류 및 그룹화"""
        for s in sensors:
            if not s.enabled:
                continue
            by_area.setdefault(s.area, []).append(s.id)         #해당 키가 딕셔너리에 없으면 빈 리스트 []를 새로 만든 뒤 append로 ID를 집어넣음
            for role in s.roles:
                by_role.setdefault(role, []).append(s.id)

        for d in devices:
            if not d.enabled:
                continue
            dev_by_area.setdefault(d.area, []).append(d.id)

        """임시로 작성한 리스트(list)들을 모두 수정 불가능한 튜플(tuple)로 변환하여 내부 변수에 저장"""
        self._by_area = {k: tuple(v) for k, v in by_area.items()}
        self._by_role = {k: tuple(v) for k, v in by_role.items()}
        self._dev_by_area = {k: tuple(v) for k, v in dev_by_area.items()}

    # ------------------------------------------------------------ 개별 조회

    def sensor(self, vid: str) -> SensorConfig | None:
        return self._sensors.get(vid)

    def device(self, vid: str) -> DeviceConfig | None:
        return self._devices.get(vid)

    def area_of(self, vid: str) -> str | None:
        entry = self._sensors.get(vid) or self._devices.get(vid)
        return entry.area if entry else None

    def roles_of(self, vid: str) -> tuple[str, ...]:
        sensor = self._sensors.get(vid)
        return sensor.roles if sensor else ()

    def is_enabled(self, vid: str) -> bool:
        """설정에 없는 ID 는 비활성으로 본다.
        registry 로 제거됐거나 다른 집의 센서가 섞여 들어온 경우-> 판단에 넣지 않음
        """
        entry = self._sensors.get(vid) or self._devices.get(vid)
        return bool(entry and entry.enabled)

    def is_known(self, vid: str) -> bool:
        return vid in self._sensors or vid in self._devices

    # ------------------------------------------------------------ 역방향 조회

    def sensors_in(self, area: str) -> tuple[str, ...]:
        return self._by_area.get(area, ())

    def sensors_with_role(self, role: str) -> tuple[str, ...]:
        return self._by_role.get(role, ())

    def devices_in(self, area: str) -> tuple[str, ...]:
        return self._dev_by_area.get(area, ())

    def channels(self, area: str | None = None) -> tuple[str, ...]:
        """알림 채널 후보.

        명세의 채널 선택 ①단계 — device_type 이 affinity 에 포함되고
        area 가 presence.area 와 일치하는 것.
        """
        ids = self._dev_by_area.get(area, ()) if area else tuple(self._devices)
        return tuple(
            vid
            for vid in ids
            if (d := self._devices.get(vid)) and d.channel and d.enabled        #self._devices 딕셔너리에서 가전 객체(DeviceConfig)를 찾아 변수 d에 할당
        )

    def all_sensors(self) -> tuple[SensorConfig, ...]:
        return tuple(self._sensors.values())

    def all_devices(self) -> tuple[DeviceConfig, ...]:
        return tuple(self._devices.values())

    # ------------------------------------------------------ 두 파일 가로지르기

    def power_rule(self, vid: str) -> PowerRule:
        """센서 ID → power_profile 라벨 → policy 의 임계값.

        호출부는 이 두 단계를 몰라도 된다.
        라벨이 없거나 policy 에 그 라벨이 없으면 default 로 떨어진다.
        """
        table = self.policy.get("power", {})
        sensor = self._sensors.get(vid)
        label = sensor.power_profile if sensor else None

        rule = table.get(label) if label else None
        if label and rule is None:
            log.warning(
                "power_profile %r 이 policy 에 없음 (센서 %s). default 사용.", label, vid
            )
        if rule is None:
            rule = table.get("default")
        if rule is None:
            raise ConfigError("policy 에 [power.default] 가 없음")

        return PowerRule(
            standby_max_w=float(rule["standby_max_w"]),
            on_min_w=float(rule["on_min_w"]),
            on_exit_w=float(rule["on_exit_w"]),
            min_hold_sec=float(rule["min_hold_sec"]) * self._time_scale(),
        )

    # ------------------------------------------------------------ 정책 조회
    """policy.toml에 정의된 정책 및 수치들을 조회하고, 설정된 프로파일(REAL 또는 DEMO)의 배수 스케일까지 적용해 반환"""
    def observe_window(self, device_type: str) -> float:                #가전 기기의 관측/감시 시간 창 세컨드를 조회
        table = self.policy.get("observe", {})
        entry = table.get(device_type) or table.get("default") or {"window_sec": 600}
        return float(entry["window_sec"]) * self._observe_scale()       #설정된 값에 프로파일의 관측 배수(_observe_scale())를 곱해 반환

    def notify_policy(self, scenario: str) -> dict[str, Any]:
        """시나리오별 알림 정책. default 위에 시나리오 항목을 얹는다."""
        table = self.policy.get("notify", {})
        merged = dict(table.get("default", {}))
        merged.update(table.get(scenario, {}))

        scale = self._time_scale()
        for key in ("ack_deadline_sec", "cooldown_sec", "expiry_sec"):
            if key in merged:
                merged[key] = float(merged[key]) * scale

        #"comply_window_min"은 분 단위라 따로 처리
        if "comply_window_min" in merged:
            merged["comply_window_min"] = float(merged["comply_window_min"]) * scale
            
        return merged

    def timeout(self, *path: str) -> float:
        """policy 의 중첩 키를 따라가 시간값을 읽고 프로파일 배수를 적용한다.

            cfg.timeout("activity", "meal", "session_timeout_sec")
        """
        node: Any = self.policy
        for key in path:
            if not isinstance(node, dict) or key not in node:
                raise ConfigError(f"policy 에 없는 경로: {'.'.join(path)}")
            node = node[key]
        return float(node) * self._time_scale()

    def value(self, *path: str, default: Any = None) -> Any:
        """배수를 적용하지 않는 일반 정책값."""
        node: Any = self.policy
        for key in path:
            if not isinstance(node, dict) or key not in node:
                return default
            node = node[key]
        return node

    def publish_interval(self) -> float:
        """context 주기 발행 간격.

        DEMO 배수를 적용하지 않는다 — 5분이 30초가 되면
        로그만 지저분해지고 얻는 게 없다.
        """
        return float(self.value("publish", "interval_sec", default=300))

    # ------------------------------------------------------------ 프로파일

    @property
    def profile(self) -> str:
        return self._profile

    def set_profile(self, profile: str) -> None:
        """REAL / DEMO. DEMO 는 쿨다운·타임아웃·관측 창을 배수로 줄인다."""
        if profile not in ("REAL", "DEMO"):
            raise ConfigError(f"알 수 없는 프로파일: {profile!r}")
        self._profile = profile

    def _scales(self) -> dict[str, float]:
        return self.policy.get("profile", {}).get(self._profile, {})

    def _time_scale(self) -> float:
        s = self._scales()
        return float(s.get("timeout_scale", s.get("cooldown_scale", 1.0)))

    def _observe_scale(self) -> float:
        return float(self._scales().get("observe_scale", 1.0))

    # ------------------------------------------------------------ registry

    def apply_registry(self, entries: Any) -> None:
        """RegistryDevices 의 devices 로 매핑을 전체 교체한다.

        부분 갱신이 아니라 교체인 이유: 사용자가 대시보드에서 센서를 추가·삭제할 수 있으므로 TOML 에 없던 항목이 올 수 있다.
        명세도 devices 배열 전체를 싣는다.

        수신 후 엔진은 모든 context 를 재계산해야 한다 — 변경 전 매핑으로 진행 중이던 판단은 무효다. 그 훅은 호출부가 연결한다.
        """
        sensors: list[SensorConfig] = []
        devices: list[DeviceConfig] = []

        for e in entries:
            if e.area and e.area not in self.areas:
                log.warning("registry 의 알 수 없는 area %r (%s). 건너뜀.", e.area, e.virtual_id)
                continue

            if e.type:
                if e.type not in SENSOR_TYPES:
                    log.warning("registry 의 알 수 없는 type %r (%s)", e.type, e.virtual_id)
                    continue
                # registry 에 power_profile 이 비어 있으면 기존 값을 살린다
                prev = self._sensors.get(e.virtual_id)
                sensors.append(
                    SensorConfig(
                        id=e.virtual_id,
                        type=e.type,
                        area=e.area or "",
                        roles=tuple(r for r in e.roles if r in self.roles),
                        source=e.source or "mock",
                        node=e.node_id,
                        power_profile=e.power_profile or (prev.power_profile if prev else None),
                        enabled=e.enabled,
                    )
                )
            elif e.device_type:
                if e.device_type not in DEVICE_TYPES:
                    log.warning(
                        "registry 의 알 수 없는 device_type %r (%s)", e.device_type, e.virtual_id
                    )
                    continue
                devices.append(
                    DeviceConfig(
                        id=e.virtual_id,
                        device_type=e.device_type,
                        area=e.area or "",
                        source=e.source or "mock",
                        channel=e.channel,
                        enabled=e.enabled,
                    )
                )

        self._install(tuple(sensors), tuple(devices))
        log.info("registry 적용: 센서 %d, 가전 %d", len(sensors), len(devices))

    def __repr__(self) -> str:
        return (
            f"Config(name={self.name!r}, profile={self._profile!r}, "
            f"sensors={len(self._sensors)}, devices={len(self._devices)})"
        )


# ==================================================================== 로딩


def load(home_path: Path, policy_path: Path) -> Config:
    """두 파일을 읽어 검증하고 Config 를 만든다. 실패하면 ConfigError."""
    home = _read_toml(home_path)
    policy = _read_toml(policy_path)

    areas = tuple(_req_list(home, "areas", home_path))
    roles = tuple(_req_list(home, "roles", home_path))

    sensors = tuple(
        _sensor(item, areas, roles, home_path) for item in home.get("sensors", [])
    )
    devices = tuple(_device(item, areas, home_path) for item in home.get("devices", []))

    _no_duplicates(sensors, devices, home_path)

    if "power" not in policy or "default" not in policy["power"]:
        raise ConfigError(f"{policy_path}: [power.default] 가 필요함")

    cfg = Config(
        name=str(home.get("name", home_path.stem)),
        areas=areas,
        roles=roles,
        sensors=sensors,
        devices=devices,
        policy=policy,
    )
    log.info("설정 로드: %s", cfg)
    return cfg


def load_default(root: Path | None = None, home: str = "demo") -> Config:
    """레포 루트 기준 기본 경로로 읽는다."""
    root = root or _find_repo_root()
    return load(root / "config" / "homes" / f"{home}.toml", root / "config" / "policy.toml")


def _find_repo_root() -> Path:
    """이 파일 기준으로 config/ 를 가진 상위 디렉터리를 찾는다."""
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "config" / "policy.toml").exists():
            return parent
    raise ConfigError("config/policy.toml 을 찾을 수 없음")


# ---------------------------------------------------------------- 검증


def _read_toml(path: Path) -> dict:
    if not path.exists():
        raise ConfigError(f"설정 파일 없음: {path}")
    try:
        with path.open("rb") as fp:
            return tomllib.load(fp)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path}: TOML 문법 오류 — {exc}") from exc


def _req_list(table: dict, key: str, path: Path) -> list[str]:
    value = table.get(key)
    if not isinstance(value, list) or not value:
        raise ConfigError(f"{path}: {key} 가 비었거나 배열이 아님")
    return [str(v) for v in value]


def _req_str(item: dict, key: str, path: Path, where: str) -> str:
    value = item.get(key)
    if not isinstance(value, str) or not value:
        raise ConfigError(f"{path}: {where} 에 {key} 가 없음")
    return value


def _sensor(
    item: dict, areas: tuple[str, ...], roles: tuple[str, ...], path: Path
) -> SensorConfig:
    vid = _req_str(item, "id", path, "sensors 항목")
    stype = _req_str(item, "type", path, f"센서 {vid}")
    area = _req_str(item, "area", path, f"센서 {vid}")

    if stype not in SENSOR_TYPES:
        raise ConfigError(f"{path}: 센서 {vid} 의 알 수 없는 type {stype!r}")
    if area not in areas:
        raise ConfigError(f"{path}: 센서 {vid} 의 area {area!r} 가 areas 목록에 없음")

    item_roles = item.get("roles", [])
    if not isinstance(item_roles, list) or not item_roles:
        raise ConfigError(f"{path}: 센서 {vid} 에 roles 가 없음")
    for role in item_roles:
        if role not in roles:
            raise ConfigError(f"{path}: 센서 {vid} 의 role {role!r} 가 roles 목록에 없음")

    source = item.get("source", "mock")
    if source not in SOURCES:
        raise ConfigError(f"{path}: 센서 {vid} 의 알 수 없는 source {source!r}")

    if stype != "power" and item.get("power_profile"):
        raise ConfigError(f"{path}: 센서 {vid} 는 power 가 아닌데 power_profile 이 있음")

    return SensorConfig(
        id=vid,
        type=stype,
        area=area,
        roles=tuple(str(r) for r in item_roles),
        source=str(source),
        node=item.get("node"),
        power_profile=item.get("power_profile"),
        enabled=bool(item.get("enabled", True)),
        note=str(item.get("note", "")),
    )


def _device(item: dict, areas: tuple[str, ...], path: Path) -> DeviceConfig:
    vid = _req_str(item, "id", path, "devices 항목")
    dtype = _req_str(item, "device_type", path, f"가전 {vid}")
    area = _req_str(item, "area", path, f"가전 {vid}")

    if dtype not in DEVICE_TYPES:
        raise ConfigError(f"{path}: 가전 {vid} 의 알 수 없는 device_type {dtype!r}")
    if area not in areas:
        raise ConfigError(f"{path}: 가전 {vid} 의 area {area!r} 가 areas 목록에 없음")

    source = item.get("source", "mock")
    if source not in SOURCES:
        raise ConfigError(f"{path}: 가전 {vid} 의 알 수 없는 source {source!r}")

    return DeviceConfig(
        id=vid,
        device_type=dtype,
        area=area,
        source=str(source),
        channel=bool(item.get("channel", False)),
        enabled=bool(item.get("enabled", True)),
        note=str(item.get("note", "")),
    )


def _no_duplicates(
    sensors: tuple[SensorConfig, ...], devices: tuple[DeviceConfig, ...], path: Path
) -> None:
    seen: set[str] = set()
    for entry in (*sensors, *devices):
        if entry.id in seen:
            raise ConfigError(f"{path}: ID 중복 {entry.id!r}")
        seen.add(entry.id)
