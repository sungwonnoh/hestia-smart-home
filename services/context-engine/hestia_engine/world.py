"""World State — 사실의 보관소.

센서와 가전이 보내온 값을 그대로 들고 있는다. (판단은 하지 않음)
예외: 전력 판정 (watt 를 OFF/STANDBY/ON 으로 바꾸는 것)

명세: 노드는 현재 상태만 보고하고, 경과 시간·마지막 시각은 RPi5 가 recv_ts 기준으로 계산한다.

changed_at 은 주 필드가 바뀔 때만 갱신한다.
"""

from __future__ import annotations

import logging
from collections import deque
from typing import Any

from .clock import Clock
from .config import Config
from .timers import Scheduler
from . import messages as m
from dataclasses import dataclass, field, fields as dc_fields

log = logging.getLogger(__name__)

HISTORY_SIZE = 30          # 센서당 (시각, 주 필드값) 최근 N개 (링버퍼 크기)


# ==================================================================== 기반


@dataclass(slots=True)
class SensorState:
    """모든 센서 상태의 공통부.
    updated_at 은 값이 안 바뀌어도 갱신된다. 센서 생존 확인에 쓴다
    """

    vid: str    #virtual id
    updated_at: float = 0.0     #마지막 수신 시각
    history: deque[tuple[float, Any]] = field(default_factory=lambda: deque(maxlen=HISTORY_SIZE))

    def touch(self, ts: float) -> None:
        self.updated_at = ts

    def record(self, ts: float, value: Any) -> None:        #history 큐에 기록
        self.history.append((ts, value))

    def idle_sec(self, now: float) -> float:
        """마지막 수신 이후 경과. 센서 고장 의심에 쓴다."""
        return now - self.updated_at


@dataclass(slots=True)
class PresenceState(SensorState):
    """mmWave. 
    confidence=high — present=false 를 즉시 신뢰한다."""

    #presence 센서의 고유 필드
    present: bool = False
    energy: int = 0
    distance_cm: int = 0
    changed_at: float = 0.0        # present 전환 시각
    still_since: float | None = None   # energy 가 임계 아래로 내려간 시각

    def state_sec(self, now: float) -> float:
        return now - self.changed_at    #현재 present 값이 유지된 시간 (재실 지속은 WorldState.dwell_sec)

    def still_sec(self, now: float) -> float:
        """정지 지속. 움직이는 중이면 0 반환.
        화장실 쓰러짐 판정이 이 값을 본다 — present=true 인데 energy 가 계속 바닥인 상태.
        """
        return 0.0 if self.still_since is None else now - self.still_since


@dataclass(slots=True)
class MotionState(SensorState):
    """PIR. confidence=low — 정지한 사람을 놓치므로 false 를 즉시 믿지 않는다."""

    motion: bool = False
    changed_at: float = 0.0

    def since_sec(self, now: float) -> float:
        return now - self.changed_at        #PIR 센서의 값이 변화된 후(False->True, True->False) 경과한 시간 반환


@dataclass(slots=True)
class DoorState(SensorState):
    open: bool = False
    changed_at: float = 0.0

    def open_sec(self, now: float) -> float:
        return now - self.changed_at if self.open else 0.0      #문이 열려있을 때, 문이 몇초동안 열려있었는지 반환


@dataclass(slots=True)
class BedState(SensorState):
    occupied: bool = False
    changed_at: float = 0.0

    def occupied_sec(self, now: float) -> float:
        return now - self.changed_at if self.occupied else 0.0      #침대 또는 소파에 몇초동안 누워있었는지 반환


@dataclass(slots=True)
class PowerState(SensorState):
    """전력. changed_at 은 확정 시각이 아니라 **후보가 된 시각**이다.
    인덕션을 켠 것은 09:20:00 이고 확정은 15초 뒤지만, MEAL_PREP 의 t0 는 09:20:00 이어야 한다.(확정 시각을 쓰면 t0 가 밀리고 KDE 분포가 틀어짐)
    """

    watt: float = 0.0
    state: str = "OFF"
    changed_at: float = 0.0     #pending_state가 변화한 시각
    pending_state: str | None = None        #검증을 위해 대기 중인 다음 상태 후보
    pending_since: float | None = None      #pending_state 대기 열에 들어간 시각

    def state_sec(self, now: float) -> float:
        return now - self.changed_at


@dataclass(slots=True)
class LightState(SensorState):
    """연속값이라 changed_at 이 없다. 판단에 쓰는 것은 현재값과 추세다."""

    lux: float = 0.0


