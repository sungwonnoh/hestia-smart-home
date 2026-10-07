"""시나리오 — 언제 무엇을 할지.
    policy 는 "지금이 평소와 다른가" 를 묻고, notify 는 "어떻게 전달하나" 를 안다. 여기는 그 사이 — "그래서 뭘 해야 하나".

각 시나리오는 셋을 한다.

    ① 트리거 조건 확인      context 와 FSM 플래그를 읽는다
    ② policy 로 판정        개인 분포에서 지금이 이상한가
    ③ 후보면 알림 발송      notifier 에 넘긴다

②의 결과는 후보가 아니어도 발행한다. 왜 개입하지 않았는지가 왜 개입했는지만큼 중요(명세의 intervention/decision).

지금은 WAKE_ROUTINE 하나뿐. 나머지 셋(MEDICATION_PROMPT, SLEEP_ROUTINE, SAFETY)은 구조가 잡힌 뒤에 추가.
"""

from __future__ import annotations

import logging
from typing import Any, Callable
from datetime import datetime
from dataclasses import dataclass, field

from .control import Controller
from .timeutil import KST, day_key, hhmm

from .clock import Clock
from .config import Config
from .notify import Notifier
from .world import PresenceState, WorldState
from .policy import Decision, InterventionPolicy
from .control import Controller

log = logging.getLogger(__name__)

SRC_ID = "rpi5"
SCHEMA_VERSION = 1


@dataclass(frozen=True, slots=True)
class SleepProbe:
    """진행 중인 취침 확인."""

    method: str                      # banner / dim
    area: str | None
    started_at: float
    deadline: float                  # 이 시각까지 무반응이면 잠든 것
    still_sec: float                 # 프로브 시점의 정지 시간
    notify_id: str | None = None     # banner 일 때
    target: str | None = None        # dim 일 때 조명 vid
    before: int | None = None        # 내리기 전 밝기. 깨면 되돌린다


@dataclass(frozen=True, slots=True)
class AsleepState:
    """확정된 수면."""

    since: float
    area: str | None                 # 어디서 잠들었나. 제어 대상을 정한다
    nap: bool                        # 낮잠이면 제어하지 않는다
    decision_id: str | None = None


