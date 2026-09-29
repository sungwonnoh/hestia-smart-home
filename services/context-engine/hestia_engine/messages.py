"""수신 MQTT 메시지의 파싱과 검증

엔진 안쪽에는 검증된 dataclass 만 들여보내도록 (페이로드 딕셔너리를 World State 코드가 직접 뒤지지 않게 하는 것이 목적)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

log = logging.getLogger(__name__)       #현재 모듈(파일) 전용 로거 생성

SCHEMA_VERSION = 1

# version 불일치 경고를 '노드마다 한 번만' 찍기 위한 기억
_warned_versions: set[tuple[str, Any]] = set()

# ==================================================================== 기반

@dataclass(frozen=True, slots=True)
class Message:
    """모든 수신 메시지의 공통 Envelope
    recv_ts 는 RPi5 가 '수신 시점'에 Clock 으로 붙임
    """

    recv_ts: float
    src_id: str
    sent_ts: float
    seq: int | None = None


@dataclass(frozen=True, slots=True)
class SensorMessage(Message):
    """hestia/sensor/{virtual_id}/state"""


@dataclass(frozen=True, slots=True)
class DeviceStateMessage(Message):
    """hestia/device/{virtual_id}/state"""

    device_type: str = ""
    source: str = ""


@dataclass(frozen=True, slots=True)
class DeviceEventMessage(Message):
    """hestia/device/{virtual_id}/event"""

    device_type: str = ""
    source: str = ""


@dataclass(frozen=True, slots=True)
class SystemMessage(Message):
    """노드 관리·모델·프로파일 계열."""


class SchemaError(ValueError):
    """페이로드가 명세를 만족하지 않음. 호출부가 잡아서 버림."""


# ---------------------------------------------------------------- 필드 추출

def _req(payload: dict, key: str, kind: type) -> Any:
    """필수 필드. 없거나 타입이 다르면 SchemaError.
    payload가 JSON을 파싱한 딕셔너리이고, key는 그 안에서 꺼내려는 필드 이름 문자열, kind는 기대하는 타입(value의 타입)
    """
    if key not in payload:
        raise SchemaError(f"필수 필드 누락: {key}")
    value = payload[key]

    if kind is float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise SchemaError(f"{key} 는 숫자여야 함: {value!r}")
        return float(value)
    if kind is int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise SchemaError(f"{key} 는 정수여야 함: {value!r}")
        return value
    if not isinstance(value, kind):
        raise SchemaError(f"{key} 타입 불일치: {value!r} ({kind.__name__} 기대)")
    return value


def _opt(payload: dict, key: str, kind: type, default: Any = None) -> Any:
    """선택 필드. 없으면 default, 있으면 타입 검사 (선택 필드는 없을 수 있으므로 부재 시에도 동작)
    """
    if key not in payload or payload[key] is None:
        return default
    return _req(payload, key, kind)


def _one_of(payload: dict, key: str, allowed: frozenset[str], *, required: bool = True) -> str | None:
    """값이 정해진 목록 안에 있는지 검사
    """
    value = _req(payload, key, str) if required else _opt(payload, key, str)
    if value is not None and value not in allowed:
        raise SchemaError(f"{key} 허용되지 않은 값: {value!r} (가능: {sorted(allowed)})")
    return value


def check_envelope(payload: dict, recv_ts: float, expected_src: str) -> dict:
    """공통 Envelope 를 검증 & 기반 필드를 돌려줌
    """
    version = _req(payload, "version", int)
    if version != SCHEMA_VERSION:
        mark = (expected_src, version)
        if mark not in _warned_versions:
            _warned_versions.add(mark)
            log.warning(
                "스키마 버전 불일치: src=%s version=%s (엔진 기대=%s). "
                "공통 필드만 해석하고 계속 진행함.",
                expected_src, version, SCHEMA_VERSION,
            )

    src_id = _req(payload, "src_id", str)
    if src_id != expected_src:
        raise SchemaError(f"토픽과 src_id 불일치: 토픽={expected_src} payload={src_id}")

    return {
        "recv_ts": recv_ts,
        "src_id": src_id,
        "sent_ts": _req(payload, "sent_ts", float),
        "seq": _opt(payload, "seq", int),
    }


# ==================================================================== 센서

CONFIDENCE = frozenset({"high", "medium", "low"})
POWER_STATE = frozenset({"OFF", "STANDBY", "ON"})


@dataclass(frozen=True, slots=True)
class PresenceMessage(SensorMessage):
    """mmWave. confidence=high — 정지 상태도 감지하므로 Absent 전환을 즉시 신뢰."""

    present: bool = False
    confidence: str = "high"
    energy: int = 0
    distance_cm: int = 0


@dataclass(frozen=True, slots=True)
class MotionMessage(SensorMessage):
    """PIR. confidence=low — 정지한 사람을 놓치므로 무반응 N분 후 이석 판정."""

    motion: bool = False
    confidence: str = "low"


@dataclass(frozen=True, slots=True)
class DoorMessage(SensorMessage):
    """리드 스위치. 개폐 지속은 RPi5 가 recv_ts 차이로 계산."""

    open: bool = False


@dataclass(frozen=True, slots=True)
class PowerMessage(SensorMessage):
    """전력 센서. state 는 선택 — 없으면 RPi5 가 YAML 임계값으로 판정."""

    watt: float = 0.0
    state: str | None = None


@dataclass(frozen=True, slots=True)
class BedMessage(SensorMessage):
    occupied: bool = False


@dataclass(frozen=True, slots=True)
class LightMessage(SensorMessage):
    illuminance_lux: float = 0.0


@dataclass(frozen=True, slots=True)
class ClimateMessage(SensorMessage):
    temperature_c: float = 0.0
    humidity_pct: float = 0.0


def parse_sensor(payload: dict, base: dict) -> SensorMessage:
    """센서 페이로드의 type(센서 종류-mmWave인지, PIR인지,,) 값을 보고, 그에 맞는 dataclass를 만들어 돌려줌
    type 읽기  →  match 분기 (7갈래)  →  해당 dataclass 생성 (모르는 type 이면 SchemaError)
    base: 앞 단계에서 check_envelope가 돌려준 공통 4필드
    """
    sensor_type = _req(payload, "type", str)

    match sensor_type:
        case "presence":
            return PresenceMessage(
                **base,
                present=_req(payload, "present", bool),
                confidence=_one_of(payload, "confidence", CONFIDENCE) or "high",
                energy=_opt(payload, "energy", int, 0),
                distance_cm=_opt(payload, "distance_cm", int, 0),
            )
        case "motion":
            return MotionMessage(
                **base,
                motion=_req(payload, "motion", bool),
                confidence=_one_of(payload, "confidence", CONFIDENCE) or "low",
            )
        case "door":
            return DoorMessage(**base, open=_req(payload, "open", bool))
        case "power":
            return PowerMessage(
                **base,
                watt=_req(payload, "watt", float),
                state=_one_of(payload, "state", POWER_STATE, required=False),
            )
        case "bed":
            return BedMessage(**base, occupied=_req(payload, "occupied", bool))
        case "light":
            return LightMessage(**base, illuminance_lux=_req(payload, "illuminance_lux", float))
        case "climate":
            return ClimateMessage(
                **base,
                temperature_c=_req(payload, "temperature_c", float),
                humidity_pct=_req(payload, "humidity_pct", float),
            )
        case _:
            raise SchemaError(f"알 수 없는 센서 type: {sensor_type!r}")


# ================================================================ 가전 상태

DEVICE_POWER = frozenset({"ON", "OFF", "STANDBY"})
DOOR_STATE = frozenset({"OPEN", "CLOSED"})
AC_MODE = frozenset({"COOL", "HEAT", "DRY", "FAN", "SLEEP", "AUTO"})
PURIFIER_MODE = frozenset({"AUTO", "SILENT", "TURBO"})
WASHER_CYCLE = frozenset({"IDLE", "WASH", "RINSE", "SPIN", "DONE"})
CLEANER_STATUS = frozenset({"DOCKED", "CLEANING", "RETURNING", "PAUSED", "ERROR"})
SOURCE = frozenset({"thinq", "mock", "esp32", "tasmota"})


@dataclass(frozen=True, slots=True)
class SmartTvState(DeviceStateMessage):
    power: str = "OFF"
    volume: int | None = None
    channel: int | None = None


@dataclass(frozen=True, slots=True)
class SmartLightState(DeviceStateMessage):
    power: str = "OFF"
    brightness: int | None = None        # 0~100
    color_temp: int | None = None        # 켈빈


@dataclass(frozen=True, slots=True)
class AirConditionerState(DeviceStateMessage):
    power: str = "OFF"
    mode: str | None = None
    temp_set: float | None = None
    temp_room: float | None = None       # climate 센서와 충돌 시 센서값 우선


@dataclass(frozen=True, slots=True)
class AirPurifierState(DeviceStateMessage):
    power: str = "OFF"
    mode: str | None = None
    pm25: int | None = None


@dataclass(frozen=True, slots=True)
class SmartFridgeState(DeviceStateMessage):
    """power 없음 — 항상 켜져 있음. 생존 확인은 5분 주기 발행으로."""

    door: str = "CLOSED"


@dataclass(frozen=True, slots=True)
class WaterPurifierState(DeviceStateMessage):
    """실제 정보는 dispensed 이벤트에 있음. state 는 생존 신호."""

    power: str = "OFF"


@dataclass(frozen=True, slots=True)
class WasherState(DeviceStateMessage):
    power: str = "OFF"
    cycle: str | None = None
    remain_min: int | None = None
    door: str | None = None


@dataclass(frozen=True, slots=True)
class RobotCleanerState(DeviceStateMessage):
    """schedule 은 예약 시각 문자열('14:00').

    예약 도달은 이벤트로 오지 않는다. RPi5 가 이 값과 현재 시각을 비교해
    스스로 판단한다.
    """

    status: str = "DOCKED"
    battery: int | None = None
    schedule: str | None = None


@dataclass(frozen=True, slots=True)
class DoorbellState(DeviceStateMessage):
    """실제 정보는 ring 이벤트에 있음."""

    power: str = "OFF"


def parse_device_state(payload: dict, base: dict) -> DeviceStateMessage:
    device_type = _req(payload, "device_type", str)
    common = {
        **base,
        "device_type": device_type,
        "source": _one_of(payload, "source", SOURCE) or "",
    }

    match device_type:
        case "smart_tv":
            return SmartTvState(
                **common,
                power=_one_of(payload, "power", DEVICE_POWER),
                volume=_opt(payload, "volume", int),
                channel=_opt(payload, "channel", int),
            )
        case "smart_light":
            return SmartLightState(
                **common,
                power=_one_of(payload, "power", DEVICE_POWER),
                brightness=_opt(payload, "brightness", int),
                color_temp=_opt(payload, "color_temp", int),
            )
        case "air_conditioner":
            return AirConditionerState(
                **common,
                power=_one_of(payload, "power", DEVICE_POWER),
                mode=_one_of(payload, "mode", AC_MODE, required=False),
                temp_set=_opt(payload, "temp_set", float),
                temp_room=_opt(payload, "temp_room", float),
            )
        case "air_purifier":
            return AirPurifierState(
                **common,
                power=_one_of(payload, "power", DEVICE_POWER),
                mode=_one_of(payload, "mode", PURIFIER_MODE, required=False),
                pm25=_opt(payload, "pm25", int),
            )
        case "smart_fridge":
            return SmartFridgeState(**common, door=_one_of(payload, "door", DOOR_STATE))
        case "water_purifier":
            return WaterPurifierState(**common, power=_one_of(payload, "power", DEVICE_POWER))
        case "washer":
            return WasherState(
                **common,
                power=_one_of(payload, "power", DEVICE_POWER),
                cycle=_one_of(payload, "cycle", WASHER_CYCLE, required=False),
                remain_min=_opt(payload, "remain_min", int),
                door=_one_of(payload, "door", DOOR_STATE, required=False),
            )
        case "robot_cleaner":
            return RobotCleanerState(
                **common,
                status=_one_of(payload, "status", CLEANER_STATUS),
                battery=_opt(payload, "battery", int),
                schedule=_opt(payload, "schedule", str),
            )
        case "doorbell":
            return DoorbellState(**common, power=_one_of(payload, "power", DEVICE_POWER))
        case _:
            raise SchemaError(f"알 수 없는 device_type: {device_type!r}")


# ============================================================== 가전 이벤트

WATER_TYPE = frozenset({"COLD", "HOT", "AMBIENT"})


@dataclass(frozen=True, slots=True)
class RemoteInputEvent(DeviceEventMessage):
    """TV 리모컨 입력(수면 판단의 핵심 신호), RPi5 가 recv_ts 를 기억해 무조작 시간을 계산."""

    button: str = ""


@dataclass(frozen=True, slots=True)
class DoorOpenedEvent(DeviceEventMessage):
    """냉장고(vd-05), 세탁기(vd-07) 공용. src_id 로 분기"""


@dataclass(frozen=True, slots=True)
class DoorClosedEvent(DeviceEventMessage):
    """open_duration_sec 은 선택. 없으면 RPi5 가 door_opened 의 recv_ts 로 계산."""

    open_duration_sec: float | None = None


@dataclass(frozen=True, slots=True)
class DispensedEvent(DeviceEventMessage):
    """시나리오 2(복약)·4(기상 후 수분)의 핵심 신호."""

    water_type: str | None = None
    amount_ml: int | None = None


@dataclass(frozen=True, slots=True)
class CycleCompletedEvent(DeviceEventMessage):
    pass


@dataclass(frozen=True, slots=True)
class CleaningStartedEvent(DeviceEventMessage):
    pass


@dataclass(frozen=True, slots=True)
class CleaningFinishedEvent(DeviceEventMessage):
    """duration_min, obstacle_count 는 주간 리포트의 바닥 상태 지표."""

    duration_min: int | None = None
    obstacle_count: int | None = None


@dataclass(frozen=True, slots=True)
class RingEvent(DeviceEventMessage):
    """dwell_sec 으로 택배와 방문객을 구분."""

    dwell_sec: float | None = None


def parse_device_event(payload: dict, base: dict) -> DeviceEventMessage:
    event_type = _req(payload, "event_type", str)
    common = {
        **base,
        "device_type": _req(payload, "device_type", str),
        "source": _one_of(payload, "source", SOURCE) or "",
    }

    match event_type:
        case "remote_input":
            return RemoteInputEvent(**common, button=_req(payload, "button", str))
        case "door_opened":
            return DoorOpenedEvent(**common)
        case "door_closed":
            return DoorClosedEvent(
                **common,
                open_duration_sec=_opt(payload, "open_duration_sec", float),
            )
        case "dispensed":
            return DispensedEvent(
                **common,
                water_type=_one_of(payload, "water_type", WATER_TYPE, required=False),
                amount_ml=_opt(payload, "amount_ml", int),
            )
        case "cycle_completed":
            return CycleCompletedEvent(**common)
        case "cleaning_started":
            return CleaningStartedEvent(**common)
        case "cleaning_finished":
            return CleaningFinishedEvent(
                **common,
                duration_min=_opt(payload, "duration_min", int),
                obstacle_count=_opt(payload, "obstacle_count", int),
            )
        case "ring":
            return RingEvent(**common, dwell_sec=_opt(payload, "dwell_sec", float))
        case _:
            raise SchemaError(f"알 수 없는 event_type: {event_type!r}")

# ============================================================== 노드·시스템

ACK_TYPE = frozenset({"DELIVERED", "SEEN"})
PROFILE = frozenset({"REAL", "DEMO"})
MODEL_NAME = frozenset({"kde", "hmm", "classifier"})


@dataclass(frozen=True, slots=True)
class NotifyAck(Message):
    """hestia/notify/ack — 노드 → RPi5.
    RPi5가 발행한 알림에 대한 노드의 응답
    """

    notify_id: str = ""
    ack_type: str = "DELIVERED"


@dataclass(frozen=True, slots=True)
class EmulatedEntry:
    """announce 의 emulates 항목 하나, (emulates: 이 노드가 대행하는 virtual_id 목록)
    """

    virtual_id: str
    type: str | None = None              # 센서면 type
    device_type: str | None = None       # 가전이면 device_type
    source: str = ""
    default_area: str | None = None      # 제안값. 최종 결정은 YAML/registry
    default_roles: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class NodeAnnounce(SystemMessage):
    """hestia/node/{node_id}/announce — 부팅 시 1회
    """

    fw: str = ""
    emulates: tuple[EmulatedEntry, ...] = ()


@dataclass(frozen=True, slots=True)
class NodeStatus(SystemMessage):
    """hestia/node/{node_id}/status — LWT, retained
    """

    online: bool = False
    ts_synced: bool = False             #NTP 동기화 성공 여부


@dataclass(frozen=True, slots=True)
class RegistryEntry:
    virtual_id: str
    area: str | None = None              # 물리적 위치
    roles: tuple[str, ...] = ()          # 기능적 역할 (원룸은 한 area 가 여러 role)
    type: str | None = None
    device_type: str | None = None
    source: str | None = None
    node_id: str | None = None           # LWT 연결 (센서만)
    channel: bool = False                # 알림 채널 사용 가능 (가전만)
    enabled: bool = True                 # false 면 판단에서 제외
    power_profile: str | None = None     # 전력 센서가 붙은 기기 (policy 의 [power.*] 키)


@dataclass(frozen=True, slots=True)
class RegistryDevices(SystemMessage):
    """hestia/registry/devices — RPi4 발행, retained.

    역할 매핑의 정본은 RPi5 로컬 YAML 이고 이것은 런타임 오버라이드다.
    수신 시 역할 매핑을 즉시 교체하고 모든 context 를 재계산한다.(변경 전 매핑으로 진행 중이던 판단은 무효)
    """

    devices: tuple[RegistryEntry, ...] = ()


@dataclass(frozen=True, slots=True)
class SystemProfile(SystemMessage):
    """hestia/system/profile — 동작 모드 스위치
    """

    profile: str = "REAL"


@dataclass(frozen=True, slots=True)
class ModelMessage(SystemMessage):
    """hestia/model/{name} — RPi4 배치 결과, retained.
    RPi4 가 죽어도 RPi5 는 마지막 모델로 계속 판단. 학습만 멈춤
    """

    name: str = ""
    trained_at: float = 0.0
    payload: dict = None  # type: ignore[assignment]


def parse_notify_ack(payload: dict, base: dict) -> NotifyAck:
    return NotifyAck(
        **base,
        notify_id=_req(payload, "notify_id", str),
        ack_type=_one_of(payload, "ack_type", ACK_TYPE),
    )


def parse_node_announce(payload: dict, base: dict) -> NodeAnnounce:
    raw = _req(payload, "emulates", list)
    entries = []
    for item in raw:
        if not isinstance(item, dict):
            raise SchemaError(f"emulates 항목이 객체가 아님: {item!r}")
        entries.append(
            EmulatedEntry(
                virtual_id=_req(item, "virtual_id", str),
                type=_opt(item, "type", str),
                device_type=_opt(item, "device_type", str),
                source=_one_of(item, "source", SOURCE, required=False) or "",
                default_area=_opt(item, "default_area", str),
                default_roles=tuple(_opt(item, "default_roles", list, []) or ()),
            )
        )
    return NodeAnnounce(**base, fw=_opt(payload, "fw", str, ""), emulates=tuple(entries))


def parse_node_status(payload: dict, base: dict) -> NodeStatus:
    return NodeStatus(
        **base,
        online=_req(payload, "online", bool),
        ts_synced=_opt(payload, "ts_synced", bool, False),
    )


def parse_registry(payload: dict, base: dict) -> RegistryDevices:
    raw = _req(payload, "devices", list)
    entries = []
    for item in raw:
        if not isinstance(item, dict):
            raise SchemaError(f"devices 항목이 객체가 아님: {item!r}")
        entries.append(
            RegistryEntry(
                virtual_id=_req(item, "virtual_id", str),
                area=_opt(item, "area", str),
                roles=tuple(_opt(item, "roles", list, []) or ()),
                type=_opt(item, "type", str),
                device_type=_opt(item, "device_type", str),
                source=_one_of(item, "source", SOURCE, required=False),
                node_id=_opt(item, "node_id", str),
                channel=_opt(item, "channel", bool, False),
                enabled=_opt(item, "enabled", bool, True),
                power_profile=_opt(item, "power_profile", str),
            )
        )
    return RegistryDevices(**base, devices=tuple(entries))


def parse_profile(payload: dict, base: dict) -> SystemProfile:
    return SystemProfile(**base, profile=_one_of(payload, "profile", PROFILE))


def parse_model(name: str, payload: dict, base: dict) -> ModelMessage:
    if name not in MODEL_NAME:
        raise SchemaError(f"알 수 없는 model name: {name!r}")
    return ModelMessage(
        **base,
        name=name,
        trained_at=_req(payload, "trained_at", float),
        payload=dict(payload),
    )

# ================================================================ 진입점


def parse(topic: str, raw: bytes | str, recv_ts: float) -> Message | None:
    """수신된 바이트/문자열 데이터를 파싱하여 앞에서 정의한 Message 객체로 다듬어내는 메시지 파싱 및 라우팅 모듈

    실패하면 경고를 남기고 None 을 반환 (한 노드의 잘못된 페이로드가 엔진 전체를 멈추게 하지 않음)
    구독하지 않는 토픽은 조용히 버린다(경고 없음).
    """
    try:
        payload = _decode(raw)      #raw: 전달받은 원시 패킷 데이터, _decode(): JSON 디코딩을 시도
    except SchemaError as exc:
        log.warning("페이로드 디코드 실패: topic=%s %s", topic, exc)
        return None

    parts = topic.split("/")
    if not parts or parts[0] != "hestia":
        return None

    try:
        return _dispatch(parts, payload, recv_ts)
    except SchemaError as exc:
        log.warning("스키마 위반으로 폐기: topic=%s %s", topic, exc)
        return None
    except (TypeError, ValueError) as exc:
        log.warning("파싱 실패: topic=%s %s", topic, exc)
        return None


def _decode(raw: bytes | str) -> dict:
    """bytes 또는 str 형태의 원시 패킷을 파이썬 dict 객체(JSON)로 변환"""
    import json

    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise SchemaError(f"JSON 아님: {exc}") from exc
    if not isinstance(payload, dict):
        raise SchemaError(f"최상위가 객체가 아님: {type(payload).__name__}")
    return payload


def _dispatch(parts: list[str], payload: dict, recv_ts: float) -> Message | None:
    """토픽 문자열을 슬래시(/) 기준으로 분할한 후, 적절한 처리 파서로 분기"""
    match parts:
        case ["hestia", "sensor", vid, "state"]:
            return parse_sensor(payload, check_envelope(payload, recv_ts, vid))

        case ["hestia", "device", vid, "state"]:
            return parse_device_state(payload, check_envelope(payload, recv_ts, vid))

        case ["hestia", "device", vid, "event"]:
            return parse_device_event(payload, check_envelope(payload, recv_ts, vid))

        case ["hestia", "notify", "ack"]:
            src = _req(payload, "src_id", str)
            return parse_notify_ack(payload, check_envelope(payload, recv_ts, src))

        case ["hestia", "node", nid, "announce"]:
            return parse_node_announce(payload, check_envelope(payload, recv_ts, nid))

        case ["hestia", "node", nid, "status"]:
            return parse_node_status(payload, check_envelope(payload, recv_ts, nid))

        case ["hestia", "model", name]:
            src = _req(payload, "src_id", str)
            return parse_model(name, payload, check_envelope(payload, recv_ts, src))

        case ["hestia", "registry", "devices"]:
            src = _req(payload, "src_id", str)
            return parse_registry(payload, check_envelope(payload, recv_ts, src))

        case ["hestia", "system", "profile"]:
            src = _req(payload, "src_id", str)
            return parse_profile(payload, check_envelope(payload, recv_ts, src))

        case _:
            return None            # 구독하지 않는 토픽