@dataclass(slots=True)
class ClimateState(SensorState):
    temperature_c: float = 0.0
    humidity_pct: float = 0.0


@dataclass(slots=True)
class DeviceState:
    """가전. 판단의 입력이면서 개입의 출력이기도 하다.
    WATCHING_TV / RESTING / FOCUSED 는 센서 신호가 동일하며
    TV 전원과 remote_input 으로만 갈린다.
    """

    vid: str
    device_type: str
    fields: dict[str, Any] = field(default_factory=dict)
    changed_at: float = 0.0
    updated_at: float = 0.0
    last_event_at: dict[str, float] = field(default_factory=dict)       #이벤트 타입을 키로, 시각을 값으로 갖는 딕셔너리
    history: deque[tuple[float, str]] = field(default_factory=lambda: deque(maxlen=HISTORY_SIZE))

    def get(self, key: str, default: Any = None) -> Any:        #필드에 해당 키가 없으면 None 반환
        return self.fields.get(key, default)

    def state_sec(self, now: float) -> float:
        return now - self.changed_at

    def since_event(self, event_type: str, now: float) -> float | None:
        """그 이벤트가 마지막으로 온 뒤 경과. 한 번도 없으면 None.

        명세: TV 는 last_input_ts 를 보내지 않으므로 RPi5 가
        remote_input 의 recv_ts 를 기억해 무조작 시간을 계산한다.
        """
        ts = self.last_event_at.get(event_type)
        return None if ts is None else now - ts

    def idle_sec(self, now: float) -> float:        #무응답 시각
        return now - self.updated_at

# ==================================================================== 본체


