"""활동 묶음 추적.

activity 는 기억이 없다. 매 순간 센서를 보고 12종 점수를 다시 계산할 뿐,
09:20 의 MEAL_PREP 과 09:38 의 EATING 이 같은 식사인지 모른다.

FSM 이 그 '묶음'이라는 개념을 들고 있는 유일한 곳이다.
그리고 묶음의 시작 시각 — t0 — 이 KDE 학습의 입력이다.

명세: t0 는 활동 묶음의 시작 시각. MEAL_PREP -> EATING 전이 시에도 유지되어
KDE 에 일관된 값을 제공한다.

방향은 한쪽이다. FSM 은 activity 를 읽기만 하고 t0 를 돌려준다.
FSM 이 점수를 되돌려 바꾸면 순환이 생겨 추적이 불가능해진다.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from .clock import Clock
from .config import Config
from .timeutil import day_key
from .world import PowerState, WorldState
from .context import Context

log = logging.getLogger(__name__)


# ==================================================================== t0 로그


@dataclass(frozen=True, slots=True)
class T0Entry:
    """활동 묶음 한 건. KDE 학습의 원재료다."""

    date: str              # YYYY-MM-DD (KST)
    type: str              # meal / wake / hydration
    t0: float              # 묶음 시작 시각 (epoch)
    source: str            # sensor / diary / aruba
    prompted: bool         # 시스템 유도로 일어난 행동인가
    duration_sec: float


@runtime_checkable
class T0Log(Protocol):
    def write(self, entry: T0Entry) -> None: ...


class MemoryT0Log:
    """테스트와 Replay 용. 파일을 쓰면 결정성이 깨진다."""

    def __init__(self) -> None:
        self.entries: list[T0Entry] = []

    def write(self, entry: T0Entry) -> None:
        self.entries.append(entry)

    def of_type(self, type_: str) -> tuple[T0Entry, ...]:
        return tuple(e for e in self.entries if e.type == type_)


class FileT0Log:
    """JSONL append. RPi5 는 /data 만 쓸 수 있다.

    나중에 hestia/log/t0 발행을 붙이면 RPi4 가 hestia/# 전량 적재로
    자동 수집한다. 그때도 이 파일은 백업으로 남는다.
    """

    def __init__(self, path: Path) -> None:
        self._path = path

    def write(self, entry: T0Entry) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._path.open("a", encoding="utf-8") as fp:
                fp.write(json.dumps(asdict(entry), ensure_ascii=False) + "\n")
        except OSError as exc:
            # 로그 실패가 엔진을 멈추게 해서는 안 된다
            log.warning("t0 로그 기록 실패: %s", exc)


# ==================================================================== Meal FSM


@dataclass(slots=True)
class MealSession:
    t0: float
    opened_state: str
    last_active_at: float          # 묶음 상태였던 마지막 시각
    prompted: bool = False


class MealFSM:
    """식사 묶음을 열고 닫으며 t0 를 보관한다.

        IDLE ──MEAL_PREP/EATING 진입──→ ACTIVE ──이탈·타임아웃──→ IDLE
                                          ↑
                                     t0 고정
    """

    def __init__(
        self,
        clock: Clock,
        config: Config,
        world: WorldState,
        t0log: T0Log | None = None,
    ) -> None:
        self._clock = clock
        self._config = config
        self._world = world
        self._log = t0log
        self.session: MealSession | None = None
        self._timed_out = False        # 타임아웃 후 재개 차단

    @property
    def t0(self) -> float | None:
        return self.session.t0 if self.session else None

    def update(self, state: str, in_meal_area: bool = False) -> list[tuple[str, float]]:
        """activity 상태를 받아 묶음을 열거나 닫는다. 예약할 타이머를 돌려준다.

        in_meal_area 는 '주방에 아직 있는가'다. 조리가 끝나고 EATING 판정이
        서기 전 구간은 KITCHEN_MISC 인데, 그것으로 묶음을 끊으면
        MEAL_PREP -> EATING 사이에서 t0 가 리셋된다.
        """
        now = self._clock.now()
        timers: list[tuple[str, float]] = []
        open_states = tuple(
            self._config.value("fsm", "meal", "open_states", default=["MEAL_PREP", "EATING"])
        )

        if state in open_states:
            if self.session is None:
                if self._timed_out:
                    # 전력이 꺼지기 전까지 다시 열지 않는다.
                    # 조리기구가 켜진 채라 _evidence_t0 가 같은 시각을 돌려주고,
                    # 같은 t0 가 2시간마다 쌓이면 KDE 분포가 왜곡된다.
                    return timers
                self._open(state, now)
            elif now - self.session.t0 >= self._timeout():
                # 2시간째 조리 중일 리 없다. 센서가 켜진 채 방치됐거나
                # 판정이 고착된 것이다. 닫되 새로 열지 않는다.
                self._close(now, use_now=True)
                self._timed_out = True
                return timers
            else:
                self.session.last_active_at = now
            timers.append(("meal-timeout", self.session.t0 + self._timeout()))
            return timers

        self._timed_out = False        # 묶음 상태를 벗어나면 차단 해제

        if self.session is None:
            return timers

        # 세션이 열려 있고 주방에 아직 있으면 묶음은 이어진다.
        # 조리 종료 후 EATING 판정이 서기까지의 공백(KITCHEN_MISC)을 흡수한다.
        if in_meal_area and state == "KITCHEN_MISC":
            self.session.last_active_at = now
            timers.append(("meal-timeout", self.session.t0 + self._timeout()))
            return timers

        grace = float(self._config.value("fsm", "meal", "close_grace_sec", default=600))
        idle = now - self.session.last_active_at

        if now - self.session.t0 >= self._timeout():
            self._close(now, use_now=True)
            self._timed_out = True
        elif idle >= grace:
            # 묶음은 last_active_at 에 끝났고 우리가 grace 만큼 기다린 것뿐이다
            self._close(now)
        else:
            timers.append(("meal-close", self.session.last_active_at + grace))

        return timers

    def mark_prompted(self) -> None:
        """시스템 유도로 시작된 묶음임을 표시한다.

        명세: 09:30 에 '물 드세요'를 보내 09:32 에 마셨다면, 그대로 학습하면
        '이 사람은 09:32 에 물을 마신다'가 되지만 실제로는 시스템이 만든 패턴이다.
        알림 층이 생기면 여기를 호출한다.
        """
        if self.session is not None:
            self.session.prompted = True

    # ------------------------------------------------------------ 내부

    def _open(self, state: str, now: float) -> None:
        t0 = self._evidence_t0(state, now)
        self.session = MealSession(t0=t0, opened_state=state, last_active_at=now)
        log.debug("식사 묶음 시작 t0=%s (%s)", t0, state)

    def _close(self, now: float, *, use_now: bool = False) -> None:
        s = self.session
        self.session = None
        if s is None:
            return

        # 타임아웃으로 닫을 때는 묶음이 now 까지 이어진 것이다.
        # 이탈로 닫을 때는 last_active_at 에 끝났고 grace 만큼 기다린 것뿐이다.
        duration = (now if use_now else s.last_active_at) - s.t0
        min_dur = float(self._config.value("fsm", "meal", "min_duration_sec", default=120))
        if duration < min_dur:
            log.debug("식사 묶음 폐기 — 너무 짧음 (%.0f초)", duration)
            return

        if self._log is not None:
            self._log.write(
                T0Entry(
                    date=day_key(s.t0),
                    type="meal",
                    t0=s.t0,
                    source="sensor",
                    prompted=s.prompted,
                    duration_sec=duration,
                )
            )
        log.debug("식사 묶음 종료 t0=%s duration=%.0f", s.t0, duration)

    def _evidence_t0(self, state: str, now: float) -> float:
        """근거가 생긴 시각을 t0 로 쓴다. 상태가 바뀐 시각이 아니다.

        인덕션을 켠 것은 09:20:00 이고 MEAL_PREP 판정은 그 뒤다.
        판정 시각을 쓰면 t0 가 밀리고 KDE 분포가 통째로 틀어진다.
        """
        if state == "MEAL_PREP":
            # 조리 기구가 켜진 시각 — World State 가 후보 시각으로 들고 있다
            starts = [
                st.changed_at
                for st in self._world.sensors_by_role("MEAL")
                if isinstance(st, PowerState) and st.state == "ON" and st.changed_at > 0.0
            ]
            if starts:
                return min(starts)

        # EATING 으로 바로 열렸거나 전력 근거가 없으면 주방 체류 시작 시각
        dwell = self._world.dwell_sec("kitchen")
        return now - dwell if dwell > 0 else now

    def _timeout(self) -> float:
        return float(self._config.value("fsm", "meal", "session_timeout_sec", default=7200))


# ==================================================================== wake FSM


@dataclass(frozen=True, slots=True)
class WakeState(Context):
    """명세의 context/wake.

    activity 는 '지금'의 상태, wake 는 '오늘 하루'의 누적이다.
    시간 스케일이 달라 별도로 유지한다.

    추론이 아니라 플래그 집합이므로 confidence·factors 를 쓰지 않는다 (명세).
    Context 를 상속하는 것은 발행 경로를 하나로 두기 위해서다.

    _prompted 는 되먹임 억제용 — 유도된 행동이 개인 분포를 오염시키지 않도록
    분리 기록한다.
    """

    state: str = "ASLEEP"              # ASLEEP / AWAKE
    wake_t0: float | None = None       # 침대를 떠난 시각. KDE 기상 분포의 입력
    hydration_done: bool = False
    hydration_prompted: bool = False
    meal_done: bool = False
    meal_prompted: bool = False
    medication_done: bool = False
    medication_prompted: bool = False

    def payload(self, now: float) -> dict[str, Any]:
        return {
            "state": self.state,
            "wake_t0": self.wake_t0,
            "hydration_done": self.hydration_done,
            "hydration_prompted": self.hydration_prompted,
            "meal_done": self.meal_done,
            "meal_prompted": self.meal_prompted,
            "medication_done": self.medication_done,
            "medication_prompted": self.medication_prompted,
        }

    def same_as(self, other: Context | None) -> bool:
        if not isinstance(other, WakeState):
            return False
        return (
            self.state == other.state
            and self.hydration_done == other.hydration_done
            and self.meal_done == other.meal_done
            and self.medication_done == other.medication_done
        )


class WakeFSM:
    """하루의 기상 루틴을 추적한다.

        ASLEEP ──SLEEPING 지속 후 이탈──→ AWAKE ──다음 수면──→ ASLEEP
                                            ↑
                                       wake_t0 고정

    추론이 아니라 플래그 집합이므로 confidence·factors 가 없다 (명세).
    """

    def __init__(
        self,
        clock: Clock,
        config: Config,
        world: WorldState,
        t0log: T0Log | None = None,
    ) -> None:
        self._clock = clock
        self._config = config
        self._world = world
        self._log = t0log
        self.state = WakeState(name="wake", since=clock.now())
        self._sleep_since: float | None = None
        self._logged_wake_date: str | None = None

    def update(self, activity_state: str, activity_since: float) -> list[tuple[str, float]]:
        now = self._clock.now()
        timers: list[tuple[str, float]] = []

        sleep_need = float(self._config.value("fsm", "wake", "sleep_confirm_sec", default=600))
        wake_need = float(self._config.value("fsm", "wake", "wake_confirm_sec", default=300))

        if activity_state == "SLEEPING":
            if self._sleep_since is None:
                self._sleep_since = activity_since
            if now - self._sleep_since >= sleep_need and self.state.state == "AWAKE":
                # 다음 기상을 위해 리셋한다 (명세: 리셋은 다음 기상 확정 시)
                self.state = WakeState(name="wake", since=now)
                self._logged_wake_date = None
            else:
                timers.append(("wake-sleep", self._sleep_since + sleep_need))
            return timers

        self._sleep_since = None

        # 기상 — 침대를 떠나 명확한 활동이 보이면 확정
        if self.state.state == "ASLEEP":
            if activity_state in ("BATHROOM", "MEAL_PREP", "EATING", "KITCHEN_MISC",
                                  "WATCHING_TV", "RESTING", "LAUNDRY"):
                # 다른 방에서 활동 중 — 기상이 명백하다
                self._confirm_wake(self._bed_left_at() or activity_since)
            elif activity_state == "WAKING":
                if now - activity_since >= wake_need:
                    self._confirm_wake(self._bed_left_at() or activity_since)
                else:
                    timers.append(("wake-confirm", activity_since + wake_need))

        return timers

    def note_hydration(self, prompted: bool = False) -> None:
        """정수기 급수 이벤트. 기상 후 창 안이면 루틴으로 기록한다."""
        if self.state.state != "AWAKE" or self.state.hydration_done:
            return
        if not self._within("hydration_window_sec"):
            return
        self.state = _replace(self.state, hydration_done=True, hydration_prompted=prompted)
        self._log_entry("hydration", prompted)

    def note_meal(self, prompted: bool = False) -> None:
        if self.state.state != "AWAKE" or self.state.meal_done:
            return
        if not self._within("meal_window_sec"):
            return
        self.state = _replace(self.state, meal_done=True, meal_prompted=prompted)

    def note_medication(self, prompted: bool = False) -> None:
        """복약은 이벤트로 판정할 수 없다 — ack 로만 확인한다 (명세)."""
        if self.state.state != "AWAKE" or self.state.medication_done:
            return
        self.state = _replace(self.state, medication_done=True, medication_prompted=prompted)

    # ------------------------------------------------------------ 내부

    def _bed_left_at(self) -> float | None:
        """침대 압력 패드가 해제된 시각.

        wake_t0 는 기상 확정 시각이 아니라 침대를 떠난 시각이다 —
        KDE 기상 분포의 입력이므로 판정 지연이 섞이면 안 된다.
        """
        from .world import BedState

        for st in self._world.sensors_by_role("SLEEP"):
            if isinstance(st, BedState) and not st.occupied and st.changed_at > 0.0:
                return st.changed_at
        return None

    def _confirm_wake(self, wake_t0: float) -> None:
        """wake_t0 는 기상 확정 시각이 아니라 침대를 떠난 시각이다."""
        self.state = WakeState(name="wake", since=wake_t0, state="AWAKE", wake_t0=wake_t0)
        date = day_key(wake_t0)
        if self._log is not None and self._logged_wake_date != date:
            self._logged_wake_date = date
            self._log.write(
                T0Entry(
                    date=date,
                    type="wake",
                    t0=wake_t0,
                    source="sensor",
                    prompted=False,
                    duration_sec=0.0,
                )
            )
        log.debug("기상 확정 wake_t0=%s", wake_t0)

    def _within(self, key: str) -> bool:
        window = float(self._config.value("fsm", "wake", key, default=7200))
        if self.state.wake_t0 is None:
            return False
        return self._clock.now() - self.state.wake_t0 <= window

    def _log_entry(self, type_: str, prompted: bool) -> None:
        if self._log is None or self.state.wake_t0 is None:
            return
        now = self._clock.now()
        self._log.write(
            T0Entry(
                date=day_key(now),
                type=type_,
                t0=now,
                source="sensor",
                prompted=prompted,
                duration_sec=now - self.state.wake_t0,   # hydration_lag
            )
        )


def _replace(state: WakeState, **changes: Any) -> WakeState:
    from dataclasses import replace

    return replace(state, **changes)