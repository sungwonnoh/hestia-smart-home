"""시나리오 — 언제 무엇을 할지.
    policy 는 "지금이 평소와 다른가" 를 묻고, notify 는 "어떻게 전달하나" 를 안다. 여기는 그 사이 — "그래서 뭘 해야 하나".

각 시나리오는 셋을 한다.

    ① 트리거 조건 확인      context 와 FSM 플래그를 읽는다
    ② policy 로 판정        개인 분포에서 지금이 이상한가
    ③ 후보면 알림 발송      notifier 에 넘긴다

②의 결과는 후보가 아니어도 발행한다. 왜 개입하지 않았는지가 왜 개입했는지만큼 중요(명세의 intervention/decision).

지금은 WAKE_ROUTINE / SAFETY / SLEEP_ROUTINE 셋. MEDICATION_PROMPT 는 추가.
"""

from __future__ import annotations

import logging
from typing import Any, Callable
from dataclasses import dataclass, replace

from .control import Controller
from .timeutil import day_key

from .clock import Clock
from .config import Config
from .notify import Notifier
from .world import PresenceState, BedState, WorldState
from .policy import Decision, InterventionPolicy
from .fsm import T0Entry

log = logging.getLogger(__name__)

SRC_ID = "rpi5"
SCHEMA_VERSION = 1


@dataclass(frozen=True, slots=True)
class SleepProbe:
    """진행 중인 취침 확인."""

    method: str                      # banner / dim
    area: str | None
    started_at: float
    deadline: float
    still_sec: float                 # 프로브 시점의 정지 시간
    still_since: float               # 정지가 시작된 시각. sleep_start 의 t0
    notify_id: str | None = None
    target: str | None = None        # dim 일 때 조명 vid
    base: int | None = None          # 프로브 시작 시점 밝기. 단계 계산의 기준
    step: int = 0                    # 조명 단계 (0 → 1)



