"""Engine — 모든 것을 엮는 진입점.

ingest() 가 유일한 입구다.

    parse → world.apply → context 재계산 → 발행

여기까지 만들어온 층들이 전부 이 함수 하나로 모인다.
그리고 Sink 프로토콜을 만족하므로 Replay 와 MQTT 배선이 같은 것을 꽂는다.

발행은 Publisher 뒤에 둔다. Clock / Sink / T0Log 와 같은 이유다 —
브로커가 끼면 네트워크 지연과 retained 잔존이 변수로 들어와
'같은 입력 같은 출력'이 깨진다. 2단계는 Replay 로 검증하는 것이 목적이므로
RecordingPublisher 로 개발하고, MqttPublisher 는 배선할 때 붙인다.
"""

from __future__ import annotations

import logging
from typing import Any, Protocol, runtime_checkable

from .clock import Clock
from .config import Config
from .context import Context, ContextEngine
from .messages import Message, parse
from .timers import Scheduler
from .world import WorldState
from .model import ModelStore
from .policy import InterventionPolicy
from .notify import Notifier
from .scenarios import ScenarioRunner

log = logging.getLogger(__name__)

SRC_ID = "rpi5"
SCHEMA_VERSION = 1


# ==================================================================== Publisher


@runtime_checkable
class Publisher(Protocol):
    """발행 대상을 추상화한다. 엔진은 어디로 가는지 모른다."""

    def publish(self, topic: str, payload: dict[str, Any], *, retain: bool) -> None: ...


class RecordingPublisher:
    """리스트에 모은다. Replay 와 테스트용.

    실패도 지연도 없으므로 결정성이 유지된다.
    """

    def __init__(self, echo: bool = False) -> None:
        self.published: list[tuple[float, str, dict[str, Any]]] = []
        self._echo = echo

    def publish(self, topic: str, payload: dict[str, Any], *, retain: bool) -> None:
        self.published.append((payload.get("sent_ts", 0.0), topic, payload))
        if self._echo:
            print(f"  → {topic}  {payload}")

    def of_topic(self, topic: str) -> tuple[dict[str, Any], ...]:
        return tuple(p for _, t, p in self.published if t == topic)

    def last(self, topic: str) -> dict[str, Any] | None:
        found = self.of_topic(topic)
        return found[-1] if found else None

    @property
    def count(self) -> int:
        return len(self.published)


class NullPublisher:
    """아무것도 하지 않는다. 발행이 필요 없는 경우."""

    def publish(self, topic: str, payload: dict[str, Any], *, retain: bool) -> None:
        pass


# ==================================================================== Engine