class ScenarioRunner:
    """매 recompute 후에 불림.
       Engine 이 소유하고, 타이머 경로에서도 같은 함수를 탄다 — 시간 경과로만 성립하는 조건(기상 후 N분 물을 안 마심)이 묻히지 않게.
    """

    def __init__(
        self,
        clock: Clock,
        config: Config,
        world: WorldState,
        policy: InterventionPolicy,
        notifier: Notifier,
        controller: Controller,
        publish: Callable[[str, dict[str, Any], bool], None],
        suppression: Any = None,
    ) -> None:
        self._clock = clock
        self._config = config
        self._world = world
        self._policy = policy
        self._notifier = notifier
        self._controller = controller
        self._publish = publish
        self._suppression = suppression

        # 직전 판정. candidate 나 reason 이 바뀔 때만 발행한다 —
        # 판정은 context 가 바뀔 때마다 일어나므로 매번 발행하면 과함.
        self._last: dict[str, tuple[bool, str]] = {}
        self._probe: SleepProbe | None = None
        self.asleep: AsleepState | None = None
        self._seq = 0

    def tick(self, context: Any) -> list[tuple[str, float]]:
        """시나리오를 전부 훑는다. 조건에 안 맞으면 조용히 지나간다."""
        timers: list[tuple[str, float]] = []
        timers += self._wake_routine(context)
        timers += self._safety(context)
        timers += self._sleep_routine(context)
        return timers


     # ------------------------------------------------------------ SLEEP_ROUTINE

    def _sleep_routine(self, context) -> list[tuple[str, float]]:
        """자는지 확인한다."""
        now = self._clock.now()

        if self._probe is not None:
            return self._check_probe(context, now)

        if self.asleep is not None:
            # 확정된 뒤에는 깰 때까지 다시 묻지 않는다
            if self._woke_up():
                self.asleep = None
            return []

        area = self._sleep_area(context)
        if area is None:
            return []

        limit = float(self._sleep_value("still_sec", 900))
        since = self._still_since(area)
        if since is None:
            return []                       # 움직이는 중

        still = now - since
        if still < limit:
            return [("sleep-probe", since + limit)]

        return self._send_probe(context, area, still, now)


    def _sleep_area(self, context) -> str | None:
        """자는지 확인할 만한 상태인가. 그렇다면 어느 구역인가."""
        states = tuple(self._sleep_value(
            "states",
            ["RESTING", "WATCHING_TV", "IN_BED_AWAKE", "SLEEPING"],
        ))
        activity = getattr(context, "activity", None)
        if activity is None or activity.state not in states:
            return None
        if activity.area:
            return activity.area
        presence = getattr(context, "presence", None)
        return presence.user_area if presence is not None else None


    # 프로브 발송
    def _send_probe(
        self, context, area: str, still: float, now: float
    ) -> list[tuple[str, float]]:
        timeout = float(self._sleep_value("probe_timeout_sec", 60))

        # 프로브 중에는 다른 알림을 막는다.
        if self._suppression is not None:
            self._suppression.start_probe(stage=1, duration_sec=timeout)

        probe = (
            self._probe_banner(context, area, still, now, timeout)
            if self._tv_on(area)
            else self._probe_dim(area, still, now, timeout)
        )
        if probe is None:
            self._end_probe()
            return []

        self._probe = probe
        return [("sleep-probe", probe.deadline)]
    

    def _probe_banner(
        self, context, area: str, still: float, now: float, timeout: float
    ) -> SleepProbe | None:
        """TV 가 켜져 있으면 묻는다."""
        notify_id = self._notifier.send(
            scenario="SLEEP_ROUTINE",
            title=str(self._notify_value("SLEEP_ROUTINE", "title", "확인")),
            text=str(self._notify_value(
                "SLEEP_ROUTINE", "text", "지금 일어나계신가요?"
            )),
            priority=str(self._notify_value("SLEEP_ROUTINE", "priority", "low")),
            presence=getattr(context, "presence", None),
            suppression=getattr(context, "suppression", None),
        )
        if notify_id is None:
            return None
        return SleepProbe(
            method="banner", area=area, started_at=now,
            deadline=now + timeout, still_sec=still, notify_id=notify_id,
        )

    def _probe_dim(
        self, area: str, still: float, now: float, timeout: float
    ) -> SleepProbe | None:
        """V 가 꺼져 있으면 배너를 띄울 근거가 없다. 조명을 한 단계 내려 반응을 본다.
        """
        lights = self._controller.devices_of_type("smart_light", area)
        if not lights:
            return None

        target = lights[0]
        st = self._world.device(target)
        if st is None or st.get("power") != "ON":
            return None                     # 꺼진 조명은 내릴 수 없다

        before = st.get("brightness")
        level = int(self._sleep_value("probe_brightness", 70))
        if before is not None and int(before) <= level:
            return None                     # 이미 그보다 어둡다

        self._controller.set(
            target,
            power="ON",
            brightness=level,
            transition_ms=int(self._sleep_value("probe_transition_ms", 30000)),
            reason="SLEEP_ROUTINE",
            priority="low",
        )
        return SleepProbe(
            method="dim", area=area, started_at=now,
            deadline=now + timeout, still_sec=still,
            target=target, before=int(before) if before is not None else None,
        )


    # 응답 확인
    def _check_probe(self, context, now: float) -> list[tuple[str, float]]:
        """반응이 있었나, 시간이 다 됐나."""
        probe = self._probe
        assert probe is not None

        if self._responded(probe):
            self._end_probe()
            self._probe = None
            self._restore(probe)
            self._emit_sleep_decision(probe, asleep=False, now=now)
            return []

        if now < probe.deadline:
            return [("sleep-probe", probe.deadline)]

        # 무반응 — 잠든 것으로 본다
        self._end_probe()
        self._probe = None
        decision_id = self._emit_sleep_decision(probe, asleep=True, now=now)
        self.asleep = AsleepState(
            since=probe.started_at,
            area=probe.area,
            nap=self._is_nap(),
            decision_id=decision_id,
        )
        return []

    def _responded(self, probe: SleepProbe) -> bool:
        """깨어 있다는 증거. 누르거나 움직이거나 되돌렸다."""
        if probe.notify_id is not None:
            pending = self._notifier.store.get(probe.notify_id)
            if pending is not None and pending.acked:
                return True
        if probe.target is not None and self._controller.reverted(probe.target):
            return True
        if probe.area is not None and self._still_since(probe.area) is None:
            return True                     # 움직였다
        return False

    def _restore(self, probe: SleepProbe) -> None:
        """조명 프로브를 되돌린다.
        """
        if probe.method != "dim" or probe.target is None or probe.before is None:
            return
        if self._controller.reverted(probe.target):
            return
        self._controller.set(
            probe.target, power="ON", brightness=probe.before,
            reason="SLEEP_ROUTINE", priority="low",
        )

    def _end_probe(self) -> None:
        if self._suppression is not None:
            self._suppression.end_probe()

    # decision과 헬퍼
    def _emit_sleep_decision(
        self, probe: SleepProbe, *, asleep: bool, now: float
    ) -> str | None:
        decision = Decision(
            kind="sleep",
            candidate=asleep,
            reason="ASLEEP_CONFIRMED" if asleep else "AWAKE",
            confidence=1.0 if asleep else 0.0,
            factors={
                "area": probe.area,
                "method": probe.method,
                "still_sec": round(probe.still_sec),
                "probe_sec": round(now - probe.started_at),
                "nap": self._is_nap(),
            },
        )
        return self._emit_decision(decision)

    def _is_nap(self) -> bool:
        """낮잠과 밤잠을 가른다."""
        window = self._config.value("limits", "quiet_hours", default=None)
        if not window:
            return False
        now = datetime.fromtimestamp(self._clock.now(), KST)
        minutes = now.hour * 60 + now.minute
        start = hhmm(str(window.get("start", "22:00")))
        end = hhmm(str(window.get("end", "07:00")))
        night = (
            start <= minutes < end if start <= end
            else minutes >= start or minutes < end
        )
        return not night

    def _woke_up(self) -> bool:
        """확정을 푼다. 움직이면 깬 것이다."""
        if self.asleep is None or self.asleep.area is None:
            return True
        return self._still_since(self.asleep.area) is None

    def _tv_on(self, area: str) -> bool:
        for vid in self._controller.devices_of_type("smart_tv", area):
            st = self._world.device(vid)
            if st is not None and st.get("power") == "ON":
                return True
        return False

    def _sleep_value(self, key: str, default: Any) -> Any:
        return self._config.value("scenarios", "sleep", key, default=default)
    

    # ------------------------------------------------------------ SAFETY

    def _safety(self, context: Any) -> list[tuple[str, float]]:
        """낙상 의심 — 사람이 있는데 오래 움직이지 않는다.
           개인 분포를 쓰지 않는다. 지금은 화장실만 본다.
        """
        timers: list[tuple[str, float]] = []
        now = self._clock.now()

        areas = tuple(
            self._config.value("thresholds", "safety", "still_areas", default=["bathroom"])
        )
        limit = float(
            self._config.value("thresholds", "safety", "still_sec", default=900)
        )

        for area in areas:
            if not self._area_occupied(area):
                continue

            since = self._still_since(area)
            if since is None:
                # 움직이는 중. 멈추는 순간 센서가 메시지를 보내
                # 다시 걸리므로 예약하지 않는다.
                self._emit_decision(Decision(
                    kind=f"safety:{area}",
                    candidate=False,
                    reason="MOVING",
                    factors={"area": area, "still_sec": 0, "limit_sec": limit},
                ))
                continue
            
            still = now - since
            over = still >= limit

            decision = Decision(
                kind=f"safety:{area}",
                candidate=over,
                reason="STILL_TOO_LONG" if over else "MOVING",
                confidence=1.0 if over else 0.0,
                factors={"area": area, "still_sec": round(still), "limit_sec": limit},
            )
            decision_id = self._emit_decision(decision)

            if not over:
                timers.append((f"safety-{area}", since + limit))
                continue

            self._notifier.send(
                scenario="SAFETY",
                title=str(self._notify_value("SAFETY", "title", "확인이 필요합니다")),
                text=str(self._notify_value("SAFETY", "text", "괜찮으신가요?")),
                priority="health",
                presence=context.presence,
                suppression=context.suppression,     # SAFETY 는 억제를 뚫는다
                comply_check="movement",
                comply_area=area,
                decision_id=decision_id,
                confidence=decision.confidence,
            )
            return timers          # 한 번에 하나만

        return timers
    
    
    # ------------------------------------------------------------ WAKE_ROUTINE

    def _wake_routine(self, context: Any) -> list[tuple[str, float]]:
        """기상 후 수분 섭취.

        시나리오 4. 기상하고 평소보다 오래 물을 마시지 않으면 권한다. hydration_lag 분포를 쓰는 것이 핵심이다 — "몇 시에" 가 아니라 "기상하고 몇 분 만에" 가 이 사람의 패턴이다.
        """
        timers: list[tuple[str, float]] = []
        wake = context.wake_fsm.state

        # ① 트리거 — 기상했고, 아직 안 마셨고, 집에 있어야 한다
        if wake.state != "AWAKE" or wake.wake_t0 is None:
            return timers
        if wake.hydration_done:
            return timers

        # 기상 직후 몇 분은 묻지 않는다. 일어나자마자 물을 마시는
        # 사람도 있고, 화장실부터 가는 사람도 있다.
        now = self._clock.now()
        grace = float(
            self._config.value("scenarios", "wake", "hydration_grace_sec", default=900)
        )
        if now - wake.wake_t0 < grace:
            return [("wake-hydration", wake.wake_t0 + grace)]

        # ② 판정 — 개인 분포에서 지금이 이상하게 늦은가
        decision = self._policy.evaluate_hydration(
            context.away,
            context.occupancy,
            context.suppression,
            wake_t0=wake.wake_t0,
            hydration_done=wake.hydration_done,
        )
        decision_id = self._emit_decision(decision)

        if not decision.candidate:      # 아직 평소 범위
            recheck = float(
                self._config.value("scenarios", "wake", "recheck_sec", default=300)
            )
            return [("wake-hydration", now + recheck)]

        # ③ 발송
        self._notifier.send(
            scenario="WAKE_ROUTINE",
            title=str(self._notify_value("WAKE_ROUTINE", "title", "수분 섭취")),
            text=str(self._notify_value("WAKE_ROUTINE", "text", "물 한 잔 드세요")),
            priority=str(self._notify_value("WAKE_ROUTINE", "priority", "normal")),
            presence=context.presence,
            suppression=context.suppression,
            comply_kind="hydration",
            comply_check="wake",
            decision_id=decision_id,
            confidence=decision.confidence,
        )
        return timers

    # ------------------------------------------------------------ 판정 발행

    def _emit_decision(self, d: Decision) -> str | None:
        """candidate 또는 reason 이 직전과 달라질 때만 발행한다."""
        key = (d.candidate, d.reason)
        if self._last.get(d.kind) == key:
            return None

        self._last[d.kind] = key
        decision_id = self._next_id()
        self._publish("hestia/intervention/decision", {
            "version": SCHEMA_VERSION,
            "sent_ts": int(self._clock.now()),
            "src_id": SRC_ID,
            "decision_id": decision_id,
            **d.payload(),
        }, False)
        log.debug("판정 %s %s candidate=%s", decision_id, d.kind, d.candidate)
        return decision_id

    # ------------------------------------------------------------ 내부

    def _notify_value(self, scenario: str, key: str, default: Any) -> Any:
        return self._config.notify_policy(scenario).get(key, default)

    def _next_id(self) -> str:
        self._seq += 1
        return f"d-{day_key(self._clock.now()).replace('-', '')}-{self._seq:03d}"

    def _area_occupied(self, area: str) -> bool:
        """그 구역에 사람이 있는가."""
        return any(
            isinstance(s, PresenceState) and s.present
            for s in self._world.sensors_of(area)
        )

    def _area_occupied(self, area: str) -> bool:
        """그 구역에 사람이 있는가."""
        return any(
            isinstance(s, PresenceState) and s.present
            for s in self._world.sensors_of(area)
        )

    def _still_since(self, area: str) -> float | None:
        """그 구역에서 정지가 시작된 시각. 움직이는 중이면 None."""
        spans = [
            s.still_since
            for s in self._world.sensors_of(area)
            if isinstance(s, PresenceState) and s.present and s.still_since is not None
        ]
        return min(spans) if spans else None

    def _notify_value(self, scenario: str, key: str, default: Any) -> Any:
        return self._config.notify_policy(scenario).get(key, default)