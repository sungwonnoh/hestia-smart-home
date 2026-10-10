"""시나리오 — 언제 무엇을 할지.
    policy 는 "지금이 평소와 다른가" 를 묻고, notify 는 "어떻게 전달하나" 를 안다. 여기는 그 사이 — "그래서 뭘 해야 하나".

각 시나리오는 셋을 한다.

    ① 트리거 조건 확인      context 와 FSM 플래그를 읽는다
    ② policy 로 판정        개인 분포에서 지금이 이상한가
    ③ 후보면 알림 발송      notifier 에 넘긴다

②의 결과는 후보가 아니어도 발행한다. 왜 개입하지 않았는지가 왜 개입했는지만큼 중요(명세의 intervention/decision).

지금은 HYDRATION_PROMPT / SAFETY / SLEEP_ROUTINE 셋. MEDICATION_PROMPT 는 뒤에.
"""

from __future__ import annotations

import logging
from typing import Any, Callable
from dataclasses import dataclass, replace

from .control import Controller
from .timeutil import KST, day_key, hhmm, minutes_since_midnight
from datetime import datetime

from .clock import Clock
from .config import Config
from .notify import Notifier
from .world import PresenceState, BedState, ClimateState, WorldState
from .policy import Decision, InterventionPolicy
from .fsm import T0Entry
from .model import MealPeak, peak_tail

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
        self._awake_areas: set[str] | None = None   # None = sleep_end 를 못 봤다
        self._seq = 0

        # 수분
        self._last_dispensed_at: float | None = None
        self._last_sleep_end_at: float | None = None
        self._notify_count = 0                 # 급수·sleep_end 에서 리셋
        self._last_notify_at: float | None = None
        self._daily_ml = 0
        self._hydration_day: str | None = None
        self._air_stress_sent = False          # HEAT + DRY 합산 하루 1회
        self._hot = False                      # 히스테리시스
        self._dry = False
        self._daily_short_sent = False
        self._last_away_state: str | None = None
        self._away_since: float | None = None
        self._returned_at: float | None = None
        self._return_sent = False

        # 식사
        self._meal_fired: set[tuple[int, str]] = set()   # (peak.center, 날짜)


    def tick(self, context: Any) -> list[tuple[str, float]]:
        """시나리오를 전부 훑는다. 조건에 안 맞으면 조용히 지나간다."""
        self._context = context
        timers: list[tuple[str, float]] = []
        timers += self._hydration_prompt(context)
        timers += self._safety(context)
        timers += self._sleep_routine(context)
        timers += self._meal_prompt(context)
        return timers

    def note_hydration(self, amount_ml: int) -> None:
        """정수기 급수. ContextEngine 이 이벤트마다 부른다."""
        now = self._clock.now()
        self._roll_hydration_day(now)
        self._last_dispensed_at = now
        self._daily_ml += amount_ml
        self._notify_count = 0          # 마셨으면 반복 카운터를 푼다

     # ------------------------------------------------------------ SLEEP_ROUTINE

    def _sleep_routine(self, context) -> list[tuple[str, float]]:
        """자는지 확인한다."""
        now = self._clock.now()

        # 내려둔 조명은 사용자가 끈 뒤에 조용히 되돌린다
        self._restore_if_off()

                # 내려둔 조명은 사용자가 끈 뒤에 조용히 되돌린다
        self._restore_if_off()
        self._track_awake_areas(context)

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
                self._last_sleep_end_at = woke_at
                self._notify_count = 0      # 깼으면 반복 카운터를 푼다
                self._awake_areas = set()
                self.asleep = None
                self._notify_asleep(None)
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
            ["IN_SOFA_AWAKE", "WATCHING_TV", "IN_BED_AWAKE", "SLEEPING"],
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
            awake_areas=self._take_awake_areas(area),
        )
        self.asleep = AsleepState(
            since=still_since, area=area, decision_id=decision_id,
        )
        self._notify_asleep(area)

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
        awake_areas: tuple[str, ...] | None = None,
    ) -> None:
        if self._t0log is None:
            return
        self._t0log.write(T0Entry(
            date=day_key(t0), type=type_, t0=t0,
            source="sensor", prompted=False, duration_sec=0.0,
            area=area, method=method, confidence=confidence,
            awake_areas=awake_areas,
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

    def _track_awake_areas(self, context: Any) -> None:
        """sleep_end 이후 들른 구역을 모은다."""
        if self._awake_areas is None or self.asleep is not None:
            return
        presence = getattr(context, "presence", None)
        if presence is not None and presence.user_area:
            self._awake_areas.add(presence.user_area)

    def _take_awake_areas(self, area: str | None) -> tuple[str, ...] | None:
        """잠든 구역은 뺀다 — area 에 이미 있다.
        None 은 '모른다' 다. 재시작으로 직전 sleep_end 를 못 본 경우이며,
        빈 배열('나가지 않았다')과 구별해야 배치가 잘못 잇지 않는다.
        """
        if self._awake_areas is None:
            return None
        out = tuple(sorted(self._awake_areas - {area}))
        self._awake_areas = None
        return out
    

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


    # ------------------------------------------------------------ HYDRATION_PROMPT

    def _hydration_prompt(self, context: Any) -> list[tuple[str, float]]:
        """수분 섭취.

        meal 과 달리 분포를 쓰지 않는다. 기상 기준이 사라져 hydration_lag 을
        잴 수 없고, 급수는 '몇 시에' 가 아니라 '얼마 만에' 의 문제다.
        reason 여섯이 한 시나리오를 공유하며, 사용자가 받는 것은 모두
        "물 한 잔 드세요" 다.
        """
        now = self._clock.now()
        self._roll_hydration_day(now)
        self._track_away(context, now)
        self._track_air(now)

        if self._in_pause(
            now,
            str(self._hyd("pause_start", "22:00")),
            str(self._hyd("pause_end", "06:00")),
        ):
            # 정숙 시간에 쌓인 1회성 조건은 버린다. 6시에 몰아서 보내면
            # 사용자는 '왜 지금' 을 알 수 없다.
            self._returned_at = None
            return []

        if self.asleep is not None:
            return []

        reason, at = self._hydration_reason(context, now)
        if reason is None:
            return self._hydration_timers(now)

        # 예약과 발송 사이에 마셨을 수 있다
        if self._last_dispensed_at is not None and self._last_dispensed_at > at:
            return self._hydration_timers(now)

        self._send_hydration(context, reason, now)
        return self._hydration_timers(now)


    # ------------------------------------------------------------ MEAL_PROMPT

    def _meal_prompt(self, context: Any) -> list[tuple[str, float]]:
        """끼니가 평소보다 늦은가.

        하루 전체 분포로 보면 아침이 늦어도 뒤의 점심·저녁 질량이 남아
        '아직 이르다' 가 된다. 배치가 나눠준 구간 안에서만 본다.
        """
        now = self._clock.now()

        if self.asleep is not None:
            return []
        if self._meal_in_pause(now):
            return []

        peaks = self._policy.store.peaks()
        if peaks is None:
            return []                        # 봉우리가 1개 이하 — 가를 수 없다

        minutes = minutes_since_midnight(now)
        peak = next((p for p in peaks if p.contains(minutes)), None)
        if peak is None:
            return self._meal_timers(peaks, minutes, now)   # 다음 구간 시작을 예약

        today = day_key(now)
        if (peak.center, today) in self._meal_fired:
            return self._meal_timers(peaks, minutes, now)

        d = self._policy.evaluate_meal(
            context.away, context.occupancy, context.suppression,
            meal_done=self._ate_in(peak, context, now),
            peak=peak,
        )
        decision_id = self._emit_decision(d)

        if not d.candidate:
            return self._meal_timers(peaks, minutes, now)

        self._send_meal(context, peak, decision_id, now)
        return self._meal_timers(peaks, minutes, now)

    def _meal_in_pause(self, now: float) -> bool:
        return self._in_pause(
            now,
            str(self._meal_cfg("pause_start", "21:00")),
            str(self._meal_cfg("pause_end", "05:00")),
        )
    

    # ------------------------------------------------------------ 판정 발행

    def _emit_decision(self, d: Decision) -> str | None:
        """candidate 또는 reason 이 직전과 달라질 때만 발행한다.
           끼니는 구간마다 따로 센다.
        """
        key = (d.candidate, d.reason, d.factors.get("peak_center"))
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

    def _hydration_reason(self, context: Any, now: float) -> tuple[str | None, float]:
        """어떤 이유로 권하나. 없으면 (None, 0).

        순서가 곧 우선순위다. 겹칠 때 하나만 나간다 — 사용자가 받는 것은
        어느 쪽이든 같은 알림이다.
        """
        # 외출 후 귀가 — LONG_GAP 이 거의 같은 것을 잡지만 문구가 다르다
        if (
            self._returned_at is not None
            and not self._return_sent
            and now - self._returned_at >= float(self._hyd("return_delay_sec", 600))
        ):
            return "RETURNED_HOME", self._returned_at

        # 더워짐·건조해짐 — 전이 시점에 한 번. 둘 다면 HEAT 가 우선
        if not self._air_stress_sent:
            if self._hot:
                return "HEAT_ONSET", now
            if self._dry:
                return "DRY_ONSET", now

        # 일일 권장량 — 20시에 한 번. 외출 중이면 그날은 건너뛴다
        hour = datetime.fromtimestamp(now, KST).hour
        if (
            not self._daily_short_sent
            and hour >= int(self._hyd("checkpoint_hour", 20))
            and self._daily_ml < int(self._hyd("daily_target_ml", 1500))
        ):
            return "DAILY_SHORT", now

        # 간격 — 마지막 급수 또는 sleep_end 중 늦은 쪽부터
        if self._notify_count >= int(self._hyd("max_repeat", 2)):
            return None, 0.0

        gap_from, gap, reason = self._hydration_gap()
        if gap_from is None:
            return None, 0.0
        if now - gap_from < gap:
            return None, 0.0

        # 알림 뒤 최소 간격
        renotify = float(self._hyd("renotify_sec", 7200))
        if self._last_notify_at is not None and now - self._last_notify_at < renotify:
            return None, 0.0

        return reason, gap_from

    def _hydration_gap(self) -> tuple[float | None, float, str]:
        """간격의 기준점과 길이. 자는 동안은 세지 않는다.

        sleep_end 가 더 늦으면 깬 뒤부터 2시간 — 밤새 무급수였으니
        아침 한 잔이 필요하고, 그 자리를 WAKE_ROUTINE 이 맡던 것이다.
        """
        drank = self._last_dispensed_at
        woke = self._last_sleep_end_at

        if woke is not None and (drank is None or woke > drank):
            return woke, float(self._hyd("sleep_end_gap_sec", 7200)), "SLEEP_END"
        if drank is None:
            return None, 0.0, "LONG_GAP"

        gap = float(
            self._hyd("short_gap_sec", 10800) if (self._hot or self._dry)
            else self._hyd("gap_sec", 14400)
        )
        return drank, gap, "LONG_GAP"

    # ------------------------------------------------------------ 내부

    def _notify_value(self, scenario: str, key: str, default: Any) -> Any:
        return self._config.notify_policy(scenario).get(key, default)

    def _notify_asleep(self, area: str | None) -> None:
        ctx = getattr(self, "_context", None)
        if ctx is not None:
            ctx.note_asleep(area)

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


    def _send_hydration(self, context: Any, reason: str, now: float) -> None:
        decision_id = self._emit_decision(Decision(
            kind="hydration",
            candidate=True,
            reason=reason,
            confidence=1.0,
            factors={
                "daily_ml": self._daily_ml,
                "notify_count": self._notify_count,
                "hot": self._hot,
                "dry": self._dry,
            },
        ))

        notify_id = self._notifier.send(
            scenario="HYDRATION_PROMPT",
            title=str(self._notify_value("HYDRATION_PROMPT", "title", "수분 섭취")),
            text=self._hydration_text(reason),
            priority=str(self._notify_value("HYDRATION_PROMPT", "priority", "normal")),
            presence=getattr(context, "presence", None),
            suppression=getattr(context, "suppression", None),
            comply_kind="hydration",
            comply_check="done",
            decision_id=decision_id,
        )
        if notify_id is None:
            return

        self._last_notify_at = now
        if reason in ("LONG_GAP", "SLEEP_END"):
            self._notify_count += 1
        elif reason in ("HEAT_ONSET", "DRY_ONSET"):
            self._air_stress_sent = True
        elif reason == "DAILY_SHORT":
            self._daily_short_sent = True
        elif reason == "RETURNED_HOME":
            self._return_sent = True

    def _hydration_text(self, reason: str) -> str:
        """맥락이 보이는 문구가 더 설득력 있다.

        권장량은 정수기 물만 세므로 실제보다 적게 나온다 — 생수나 커피는
        잡히지 않는다. 그래서 단정하지 않는다.
        """
        default = {
            "LONG_GAP": "물 한 잔 드세요",
            "SLEEP_END": "일어나셨네요, 물 한 잔 드세요",
            "HEAT_ONSET": "더운 날이에요, 물 한 잔 드세요",
            "DRY_ONSET": "공기가 건조해요, 물 한 잔 드세요",
            "DAILY_SHORT": "오늘 물을 조금 더 드시면 좋겠습니다",
            "RETURNED_HOME": "외출하셨네요, 물 한 잔 드세요",
        }[reason]
        return str(self._notify_value("HYDRATION_PROMPT", f"text_{reason}", default))

    def _track_away(self, context: Any, now: float) -> None:
        """AWAY -> HOME 전이. 2시간 넘게 나갔다 온 경우만 센다.

        away context 의 since 는 상태가 유지되는 동안 승계되므로,
        HOME 으로 바뀐 뒤에 읽으면 거의 0 이다. 나가 있던 길이는
        AWAY 인 동안 따로 들고 있어야 한다.

        UNKNOWN 에서 온 것은 제외한다 — 나갔는지 쓰러졌는지 모른다.
        """
        away = getattr(context, "away", None)
        if away is None:
            return

        if away.state == "AWAY":
            self._away_since = away.since
        elif self._last_away_state == "AWAY" and away.state == "HOME":
            if self._away_since is not None:
                gone = now - self._away_since
                if gone >= float(self._hyd("return_min_away_sec", 7200)):
                    self._returned_at = now
                    self._return_sent = False
            self._away_since = None

        self._last_away_state = away.state

    def _track_air(self, now: float) -> None:
        """덥거나 건조한가. 27.9 ↔ 28.1 로 울리지 않게 히스테리시스를 둔다.

        실내 센서만 본다. 폭염 특보(external/weather)는 토픽이 아직 없어
        붙이지 않았고, 들어오면 OR 로 합친다.
        """
        st = self._climate()
        if st is None:
            return

        hot_in = float(self._hyd("hot_temp_c", 28))
        hot_out = float(self._hyd("hot_exit_c", 26))
        dry_in = float(self._hyd("dry_humidity_pct", 35))
        dry_out = float(self._hyd("dry_exit_pct", 40))

        self._hot = (
            st.temperature_c >= hot_in if not self._hot
            else st.temperature_c > hot_out
        )
        self._dry = (
            st.humidity_pct <= dry_in if not self._dry
            else st.humidity_pct < dry_out
        )

    def _roll_hydration_day(self, now: float) -> None:
        today = day_key(now)
        if self._hydration_day != today:
            self._hydration_day = today
            self._daily_ml = 0
            self._air_stress_sent = False
            self._daily_short_sent = False

    def _hydration_timers(self, now: float) -> list[tuple[str, float]]:
        """다음에 조건이 성립할 만한 시각들. 센서가 조용해도 걸려야 한다."""
        out: list[tuple[str, float]] = []

        if self._returned_at is not None and not self._return_sent:
            out.append((
                "hydration-return",
                self._returned_at + float(self._hyd("return_delay_sec", 600)),
            ))

        gap_from, gap, _ = self._hydration_gap()
        if gap_from is not None and self._notify_count < int(self._hyd("max_repeat", 2)):
            out.append(("hydration-gap", gap_from + gap))
            if self._last_notify_at is not None:
                out.append((
                    "hydration-renotify",
                    self._last_notify_at + float(self._hyd("renotify_sec", 7200)),
                ))

        if not self._daily_short_sent:
            at = self._today_at(int(self._hyd("checkpoint_hour", 20)), now)
            if at > now:
                out.append(("hydration-daily", at))

        return [(k, t) for k, t in out if t > now]

    def _in_pause(self, now: float, start: str = "22:00", end: str = "06:00") -> bool:
        """권하지 않는 시간대."""
        start_min = hhmm(start)
        end_min = hhmm(end)
        dt = datetime.fromtimestamp(now, KST)
        minutes = dt.hour * 60 + dt.minute
        if start_min <= end_min:
            return start_min <= minutes < end_min
        return minutes >= start_min or minutes < end_min

    def _today_at(self, hour: int, now: float) -> float:
        dt = datetime.fromtimestamp(now, KST).replace(
            hour=hour, minute=0, second=0, microsecond=0
        )
        return dt.timestamp()

    def _climate(self) -> ClimateState | None:
        for st in self._world.sensors.values():
            if isinstance(st, ClimateState):
                return st
        return None

    def _hyd(self, key: str, default: Any) -> Any:
        return self._config.value("scenarios", "hydration", key, default=default)

    def _meal_cfg(self, key: str, default: Any) -> Any:
        return self._config.value("scenarios", "meal", key, default=default)


    def _ate_in(self, peak: MealPeak, context: Any, now: float) -> bool:
        """이 구간에 이미 먹었는가. DayState.meals 는 eat_t0 기준."""
        day = getattr(context, "day", None)
        if day is not None:
            today = day_key(now)
            for ts in day.meals:
                if day_key(ts) == today and peak.contains(minutes_since_midnight(ts)):
                    return True

        # 지금 먹는 중이면 묶음이 아직 안 닫혀 meals 에 없다.
        # 그 사이에 "식사 아직이신가요" 가 나가면 안 된다.
        activity = getattr(context, "activity", None)
        return activity is not None and activity.state in ("COOKING", "EATING")

    def _send_meal(self, context: Any, peak: MealPeak,
                   decision_id: str | None, now: float) -> None:
        notify_id = self._notifier.send(
            scenario="MEAL_PROMPT",
            title=str(self._notify_value("MEAL_PROMPT", "title", "식사")),
            text=self._meal_text(peak),
            priority=str(self._notify_value("MEAL_PROMPT", "priority", "normal")),
            presence=getattr(context, "presence", None),
            suppression=getattr(context, "suppression", None),
            comply_kind="meal",
            comply_check="done",
            decision_id=decision_id,
        )
        if notify_id is None:
            return
        self._meal_fired.add((peak.center, day_key(now)))

    def _meal_text(self, peak: MealPeak) -> str:
        """어느 끼니인지는 시각으로 말한다."""
        h, m = divmod(peak.center, 60)
        default = f"{h}시{m:02d}분쯤 드시던 식사, 아직이신가요"
        return str(self._notify_value("MEAL_PROMPT", "text", default))

    def _meal_timers(self, peaks, minutes: int, now: float) -> list[tuple[str, float]]:
        """센서가 조용해도 걸려야 한다.

        구간 안이면 tail 이 임계에 닿는 시각, 구간 밖이면 다음 구간 시작.
        """
        at = self._meal_deadline(peaks, minutes, now)
        return [("meal", at)] if at is not None and at > now else []

    def _meal_deadline(self, peaks, minutes: int, now: float) -> float | None:
        peak = next((p for p in peaks if p.contains(minutes)), None)
        if peak is None:
            return self._next_peak_start(peaks, minutes, now)

        dist = self._policy.store.distribution("meal_time")
        if dist is None:
            return None

        tail_max = float(
            self._config.value("thresholds", "meal", "tail_max", default=0.05)
        )
        step = int(dist["grid_step"])

        # 자정을 넘는 구간에서는 minutes 가 from_ 보다 작다.
        pos = minutes if minutes >= peak.from_ else minutes + 1440
        end = peak.from_ + self._span(peak)

        for m in range(pos + step, end + 1, step):
            if peak_tail(dist, peak, m % 1440) < tail_max:
                return now + (m - pos) * 60.0
        return None

    @staticmethod
    def _span(peak: MealPeak) -> int:
        """구간 길이. 자정을 넘으면 펴서 센다."""
        return peak.to - peak.from_ if peak.to > peak.from_ else peak.to + 1440 - peak.from_

    def _next_peak_start(self, peaks, minutes: int, now: float) -> float | None:
        """가장 가까운 다음 구간 시작."""
        gaps = [(p.from_ - minutes) % 1440 for p in peaks]
        return now + min(gaps) * 60.0 if gaps else None