@dataclass(frozen=True, slots=True)
class AsleepState:
    """확정된 수면."""

    since: float                     # sleep_start 의 t0
    area: str | None
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
        t0log: Any = None,
    ) -> None:
        self._clock = clock
        self._config = config
        self._world = world
        self._policy = policy
        self._notifier = notifier
        self._controller = controller
        self._publish = publish
        self._suppression = suppression

        self._t0log = t0log
        # 직전 판정. candidate 나 reason 이 바뀔 때만 발행한다 —
        # 판정은 context 가 바뀔 때마다 일어나므로 매번 발행하면 과함.
        self._last: dict[str, tuple[bool, str]] = {}
        self._probe: SleepProbe | None = None
        self.asleep: AsleepState | None = None
        self._last_awake_at: float | None = None
        self._pending_restore: tuple[str, int] | None = None
        self._probe_base: dict[str, int] = {}
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

        # 내려둔 조명은 사용자가 끈 뒤에 조용히 되돌린다
        self._restore_if_off()

        if self._probe is not None:
            return self._check_probe(context, now)

        if self.asleep is not None:
            woke_at = self._woke_at(now)
            if woke_at is not None:
                self._log_sleep("sleep_end", woke_at)
                self._emit_decision(Decision(
                    kind="sleep", candidate=False, reason="WOKE",
                    factors={"area": self.asleep.area},
                ))
                self.asleep = None
            return []

        # AWAKE 로 답한 직후에는 다시 묻지 않는다
        gap = float(self._sleep_value("gap_after_awake_sec", 2700))
        if self._last_awake_at is not None and now - self._last_awake_at < gap:
            return [("sleep-probe", self._last_awake_at + gap)]

        area = self._sleep_area(context)
        if area is None:
            return []

        since = self._still_since(area)
        if since is None:
            return []                       # 움직이는 중

        # 떠볼 수단이 그 구역에 있나
        has_channel = self._tv_on(area) or self._light_in(area) is not None
        limit = float(self._sleep_value(
            "probe_still_sec" if has_channel else "probe_still_sec_no_channel",
            900 if has_channel else 1800,
        ))

        still = now - since
        if still < limit:
            return [("sleep-probe", since + limit)]

        if not has_channel:
            # 떠볼 수가 없다. 긴 정지만으로 확정한다.
            self._confirm_asleep(
                area=area, still_since=since, still_sec=still,
                method="none", now=now,
            )
            return []

        return self._send_probe(context, area, still, since, now)


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
        self, context, area: str, still: float, since: float, now: float
    ) -> list[tuple[str, float]]:
        timeout = float(self._sleep_value("probe_timeout_sec", 60))

        if self._suppression is not None:
            self._suppression.start_probe(stage=1, duration_sec=timeout)

        probe = (
            self._probe_banner(context, area, still, since, now, timeout)
            if self._tv_on(area)
            else self._probe_dim(area, still, since, now, timeout)
        )
        if probe is None:
            self._end_probe()
            return []

        self._probe = probe
        return [("sleep-probe", probe.deadline)]

    def _probe_banner(
        self, context, area: str, still: float, since: float,
        now: float, timeout: float,
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
            deadline=now + timeout, still_sec=still, still_since=since,
            notify_id=notify_id,
        )

    def _probe_dim(
        self, area: str, still: float, since: float,
        now: float, timeout: float,
    ) -> SleepProbe | None:
        """TV 가 꺼져 있으면 배너를 띄울 근거가 없다. 조명을 단계적으로 내려 반응을 본다."""
        target = self._light_in(area)
        if target is None:
            return None

        # 기준 밝기는 한 번만 읽는다. 매번 현재값을 읽으면
        # 내린 결과가 다음 기준이 되어 밝기가 복리로 줄어든다.
        base = self._probe_base.get(target)
        if base is None:
            st = self._world.device(target)
            cur = st.get("brightness") if st is not None else None
            if cur is None:
                return None
            base = int(cur)
            self._probe_base[target] = base

        if not self._dim_to(target, base, step=0):
            return None

        return SleepProbe(
            method="dim", area=area, started_at=now,
            deadline=now + timeout, still_sec=still, still_since=since,
            target=target, base=base, step=0,
        )

    def _dim_to(self, target: str, base: int, *, step: int) -> bool:
        """단계별 밝기를 쓴다. 절대값이 아니라 프로브 시작 시점 대비 비율이다."""
        levels = self._sleep_value("probe_dim_levels", [0.7, 0.3])
        if step >= len(levels):
            return False
        level = int(base * float(levels[step]))
        if level < 1:
            return False

        self._controller.set(
            target,
            power="ON",
            brightness=level,
            transition_ms=int(self._sleep_value("probe_transition_ms", 30000)),
            reason="SLEEP_ROUTINE",
            priority="low",
        )
        self._pending_restore = (
            target, int(self._sleep_value("restore_brightness", 100))
        )
        return True


    # 응답 확인
    def _check_probe(self, context, now: float) -> list[tuple[str, float]]:
        """반응이 있었나, 다음 단계인가, 시간이 다 됐나."""
        probe = self._probe
        assert probe is not None

        if self._responded(probe):
            # 밝기는 그대로 둔다. 보는 앞에서 되돌리면 조명 고장처럼 읽힌다.
            self._end_probe()
            self._probe = None
            self._last_awake_at = now
            self._emit_sleep_decision(probe, asleep=False, now=now)
            return []

        if now < probe.deadline:
            return [("sleep-probe", probe.deadline)]

        # 조명은 한 단계 더 내려본다
        if (
            probe.method == "dim"
            and probe.target is not None
            and probe.base is not None
            and self._dim_to(probe.target, probe.base, step=probe.step + 1)
        ):
            timeout = float(self._sleep_value("probe_timeout_sec", 60))
            if self._suppression is not None:
                self._suppression.start_probe(
                    stage=probe.step + 2, duration_sec=timeout
                )
            self._probe = replace(
                probe, step=probe.step + 1, deadline=now + timeout
            )
            return [("sleep-probe", self._probe.deadline)]

        # 무반응 — 잠든 것으로 본다
        self._end_probe()
        self._probe = None
        self._confirm_asleep(
            area=probe.area, still_since=probe.still_since,
            still_sec=probe.still_sec, method=probe.method, now=now,
            probe=probe,
        )
        return []

    def _responded(self, probe: SleepProbe) -> bool:
        """깨어 있다는 증거. 누르거나 명확히 움직이거나 되돌렸다."""
        if probe.notify_id is not None:
            pending = self._notifier.store.get(probe.notify_id)
            if pending is not None and pending.acked:
                return True
        if probe.target is not None and self._controller.reverted(probe.target):
            return True
        if probe.area is not None and self._moving_since(probe.area) is not None:
            return True                     # 뒤척임이 아닌 움직임
        return False

    def _end_probe(self) -> None:
        if self._suppression is not None:
            self._suppression.end_probe()

    # 확정과 로그
    def _confirm_asleep(
        self, *, area: str | None, still_since: float, still_sec: float,
        method: str, now: float, probe: SleepProbe | None = None,
    ) -> None:
        """수면 확정. t0 는 확정 시각이 아니라 정지가 시작된 시각이다."""
        confidence = float(self._sleep_value(
            "confidence", {"banner": 0.9, "dim": 0.9, "none": 0.6}
        ).get(method, 0.6))

        if probe is not None:
            decision_id = self._emit_sleep_decision(
                probe, asleep=True, now=now, confidence=confidence,
            )
        else:
            decision_id = self._emit_decision(Decision(
                kind="sleep", candidate=True, reason="ASLEEP_CONFIRMED",
                confidence=confidence,
                factors={
                    "area": area, "method": method,
                    "still_sec": round(still_sec),
                },
            ))

        self._log_sleep(
            "sleep_start", still_since,
            area=area, method=method, confidence=confidence,
        )
        self.asleep = AsleepState(
            since=still_since, area=area, decision_id=decision_id,
        )
        # 확정했으면 끈다. 되돌리기는 꺼진 뒤에 일어난다.
        if probe is not None and probe.target is not None:
            self._controller.set(
                probe.target, power="OFF",
                reason="SLEEP_ROUTINE", priority="low",
            )

    def _log_sleep(
        self, type_: str, t0: float, *,
        area: str | None = None, method: str | None = None,
        confidence: float | None = None,
    ) -> None:
        if self._t0log is None:
            return
        self._t0log.write(T0Entry(
            date=day_key(t0), type=type_, t0=t0,
            source="sensor", prompted=False, duration_sec=0.0,
            area=area, method=method, confidence=confidence,
        ))

    # 깸 판정
    def _woke_at(self, now: float) -> float | None:
        """깼으면 그 시각. 아니면 None."""
        if self.asleep is None or self.asleep.area is None:
            return now

        area = self.asleep.area
        bed = self._bed_in(area)
        if bed is not None and not bed.occupied and bed.changed_at > self.asleep.since:
            return bed.changed_at

        sustain = float(self._sleep_value("end_sustain_sec", 60))
        since = self._moving_since(area)
        if since is not None and now - since >= sustain:
            return since
        return None

    def _moving_since(self, area: str) -> float | None:
        """그 구역에서 명확한 움직임이 시작된 시각. 뒤척임은 걸러진다."""
        spans = [
            s.moving_since
            for s in self._world.sensors_of(area)
            if isinstance(s, PresenceState) and s.present and s.moving_since is not None
        ]
        return min(spans) if spans else None

    # decision과 헬퍼
    def _emit_sleep_decision(
        self, probe: SleepProbe, *, asleep: bool, now: float,
        confidence: float = 0.0,
    ) -> str | None:
        decision = Decision(
            kind="sleep",
            candidate=asleep,
            reason="ASLEEP_CONFIRMED" if asleep else "AWAKE",
            confidence=confidence if asleep else 0.0,
            factors={
                "area": probe.area,
                "method": probe.method,
                "still_sec": round(probe.still_sec),
                "probe_sec": round(now - probe.started_at),
            },
        )
        return self._emit_decision(decision)

    def _tv_on(self, area: str) -> bool:
        for vid in self._controller.devices_of_type("smart_tv", area):
            st = self._world.device(vid)
            if st is not None and st.get("power") == "ON":
                return True
        return False

    def _sleep_value(self, key: str, default: Any) -> Any:
        return self._config.value("scenarios", "sleep", key, default=default)

    # 조명 헬퍼
    def _light_in(self, area: str) -> str | None:
        """그 구역의 켜진 조명. 꺼진 조명을 켜는 것은 프로브가 아니라 방해다."""
        for vid in self._controller.devices_of_type("smart_light", area):
            st = self._world.device(vid)
            if st is not None and st.get("power") == "ON":
                return vid
        return None

    def _restore_if_off(self) -> None:
        """사용자가 불을 끈 뒤에 밝기를 되돌린다."""
        if self._pending_restore is None:
            return
        target, level = self._pending_restore
        st = self._world.device(target)
        if st is None or st.get("power") == "ON":
            return

        self._probe_base.pop(target, None)
        self._pending_restore = None
        self._controller.set(
            target, brightness=level,
            reason="SLEEP_ROUTINE", priority="low",
        )
    

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
        """HYDRATION_PROMPT로 교체 예정"""
        return []

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

    def _still_since(self, area: str) -> float | None:
        """그 구역에서 정지가 시작된 시각. 움직이는 중이면 None."""
        spans = [
            s.still_since
            for s in self._world.sensors_of(area)
            if isinstance(s, PresenceState) and s.present and s.still_since is not None
        ]
        return min(spans) if spans else None

    def _bed_in(self, area: str) -> BedState | None:
        """그 구역의 압력 패드. 침대와 소파 둘 다 type="bed" 다."""
        for s in self._world.sensors_of(area):
            if isinstance(s, BedState):
                return s
        return None