class Engine:
    """ingest() 하나로 들어와 발행까지 나간다.

    recv_ts 를 인자로 받지 않는다 — 주입받은 Clock 에게 묻는다.
    그래서 호출하는 쪽이 브로커 콜백이든 Replay 루프든 구별되지 않는다.
    """

    def __init__(
        self,
        clock: Clock,
        config: Config,
        world: WorldState,
        scheduler: Scheduler,
        publisher: Publisher | None = None,
        t0log: Any | None = None,
    ) -> None:
        self._clock = clock
        self._config = config
        self._world = world
        self._sched = scheduler
        self._pub = publisher or NullPublisher()

        # RPi4 배치가 발행한 모델. retained 라 재시작 시 즉시 복원된다.
        self.models = ModelStore()

        # 타이머가 바꾼 context 도 같은 경로로 발행되어야 한다.
        # 이 콜백이 없으면 시간 경과로만 일어나는 전이가 전부 묻힌다.
        self.context = ContextEngine(
            clock, config, world, scheduler,
            on_change=self._on_context_change,
            t0log=t0log,
            models=self.models,
        )

        # 개입 판정. 부를 곳(알림 층)이 아직 없어 자리만 둔다.
        self.policy = InterventionPolicy(clock, config, self.models)

        # 알림 전달. publish 를 주입해 엔진과 같은 Publisher 를 쓴다 —
        # Replay 에서 context 와 알림이 한 리스트에 시간순으로 쌓인다.
        self.notifier = Notifier(
            clock, config, world, scheduler,
            publish=self._publish_raw,
            wake_fsm=self.context.wake_fsm,
        )
        self.scenarios = ScenarioRunner(
            clock, config, self.policy, self.notifier, publish=self._publish_raw
        )

        self.received = 0
        self.dropped = 0
        self._periodic_armed = False

    # ------------------------------------------------------------ 입구

    def ingest(self, topic: str, payload: bytes | str, *, retained: bool = False) -> None:
        """수신 메시지 한 건. Sink 프로토콜의 유일한 메서드."""
        recv_ts = self._clock.now()
        msg = parse(topic, payload, recv_ts)

        if msg is None:
            self.dropped += 1
            return

        self.received += 1
        self._apply(msg, retained=retained)

    def _apply(self, msg: Message, *, retained: bool = False) -> None:
        self._world.apply(msg, retained=retained)
        self.context.note_event(msg)

        # 프로파일 전환은 설정 전체에 걸린다 (쿨다운·타임아웃·관측 창 배수)
        from . import messages as m

        if isinstance(msg, m.SystemProfile):
            self._config.set_profile(msg.profile)
            log.info("프로파일 전환: %s", msg.profile)

        if isinstance(msg, m.RegistryDevices):
            # 명세: 역할 매핑을 즉시 교체하고 모든 context 를 재계산한다.
            # 변경 전 매핑으로 진행 중이던 판단은 무효다.
            self._config.apply_registry(msg.devices)
            self._reset_contexts()

        if isinstance(msg, m.ModelMessage):
            # 검증에 실패하면 교체하지 않는다 — 직전 모델로 계속 판단한다
            self.models.apply(msg)

        if isinstance(msg, m.NotifyAck):
            self.notifier.on_ack(msg.notify_id, msg.ack_type, msg.src_id)

        self._publish_all(self.context.recompute())
        self.scenarios.tick(self.context)
        self._arm_periodic()

    # ------------------------------------------------------------ 발행

    def _publish_all(self, contexts: tuple[Context, ...]) -> None:
        for ctx in contexts:
            self._publish(ctx)

    def _publish_raw(self, topic: str, payload: dict[str, Any], retain: bool) -> None:
        """Notifier 와 ScenarioRunner 가 쓰는 발행 통로.
           이미 Envelope 를 갖춘 페이로드를 그대로 내보낸다 — _publish 는 Context 객체를 받아 Envelope 를 얹는 쪽이다.
        """
        self._pub.publish(topic, payload, retain=retain)

    def _on_context_change(self, contexts: tuple[Context, ...]) -> None:
        """타이머가 바꾼 context.
           발행만 하고 끝내면 시간 경과로만 성립하는 시나리오 조건(기상 후 N분 물을 안 마심)이 영영 안 걸린다.
        """
        self._publish_all(contexts)
        runner = getattr(self, "scenarios", None)
        if runner is not None:
            runner.tick(self.context)

    def _publish(self, ctx: Context) -> None:
        """hestia/context/{name}, QoS 1, retained.

        seq 가 없다 — retained 라 최신값만 의미가 있다 (명세).
        """
        now = self._clock.now()
        payload = {
            "version": SCHEMA_VERSION,
            "sent_ts": now,
            "src_id": SRC_ID,
            **ctx.payload(now),
        }
        self._pub.publish(f"hestia/context/{ctx.name}", payload, retain=True)

    def publish_snapshot(self) -> None:
        """현재 context 전부를 발행한다. 주기 발행과 기동 직후에 쓴다."""
        for ctx in self._all_contexts():
            self._publish(ctx)

    def _all_contexts(self) -> tuple[Context, ...]:
        """wake 는 ContextEngine 이 아니라 FSM 이 들고 있다."""
        return (*self.context.all_contexts(), self.context.wake_fsm.state)

    # ------------------------------------------------------------ 주기 발행

    def _arm_periodic(self) -> None:
        """명세: 상태 변화 시 즉시 + 5분 주기(생존 확인).

        콜백이 자기를 재등록한다. Replay 에서는 파일이 끝나면 루프도 끝나므로
        무한 반복이 되지 않는다.
        """
        if self._periodic_armed:
            return
        self._periodic_armed = True
        self._sched.at(
            self._clock.now() + self._config.publish_interval(),
            self._on_periodic,
            key="engine-periodic",
        )

    def _on_periodic(self) -> None:
        self.publish_snapshot()
        self._periodic_armed = False
        self._arm_periodic()

    # ------------------------------------------------------------ 내부

    def _reset_contexts(self) -> None:
        """registry 수신 후 판단을 처음부터 다시 세운다."""
        self.context.presence = None
        self.context.away = None
        self.context.occupancy = None
        self.context.activity = None

    def summary(self) -> str:
        return f"수신 {self.received}건, 폐기 {self.dropped}건"

    def __repr__(self) -> str:
        return f"Engine(received={self.received}, dropped={self.dropped})"