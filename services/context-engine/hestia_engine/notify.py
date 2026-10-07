"""알림 전달.

판정 결과를 받아 실제로 내보내고 반응을 끝까지 추적한다.

    send → 채널 선택 → notify/push 발행
         → ack 대기 ─ 무응답 → 에스컬레이션 → 포기
         → SEEN 수신 → cancel 발행
         → comply 관측 → intervention/outcome 발행

한 건이 수십 분에 걸쳐 상태를 바꾼다. 지금까지 만든 것 중 가장 오래 사는 객체이고, 그래서 PendingNotify 를 따로 두고 타이머로 깨운다.

comply 판정은 이벤트를 직접 보지 않고 wake FSM 의 _at 시각을 읽는다. (FSM 이 이미 그 판정을 하고 있으므로 중복을 피한다.) 
발송 시점의 _at 을 baseline 으로 기억해 둔다.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any, Callable

from .clock import Clock
from .config import Config
from .context import PresenceContext, SuppressionContext
from .timers import Scheduler
from .timeutil import KST, day_key
from .world import WorldState

log = logging.getLogger(__name__)

SRC_ID = "rpi5"
SCHEMA_VERSION = 1

VOICE = "voice"          # RPi4 스피커. device_type 이 아닌 예약값

# 종료 사유 — outcome 에 실리지는 않지만 로그와 테스트에서 쓴다.
CLOSED_SEEN = "SEEN"
CLOSED_TIMEOUT = "TIMEOUT"
CLOSED_EXPIRED = "EXPIRED"
CLOSED_CANCELLED = "CANCELLED"

# 화면에 문구를 띄울 수 없는 기기 종류와 그것을 대신할 종류
PROXY_TYPES = {
    "smart_tv": "display_node",
    "smart_fridge": "display_node",
}

# ==================================================================== 진행 중인 알림


@dataclass(frozen=True, slots=True)
class PendingNotify:
    """발송했고 아직 끝나지 않은 알림 한 건."""

    notify_id: str                           # 알림 id
    scenario: str                            # 어떤 시나리오의 알림인지
    priority: str                            # 우선순위
    sent_at: float                           # 알림 발생 시점
    ack_deadline: float                      # 이 시각까지 무응답이면 다음 단계로(에스컬레이션 할 때마다 갱신)
    expiry_at: float                         # 이 시각이 지나면 포기(이 알림 전체의 수명 - 고정)
    comply_until: float                      # comply: 사용자가 알림대로 실제로 행동했는가 / 관측 창 종료. 여기서 outcome 확정
    channels: tuple[str, ...] = ()           # 이번 단계에서 알림을 보낸 대상
    channels_tried: tuple[str, ...] = ()     # L3 학습 재료. 실패한 채널도 남긴다.(누적, 같은 채널 반복은 하지 않는다.)
    escalation_level: int = 1                # 1: 근접 기기 배너, 2: 음성
    requires_ack: bool = False               # 확인이 필요한 알림인가
    acked: bool = False                      # SEEN을 받았는가
    ack_type: str | None = None              # DELIVERED: 채널 도달, SEEN: 사용자 확인
    decision_id: str | None = None           # 어느 판정에서 나온 알림인지 (판정 id)
    confidence: float = 0.0                  # 해당 판정의 확신도. outcome에 포함
    comply_kind: str | None = None           # hydration / meal / medication
    comply_check: str | None = None          # wake / movement. 판정 근거의 종류
    comply_area: str | None = None           # movement 판정 대상 구역
    area: str | None = None                  # 알림이 겨냥한 구역 = 사용자 위치
    proxy_for: str | None = None             # 대행 중이면 원래 기기 종류
    baseline_at: float | None = None         # 발송 시점의 _at. 이보다 뒤여야 comply
    closed_at: float | None = None           # 알림이 닫힌 시각. None이면 진행 중
    closed_reason: str | None = None         # SEEN / TIMEOUT / EXPIRED / CANCELLED

    @property
    def is_open(self) -> bool:              # 현재 진행 중인 알림인가. 진행 중이면 True
        return self.closed_at is None


class NotifyStore:
    """진행 중인 알림을 보관한다.
    ModelStore 와 달리 여러 건을 동시에 들고, 타이머가 각각을 깨운다.
    """

    def __init__(self) -> None:
        self._pending: dict[str, PendingNotify] = {}    # 발송했고, 아직 끝나지 않은 알림에 대한 객체(PendingNotify)를 value로 갖는 dict

    def add(self, n: PendingNotify) -> None:
        self._pending[n.notify_id] = n

    def get(self, notify_id: str) -> PendingNotify | None:
        return self._pending.get(notify_id)

    def update(self, n: PendingNotify) -> None:
        self._pending[n.notify_id] = n

    def close(self, notify_id: str, reason: str, now: float) -> PendingNotify | None:
        """닫고 돌려준다. 이미 닫혔으면 None — 중복 outcome 을 막는다."""
        n = self._pending.get(notify_id)
        if n is None or not n.is_open:
            return None
        closed = replace(n, closed_at=now, closed_reason=reason)        # 기존 객체(PendingNotify)를 복사하면서, 일부 필드만 바꾼 새 객체를 만듦, 원본 n은 그래도, 새 객체가 반환됨
        self._pending[notify_id] = closed
        return closed

    def open_notifies(self) -> tuple[PendingNotify, ...]:       # 현재 진행중인 알림들에 대한 튜플 반환
        return tuple(n for n in self._pending.values() if n.is_open)

    def of_scenario(self, scenario: str) -> tuple[PendingNotify, ...]:      # 해당 시나리오에 대해 발송한 알림들
        return tuple(n for n in self._pending.values() if n.scenario == scenario)

    def has_open(self, scenario: str) -> bool:
        """같은 시나리오가 진행 중인가. 중복 발송을 막는다."""
        return any(n.is_open for n in self.of_scenario(scenario))

    def purge(self, before: float) -> int:
        """outcome 까지 끝난 오래된 건을 지운다.
        닫힌 뒤에도 comply 창이 남아 있을 수 있으므로 comply_until 을 기준으로 삼는다.
        """
        stale = [
            nid for nid, n in self._pending.items()
            if not n.is_open and n.comply_until < before
        ]
        for nid in stale:
            del self._pending[nid]
        return len(stale)

    def __len__(self) -> int:
        return len(self._pending)


# ==================================================================== 채널 선택


class ChannelSelector:
    """명세의 채널 선택 ①단계.
        : device_type 이 affinity 에 포함 + area 가 presence.user_area 와 일치 + power == ON

        voice 는 별도 경로다. device_type 이 아니므로 위 필터를 통과할 수 없고, area 를 따지지 않으며(집 안 어디서든 들린다), quiet_hours 에 막힌다.
    """

    def __init__(self, clock: Clock, config: Config, world: WorldState) -> None:
        self._clock = clock
        self._config = config
        self._world = world

    def select(
        self,
        scenario: str,
        presence: PresenceContext | None,
        *,
        level: int = 1,
        exclude: tuple[str, ...] = (),
    ) -> tuple[str, ...]:
        """이 단계에서 쓸 채널 반환. 비면 보낼 곳이 없다는 뜻이다.
            exclude는 이미 시도한 채널을 빼는 용도
        """
        affinity = tuple(self._policy(scenario, "affinity", default=[]))
        # affinity: 해당 시나리오에 어울리는 기기 종류 목록, 예: "수분 복약 알림은 냉장고를 먼저, 안되면 TV로"

        # 2단계는 음성으로 전환한다 (명세의 에스컬레이션)
        if level >= 2:
            if VOICE in exclude or not self._voice_allowed(scenario):
                return ()
            return (VOICE,)

        area = presence.user_area if presence is not None else None
        found = [
            vid for vid in self._candidates(affinity, area)     # _candidates(): 기기 종류 목록을 실제 기기 ID 목록으로 변환
            if vid not in exclude
        ]
        if found:
            return tuple(found)

        # 근접 기기가 없으면 음성으로
        if VOICE not in exclude and self._voice_allowed(scenario):
            return (VOICE,)
        return ()

    # ------------------------------------------------------------ 내부

    def _candidates(self, affinity: tuple[str, ...], area: str | None) -> list[str]:
        """affinity 순서를 보존한다 — 명세가 정한 선호 순위다.
            알림을 띄울 수 없는 종류(TV, 냉장고 화면)는 대행 종류로 바꿔 찾는다.
        """
        out: list[str] = []
        for want in affinity:
            if want == VOICE:
                continue                        # voice 는 별도 경로
            actual = PROXY_TYPES.get(want, want)    # want는 affinity에서 온 원하는 종류, actual은 실제로 찾을 종류 (PROXY_TYPE에 없으면 actual에 want를 그대로 돌려줌)
            for vid in self._config.channels(area):
                d = self._config.device(vid)
                if d is None or d.device_type != actual:
                    continue
                if not self._powered(vid):
                    continue
                if vid not in out:
                    out.append(vid)
        return out

    def proxy_for(self, scenario: str, vid: str) -> str | None:
        """그 채널이 대신 띄우고 있는 원래 기기 종류. 없으면 None.
            디스플레이가 '[TV 대행]' 을 띄울 근거다.
        """
        d = self._config.device(vid)
        if d is None:
            return None

        for want in self._policy(scenario, "affinity", default=[]):
            if want == d.device_type:
                return None             # 자기가 1순위면 대행이 아니다
            if PROXY_TYPES.get(want) == d.device_type:
                return want
        return None
    
    def _powered(self, vid: str) -> bool:
        """명세: power == ON 인 기기만 후보다.

        state 를 아직 못 받았으면 보낸다 — 꺼졌다는 근거가 없는데 후보에서 빼면 기동 직후 모든 알림이 막힌다.
        """
        st = self._world.device(vid)    # vid에 해당하는 DeviceState 객체
        if st is None:
            return True
        power = st.fields.get("power")
        return power is None or power == "ON"   # 해당 기기의 power가 ON이면 True

    def _voice_allowed(self, scenario: str) -> bool:
        """quiet_hours 에는 음성을 쓰지 않는다. SAFETY 는 예외."""
        if scenario == "SAFETY":
            return True
        return not self._in_quiet_hours()       # 현재 시간이 quiet_hours 라면, voice_allowed: False

    def _in_quiet_hours(self) -> bool:
        window = self._config.value("limits", "quiet_hours", default=None)      # window: policy.toml의 [limits] 안에 있는 quiet_hour에 해당하는 딕셔너리
        if not window:
            return False
        now = datetime.fromtimestamp(self._clock.now(), KST)
        minutes = now.hour * 60 + now.minute        # 현재 시각을 분으로
        start = _hhmm(window.get("start", "22:00"))
        end = _hhmm(window.get("end", "07:00"))
        if start <= end:
            return start <= minutes < end
        return minutes >= start or minutes < end    # 자정을 넘는 창

    def _policy(self, scenario: str, key: str, default: Any = None) -> Any:
        """Config 가 default 위에 시나리오 항목을 얹고 DEMO 배수까지 적용한다."""
        return self._config.notify_policy(scenario).get(key, default)


def _hhmm(value: str) -> int:
    hour, minute = value.split(":")
    return int(hour) * 60 + int(minute)


# ==================================================================== 총량 제한


class NotifyLimits:
    """알림별 규칙과 별개의 전체 상한.
        각각은 규칙을 지켰는데 총량이 과한 상황을 막는다.
        health 는 폐기하지 않는다.
    """

    def __init__(self, clock: Clock, config: Config) -> None:
        self._clock = clock
        self._config = config
        self._day: str | None = None
        self._count = 0
        self._last_sent_at: float | None = None

    def allows(self, priority: str) -> tuple[bool, str | None]:
        """(보내도 되는가, 안 되는 이유)."""
        now = self._clock.now()
        self._roll_day(now)

        if priority == "health":
            return True, None                   # 폐기하지 않는다

        # 하루 최대 알림 수 초과
        max_per_day = int(self._config.value("limits", "max_per_day", default=8))
        if self._count >= max_per_day:
            return False, "DAILY_LIMIT"

        # 알림 사이의 최소 간격
        gap = float(self._config.value("limits", "min_interval_sec", default=1800))
        if self._last_sent_at is not None and now - self._last_sent_at < gap:
            return False, "MIN_INTERVAL"

        return True, None

    def note_sent(self) -> None:        # 오늘 알림을 몇 건 보냈는지, 마지막으로 언제 보냈는지 기록
        now = self._clock.now()
        self._roll_day(now)
        self._count += 1
        self._last_sent_at = now

    @property
    def sent_today(self) -> int:
        self._roll_day(self._clock.now())
        return self._count

    def _roll_day(self, now: float) -> None:    # 날짜가 바뀌었으면 하루 발송 카운터를 0으로 되돌림
        today = day_key(now)
        if self._day != today:
            self._day = today
            self._count = 0


# ==================================================================== Notifier


class Notifier:
    """알림 한 건의 생애를 관리한다.
       publish 는 Engine 이 주입한다 — 엔진과 같은 Publisher 를 쓰므로 Replay 에서도 똑같이 기록된다.
    """

    def __init__(
        self,
        clock: Clock,
        config: Config,
        world: WorldState,
        scheduler: Scheduler,
        publish: Callable[[str, dict[str, Any], bool], None],
        wake_fsm: Any | None = None,
    ) -> None:
        self._clock = clock
        self._config = config
        self._world = world
        self._sched = scheduler
        self._publish = publish
        self._wake = wake_fsm

        self.store = NotifyStore()
        self.channels = ChannelSelector(clock, config, world)
        self.limits = NotifyLimits(clock, config)
        self._seq = 0

    # ------------------------------------------------------------ 발송

    def send(
        self,
        *,
        scenario: str,
        title: str,
        text: str,
        priority: str = "normal",
        presence: PresenceContext | None = None,
        suppression: SuppressionContext | None = None,
        comply_kind: str | None = None,
        comply_check: str | None = None,
        comply_area: str | None = None,
        decision_id: str | None = None,
        confidence: float = 0.0,
    ) -> str | None:
        """알림을 발송한다. 못 보내면 None 과 함께 이유를 로그에 남긴다."""
        now = self._clock.now()

        # 같은 시나리오가 진행 중이면 겹쳐 보내지 않는다
        if self.store.has_open(scenario):
            log.debug("%s 진행 중 — 발송 생략", scenario)
            return None

        if suppression is not None and not suppression.allows(scenario):
            log.debug("%s 억제됨 (%s)", scenario, suppression.reason)
            return None

        ok, why = self.limits.allows(priority)      # 알림 총량 조건에서, (현재 알림을 보낼 수 있는지, 그 이유)
        if not ok:
            log.info("%s 총량 제한 — %s", scenario, why)
            return None

        chosen = self.channels.select(scenario, presence, level=1)
        if not chosen:
            log.info("%s 보낼 채널 없음", scenario)
            return None

        notify_id = self._next_id(now)
        requires_ack = bool(self._policy(scenario, "requires_ack", default=False))
        deadline = now + float(self._policy(scenario, "ack_deadline_sec", default=600))
        expiry = now + float(self._policy(scenario, "expiry_sec", default=3600))
        comply_min = float(self._policy(scenario, "comply_window_min", default=30))

        pending = PendingNotify(    # 발송했고, 아직 끝나지 않은 알림에 대한 객체
            notify_id=notify_id,
            scenario=scenario,
            priority=priority,
            sent_at=now,
            ack_deadline=deadline,
            expiry_at=expiry,
            comply_until=now + comply_min * 60,
            channels=chosen,
            channels_tried=chosen,
            area=presence.user_area if presence is not None else None,
            proxy_for=self.channels.proxy_for(scenario, chosen[0]),
            requires_ack=requires_ack,
            decision_id=decision_id,
            confidence=confidence,
            comply_kind=comply_kind,
            comply_check=comply_check,
            comply_area=comply_area,
            baseline_at=self._done_at(comply_kind),
        )
        self.store.add(pending)
        self.limits.note_sent()

        self._push(pending, title, text)
        self._arm(pending)
        log.info("알림 발송 %s %s → %s", notify_id, scenario, chosen)
        return notify_id

    # ------------------------------------------------------------ 수신

    def on_ack(self, notify_id: str, ack_type: str, src_id: str) -> None:
        """노드가 보낸 ack.
        cancel 은 SEEN 수신 시에만 발행한다. DELIVERED 는 채널 도달 확인용이며 에스컬레이션을 멈추지 않는다.
        """
        n = self.store.get(notify_id)
        if n is None or not n.is_open:
            return

        if ack_type == "DELIVERED":
            self.store.update(replace(n, ack_type="DELIVERED"))
            return

        if ack_type != "SEEN":
            log.warning("알 수 없는 ack_type: %s", ack_type)
            return

        self.store.update(replace(n, acked=True, ack_type="SEEN"))
        self._cancel(notify_id)
        self._sched.cancel(f"notify-deadline-{notify_id}")
        self._sched.cancel(f"notify-expiry-{notify_id}")

        # 봤다고 한 것이지 했다는 뜻은 아니다. comply 창은 계속 돈다
        if n.comply_check is None:
            self._close(notify_id, CLOSED_SEEN)

    # ------------------------------------------------------------ 타이머

    def _arm(self, n: PendingNotify) -> None:
        if n.requires_ack:
            self._sched.at(
                n.ack_deadline,
                lambda: self._on_deadline(n.notify_id),
                key=f"notify-deadline-{n.notify_id}",
            )
        self._sched.at(
            n.expiry_at,
            lambda: self._on_expiry(n.notify_id),
            key=f"notify-expiry-{n.notify_id}",
        )
        if n.comply_check is not None:
            self._sched.at(
                n.comply_until,
                lambda: self._on_comply_window(n.notify_id),
                key=f"notify-comply-{n.notify_id}",
            )

    def _on_deadline(self, notify_id: str) -> None:
        """무응답. 다음 단계로 올리거나 포기한다.
            최대 2단계. 1단계 근접 기기 배너 → 2단계 음성.
        """
        n = self.store.get(notify_id)
        if n is None or not n.is_open or n.acked:
            return

        now = self._clock.now()
        max_level = int(self._policy(n.scenario, "max_escalation", default=2))
        unlimited = max_level == 0      # max_levle이 0이면, unlimited-> True

        if not unlimited and n.escalation_level >= max_level:
            self._close(notify_id, CLOSED_TIMEOUT)
            return

        chosen = self.channels.select(
            n.scenario, None,
            level=n.escalation_level + 1,
            exclude=n.channels_tried,
        )
        if not chosen:      # 더 이상 보낼 채널이 없다면 📍
            self._close(notify_id, CLOSED_TIMEOUT)
            return

        deadline = now + float(
            self._policy(n.scenario, "ack_deadline_sec", default=600)
        )
        escalated = replace(
            n,
            escalation_level=n.escalation_level + 1,
            channels=chosen,
            channels_tried=n.channels_tried + chosen,
            proxy_for=self.channels.proxy_for(n.scenario, chosen[0]),       # 에스컬레이션에서 proxy_for 갱신
            ack_deadline=deadline,
        )
        self.store.update(escalated)

        title = str(self._policy(n.scenario, "title", default=""))
        text = str(self._policy(n.scenario, "text", default=""))
        self._push(escalated, title, text)
        self._sched.at(
            deadline,
            lambda: self._on_deadline(notify_id),
            key=f"notify-deadline-{notify_id}",
        )
        log.info("에스컬레이션 %s → %s 단계 %s",
                 notify_id, escalated.escalation_level, chosen)

    def _on_expiry(self, notify_id: str) -> None:       # 알림 만료 시각에 도달
        n = self.store.get(notify_id)
        if n is None or not n.is_open:
            return
        self._cancel(notify_id)
        self._close(notify_id, CLOSED_EXPIRED)

    def _on_comply_window(self, notify_id: str) -> None:
        """관측 창 종료.(comply를 기다리는 시간이 끝남) 
           여기서 outcome 이 확정된다."""
        n = self.store.get(notify_id)
        if n is None:
            return
        if n.is_open:
            self._close(notify_id, CLOSED_TIMEOUT)
        else:
            self._emit_outcome(self.store.get(notify_id))

    # ------------------------------------------------------------ 발행

    def _push(self, n: PendingNotify, title: str, text: str) -> None:
        payload = {
            "version": SCHEMA_VERSION,
            "sent_ts": int(self._clock.now()),
            "src_id": SRC_ID,
            "notify_id": n.notify_id,
            "scenario": n.scenario,
            "priority": n.priority,
            "channels": list(n.channels),
            "area": n.area,
            "requires_ack": n.requires_ack,
            "ack_deadline": int(n.ack_deadline),
            "escalation_level": n.escalation_level,
            "payload": {"title": title, "text": text},
        }
        if n.proxy_for is not None:
            payload["proxy_for"] = n.proxy_for      # 디스플레이가 다른 가전의 대행일 경우, [__대행]을 띄우게 함
        self._publish("hestia/notify/push", payload, False)

        # 음성 채널은 RPi4 가 별도 토픽으로 받는다
        if VOICE in n.channels:
            self._publish("hestia/notify/speak", {
                "version": SCHEMA_VERSION,
                "sent_ts": int(self._clock.now()),
                "src_id": SRC_ID,
                "notify_id": n.notify_id,
                "text": text,
                "cache_key": n.scenario.lower(),
                "priority": n.priority,
            }, False)

    def _cancel(self, notify_id: str) -> None:
        self._publish("hestia/notify/cancel", {
            "version": SCHEMA_VERSION,
            "sent_ts": int(self._clock.now()),
            "src_id": SRC_ID,
            "notify_id": notify_id,
        }, False)

    def _close(self, notify_id: str, reason: str) -> None:
        closed = self.store.close(notify_id, reason, self._clock.now())
        if closed is None:
            return
        self._sched.cancel(f"notify-deadline-{notify_id}")
        self._sched.cancel(f"notify-expiry-{notify_id}")

        # comply 창이 아직 남았으면 outcome 은 그때 확정한다 (명세:
        # 확정 시점에 한 번만 발행)
        if closed.comply_check is not None and self._clock.now() < closed.comply_until:
            return
        self._emit_outcome(closed)

    def _emit_outcome(self, n: PendingNotify | None) -> None:
        if n is None:
            return
        complied, evidence, delay = self._comply(n)
        self._publish("hestia/intervention/outcome", {
            "version": SCHEMA_VERSION,
            "sent_ts": int(self._clock.now()),
            "src_id": SRC_ID,
            "intervention_id": f"i-{n.notify_id}",
            "type": "notify",
            "scenario": n.scenario,
            "refs": [n.notify_id],
            "confidence": round(n.confidence, 3),
            "user_response": {
                "acked": n.acked,
                "complied": complied,
                "evidence": evidence,
                "delay_sec": delay,
                "channels_tried": list(n.channels_tried),
            },
        }, False)
        log.info("outcome %s acked=%s complied=%s", n.notify_id, n.acked, complied)

    # ------------------------------------------------------------ comply 판정

    def _comply(self, n: PendingNotify) -> tuple[bool, str | None, float | None]:       # complied, evidence, delay
        """관측 창 안에 해당 행동이 일어났는가.
           시나리오마다 판정 근거가 다르다. - 근거의 종류를 발송 시점에 받아 여기서 분기
        """
        match n.comply_check:
            case "wake":
                return self._comply_wake(n)
            case "movement":
                return self._comply_movement(n)
            case _:
                return False, None, None
            

    def _comply_wake(self, n: PendingNotify) -> tuple[bool, str | None, float | None]:
        """FSM 의 _at 시각을 읽는다.
           발송 시점의 값(baseline_at)보다 뒤여야 이 알림 덕분이다 — 09:00 에 이미 물을 마셨는데 09:30 알림의 성과로 기록하면 L3 가 엉뚱한 채널을 학습한다.
        """
        if n.comply_kind is None or self._wake is None:
            return False, None, None

        done_at = self._done_at(n.comply_kind)
        if done_at is None or done_at == n.baseline_at:
            return False, None, None
        if done_at < n.sent_at or done_at > n.comply_until:
            return False, None, None

        evidence = {
            "hydration": "dispensed",
            "meal": "activity:EATING",
            "medication": "notify:ack",
        }.get(n.comply_kind, n.comply_kind)
        return True, evidence, round(done_at - n.sent_at)
    

    def _comply_movement(self, n: PendingNotify) -> tuple[bool, str | None, float | None]:
        """정지가 풀렸는가. '괜찮으신가요' 에 대한 응답은 움직임이다.

        still_sec 이 0 이면 지금 움직이는 중이고, 발송 시점보다 짧으면 한 번 끊겼다가 다시 멈춘 것이다. (어느 쪽이든 그 사이에 움직였다는 뜻)

        delay_sec 은 알 수 없다 — 언제 움직였는지가 아니라 '지금 움직이고 있나' 만 보이기 때문
        """
        if n.comply_area is None:
            return False, None, None

        still = self._world.still_sec(n.comply_area)
        if still == 0.0:
            return True, f"movement:{n.comply_area}", None

        # 발송 후 경과보다 정지가 짧으면 중간에 한 번 움직인 것이다
        elapsed = self._clock.now() - n.sent_at
        if still < elapsed:
            return True, f"movement:{n.comply_area}", round(elapsed - still)

        return False, None, None
    

    def _done_at(self, kind: str | None) -> float | None:
        if kind is None or self._wake is None:
            return None
        return self._wake.done_at(kind)

    # ------------------------------------------------------------ 내부

    def _next_id(self, now: float) -> str:
        self._seq += 1
        return f"n-{day_key(now).replace('-', '')}-{self._seq:03d}"

    def _policy(self, scenario: str, key: str, default: Any = None) -> Any:
        """Config 가 default 위에 시나리오 항목을 얹고 DEMO 배수까지 적용한다."""
        return self._config.notify_policy(scenario).get(key, default)