class WorldState:
    """센서·가전의 최신값과 전환 시각을 보관한다.

    Config 로 센서 타입을 알고, Scheduler(타이머 스케줄러) 로 전력 확정을 대기하고, Clock 으로 시각을 읽는다. 
    셋 다 주입받으므로 Replay 와 실시간이 같다.
    """

    def __init__(self, clock: Clock, config: Config, scheduler: Scheduler) -> None:
        self._clock = clock
        self._config = config
        self._sched = scheduler
        self.sensors: dict[str, SensorState] = {}   #vid를 키로, 센서 객체를 값으로 갖는 딕셔너리
        self.devices: dict[str, DeviceState] = {}
        self.nodes: dict[str, bool] = {}        # node_id → online
        self.node_synced: dict[str, bool] = {}  # node_id → ts_synced

    # ------------------------------------------------------------ 조회

    def sensor(self, vid: str) -> SensorState | None:
        return self.sensors.get(vid)

    def device(self, vid: str) -> DeviceState | None:
        return self.devices.get(vid)

    def sensors_of(self, area: str) -> tuple[SensorState, ...]:
        """그 구역의 센서 중 값이 들어온 것들."""
        return tuple(
            s for vid in self._config.sensors_in(area) if (s := self.sensors.get(vid))
        )
        #해당 area에 해당하는 센서들의 vid에 대해, WorldState 클래스의 sensors에 vid 및 SensorState 객체가 등록되어 있으면 SensorState 객체들 튜플 반환
        #즉 해당 area의 센서들 중, 지금까지 값을 보내온 센서들을 반환

    def sensors_by_role(self, role: str) -> tuple[SensorState, ...]:
        return tuple(
            s for vid in self._config.sensors_with_role(role) if (s := self.sensors.get(vid))
        )

    def devices_of(self, area: str) -> tuple[DeviceState, ...]:
        return tuple(
            d for vid in self._config.devices_in(area) if (d := self.devices.get(vid))
        )

    def devices_of_type(self, device_type: str) -> tuple[DeviceState, ...]:
        return tuple(d for d in self.devices.values() if d.device_type == device_type)
        #WorldState 클래스의 devices 중 해당 device_type에 해당하는 가전 객체 반환

    def now(self) -> float:
        return self._clock.now()

    # ------------------------------------------------------------ 투입

    def apply(self, msg: m.Message) -> None:
        """파싱된 메시지 한 건을 반영한다.
        설정에 없거나 비활성인 ID 는 버린다. 
        """
        match msg:
            case m.SensorMessage():
                if not self._config.is_enabled(msg.src_id):
                    return
                self._apply_sensor(msg)
            case m.DeviceStateMessage():
                if not self._config.is_enabled(msg.src_id):
                    return
                self._apply_device_state(msg)
            case m.DeviceEventMessage():
                if not self._config.is_enabled(msg.src_id):
                    return
                self._apply_device_event(msg)
            case m.NodeStatus():
                self.nodes[msg.src_id] = msg.online
                self.node_synced[msg.src_id] = msg.ts_synced

    # ------------------------------------------------------------ 센서

    def _apply_sensor(self, msg: m.SensorMessage) -> None:
        ts = msg.recv_ts

        match msg:
            case m.PresenceMessage():
                self._presence(msg, ts)
            case m.MotionMessage():
                st = self._get(msg.src_id, MotionState)
                if msg.motion != st.motion or st.updated_at == 0.0:
                    st.motion = msg.motion
                    st.changed_at = ts
                    st.record(ts, msg.motion)
                st.touch(ts)
            case m.DoorMessage():
                st = self._get(msg.src_id, DoorState)
                if msg.open != st.open or st.updated_at == 0.0:
                    st.open = msg.open
                    st.changed_at = ts
                    st.record(ts, msg.open)
                st.touch(ts)
            case m.BedMessage():
                st = self._get(msg.src_id, BedState)
                if msg.occupied != st.occupied or st.updated_at == 0.0:
                    st.occupied = msg.occupied
                    st.changed_at = ts
                    st.record(ts, msg.occupied)
                st.touch(ts)
            case m.PowerMessage():
                self._power(msg, ts)
            case m.LightMessage():
                st = self._get(msg.src_id, LightState)
                st.lux = msg.illuminance_lux
                st.record(ts, msg.illuminance_lux)
                st.touch(ts)
            case m.ClimateMessage():
                st = self._get(msg.src_id, ClimateState)
                st.temperature_c = msg.temperature_c
                st.humidity_pct = msg.humidity_pct
                st.record(ts, msg.temperature_c)
                st.touch(ts)

    def _presence(self, msg: m.PresenceMessage, ts: float) -> None:
        """present 전환만 changed_at 을 갱신한다.
        """
        st = self._get(msg.src_id, PresenceState)
        first = st.updated_at == 0.0
        #_get()이 처음 만든 센서 객체를 반환한다면, updated_at이 == 0.0, 즉 처음 만든 객체라면 first = True

        if msg.present != st.present or first:      #방금 도착한 메시지의 present값과 센서의 누적 상태(st)의 present가 다른경우
            st.present = msg.present
            st.changed_at = ts
            st.record(ts, msg.present)

        st.energy = msg.energy
        st.distance_cm = msg.distance_cm

        """policy.toml의 [presence.energy] still_max = 10을 읽어와서, energy가 그 이하면 "정지 중"으로 봄"""
        still_max = float(self._config.value("presence", "energy", "still_max", default=10))
        if msg.present and msg.energy <= still_max:     #사람은 있는데 안 움직임 (mmWave 는 정지한 사람도 감지)
            if st.still_since is None:
                st.still_since = ts
        else:
            st.still_since = None

        st.touch(ts)        #updated_at 갱신

    # ------------------------------------------------------------ 전력

    def _power(self, msg: m.PowerMessage, ts: float) -> None:
        """watt 를 OFF/STANDBY/ON 으로 판정한다.
        노드가 state 를 채워 보냈으면 그대로 쓴다 (명세: 선택 필드). 없을 때만 watt 와 policy 임계값으로 판정한다.
        """
        st = self._get(msg.src_id, PowerState)
        first = st.updated_at == 0.0        # 해당 센서에서 처음 온 메시지인지
        st.watt = msg.watt
        st.record(ts, msg.watt)
        st.touch(ts)

        if msg.state is not None:       #수신한 메시지에 state가 채워져 있으면 그 값으로 업데이트
            self._commit_power(st, msg.state, ts, first=first)
            st.pending_state = None
            st.pending_since = None
            return

        rule = self._config.power_rule(msg.src_id)      #전력 임계값 확인
        target = self._classify(st.state, msg.watt, rule)

        if target == st.state:      #메시지 수신 후, 목표 state가 기존 state가 같다면
            if first:
                # 첫 수신은 전환이 아니라 초기 상태 확인이다.
                # min_hold 를 기다릴 대상이 아니므로 바로 시각을 찍는다.
                self._commit_power(st, target, ts, first=True)
            elif st.pending_state is not None:      #첫 수신은 아님 / 후보 상태가 존재함
                # 후보가 있었다면 조건이 깨진 것 — 스파이크였다
                st.pending_state = None     #후보 상태 비우기
                st.pending_since = None
                self._sched.cancel(self._power_key(msg.src_id))     # 전력 확정 대기 타이머 취소
            return

        if st.pending_state == target:
            return          # 이미 같은 후보로 대기 중

        # 목표 state가 현재 state 및 pending_state와 다른 경우
        # 새 후보. min_hold_sec 지속돼야 확정한다.
        st.pending_state = target
        st.pending_since = ts
        self._sched.at(     #전력 확정 대기 타이머 등록
            ts + rule.min_hold_sec,
            lambda vid=msg.src_id: self._confirm_power(vid),
            key=self._power_key(msg.src_id),
        )

    def _confirm_power(self, vid: str) -> None:
        """min_hold_sec 뒤 호출. 그동안 후보가 유지됐으면 확정한다.

        타이머가 필요한 이유: 전력 센서는 60초 주기 발행이라
        대기 중에 메시지가 안 올 수 있다.
        """
        st = self.sensors.get(vid)
        if not isinstance(st, PowerState) or st.pending_state is None:
            return
        self._commit_power(st, st.pending_state, st.pending_since or self._clock.now())
        st.pending_state = None
        st.pending_since = None

    def _commit_power(
        self, st: PowerState, state: str, changed_at: float, *, first: bool = False
    ) -> None:
        """first 는 첫 수신. 값이 기본값과 같아도 changed_at 을 찍어야 함(안 찍으면 changed_at 이 0.0 에 머물러 state_sec() 이 17억 초를 돌려주게 됨)
        """
        if st.state == state and not first:     #state 값 변화가 없고, 첫 메시지가 아닌 경우 바로 반환
            return
        st.state = state
        st.changed_at = changed_at      # 확정 시각이 아니라 후보가 된 시각
        log.debug("전력 %s → %s (t=%s)", st.vid, state, changed_at)

    @staticmethod
    def _classify(current: str, watt: float, rule: Any) -> str:
        """히스테리시스. 전력이 올라갈 때와 내려갈 때 기준이 다르다.
        state가 ON에서 떨어지는 기준은 on_exit_w / 다른 상태에서 ON이 되는 기준은 on_min_w
        """
        if current == "ON":     #현재 ON 상태일 때
            if watt >= rule.on_exit_w:
                return "ON"
            return "STANDBY" if watt > rule.standby_max_w else "OFF"
            #watt가 standby_max_w < watt < on_exit_w일 땐 "STANDBY", watt < standby_max_w이면 "OFF"

        if watt >= rule.on_min_w:   #현재 ON 상태가 아닐 때, on_min_w 보다 크다면
            return "ON"
        return "STANDBY" if watt > rule.standby_max_w else "OFF"

    @staticmethod
    def _power_key(vid: str) -> str:
        return f"power-confirm-{vid}"

    # ------------------------------------------------------------ 가전

    def _apply_device_state(self, msg: m.DeviceStateMessage) -> None:
        ts = msg.recv_ts
        st = self.devices.get(msg.src_id)
        if st is None:      #해당 가전에 대한 첫 수신 메시지
            st = DeviceState(vid=msg.src_id, device_type=msg.device_type)
            self.devices[msg.src_id] = st

        fields = _device_fields(msg)        #해당 가전 고유의 필드와 그 값만 남겨 딕셔너리로 반환
        marker = _primary(msg.device_type, fields)      #방금 도착한 메시지의 주 상태
        prev = _primary(st.device_type, st.fields)      #기존에 저장돼 있던 직전 주 상태

        if marker != prev or st.updated_at == 0.0:      #수신한 상태와 기존 상태가 다르면 changed_at 갱신
            st.changed_at = ts
            st.history.append((ts, marker))

        st.fields = fields
        st.updated_at = ts

    def _apply_device_event(self, msg: m.DeviceEventMessage) -> None:
        ts = msg.recv_ts
        st = self.devices.get(msg.src_id)
        if st is None:      #첫 수신
            st = DeviceState(vid=msg.src_id, device_type=msg.device_type)
            self.devices[msg.src_id] = st

        event_type = _event_type(msg)       #클래스 이름으로부터 이벤트 타입(문자열) 반환
        st.last_event_at[event_type] = ts
        st.history.append((ts, event_type))
        st.updated_at = ts

    # ------------------------------------------------------------ 파생

    def dwell_sec(self, area: str) -> float:
        """그 구역의 재실 센서가 present 로 바뀐 뒤 경과. (재실 경과 시간 반환)
        여러 센서가 있으면 가장 오래된 것을 쓴다 — 주방 mmWave 와 PIR 이 따로 반응해도 체류는 하나다.
        """
        now = self._clock.now()
        spans = [
            s.state_sec(now)
            for s in self.sensors_of(area)
            if isinstance(s, PresenceState) and s.present       #해당 area의 센서들 중 presence 센서에 대해
        ]
        return max(spans) if spans else 0.0

    def still_sec(self, area: str) -> float:        #그 구역에서 사람이 몇 초째 정지 상태인가, 화장실 쓰러짐 판정
        now = self._clock.now()
        spans = [
            s.still_sec(now)
            for s in self.sensors_of(area)
            if isinstance(s, PresenceState) and s.present and s.still_since is not None #사람이 있지만, 정지중
        ]
        return max(spans) if spans else 0.0

    def power_state(self, vid: str) -> str:
        st = self.sensors.get(vid)
        return st.state if isinstance(st, PowerState) else "OFF"

    def any_power_on(self, role: str) -> bool:
        """그 역할의 전력 센서 중 하나라도 ON 인가.
        MEAL_PREP 판정 — 조리 기구가 켜져 있나.
        """
        return any(
            isinstance(s, PowerState) and s.state == "ON"
            for s in self.sensors_by_role(role)
        )

    def since_device_event(self, vid: str, event_type: str) -> float | None:
        st = self.devices.get(vid)
        return st.since_event(event_type, self._clock.now()) if st else None    #해당 이벤트가 마지막으로 온 뒤 경과

    def since_any_event(self, device_type: str, event_type: str) -> float | None:
        """그 종류의 가전 중 가장 최근 이벤트 이후 경과.
        TV 가 여러 대여도 '마지막 리모컨 조작'은 하나다.
        """
        now = self._clock.now()
        spans = [
            gap
            for d in self.devices_of_type(device_type)
            if (gap := d.since_event(event_type, now)) is not None
        ]
        return min(spans) if spans else None

    def offline_nodes(self) -> tuple[str, ...]:
        return tuple(nid for nid, online in self.nodes.items() if not online)

    def sensors_offline(self) -> tuple[str, ...]:
        """죽은 노드에 속한 센서들. away 의 SENSOR_FAULT 판정 근거."""
        dead = set(self.offline_nodes())
        return tuple(
            s.id for s in self._config.all_sensors()
            if s.node is not None and s.node in dead and s.enabled
        )

    # ------------------------------------------------------------ 내부

    def _get(self, vid: str, kind: type) -> Any:
        st = self.sensors.get(vid)
        if st is None or not isinstance(st, kind):
            st = kind(vid=vid)
            self.sensors[vid] = st
        return st

    def __repr__(self) -> str:
        return f"WorldState(sensors={len(self.sensors)}, devices={len(self.devices)})"


# ---------------------------------------------------------------- 헬퍼

# 가전마다 '주 상태'로 볼 필드가 다르다. changed_at 갱신 기준이 된다.
# 세탁기는 power 가 계속 ON 이고 의미 있는 변화는 cycle 쪽이다.
PRIMARY_FIELD = {
    "smart_tv": "power",
    "smart_light": "power",
    "air_conditioner": "power",
    "air_purifier": "power",
    "water_purifier": "power",
    "smart_fridge": "door",
    "washer": "cycle",
    "robot_cleaner": "status",
    "doorbell": "power",
}


def _primary(device_type: str, fields: dict[str, Any]) -> str:
    """그 가전의 주 상태값. 없으면 빈 문자열."""
    value = fields.get(PRIMARY_FIELD.get(device_type, "power"))
    return "" if value is None else str(value)


def _device_fields(msg: m.DeviceStateMessage) -> dict[str, Any]:
    """dataclass 를 딕셔너리로. 가전 9종의 필드가 제각각이라 이렇게 둔다.

    __slots__ 대신 dataclasses.fields 를 쓰는 이유: __slots__ 는 그 클래스가
    직접 선언한 것만 담아서 상속분이 빠진다.
    """
    skip = {"recv_ts", "src_id", "sent_ts", "seq", "device_type", "source"}
    return {
        f.name: value
        for f in dc_fields(msg)
        if f.name not in skip and (value := getattr(msg, f.name)) is not None
    }


def _event_type(msg: m.DeviceEventMessage) -> str:
    """클래스 이름으로부터 event_type 을 되돌린다. RemoteInputEvent → remote_input"""
    name = type(msg).__name__.removesuffix("Event")
    out = []
    for i, ch in enumerate(name):
        if ch.isupper() and i:
            out.append("_")
        out.append(ch.lower())
    return "".join(out)