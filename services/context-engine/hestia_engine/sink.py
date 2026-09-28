"""엔진 자리에 들어가는 부품(엔진 자리 껍데기)

2단계에서 진짜 엔진(engine.py)이 이 자리를 차지함
그전까지는 EchoSink 가 실제 엔진은 대신하여, 판단 로직 없이도 재생기와 파싱 계층이 도는지 확인할 수 있게 하기 위한 용도

데이터 재생기(replay.py)나 실행기(runner.py)가 메시지 수신과 파싱 레이어(messages.py)까지 문제없이 잘 동작하는지 시뮬레이션 및 테스트하기 위한 "가짜 엔진(스텁/더미)" 모듈
replay.py 와 runner.py 는 실제 엔진(engine.py)인지/ 가짜(sink.py)인지 모름
"""

from __future__ import annotations

import logging
from typing import Protocol, runtime_checkable

from .clock import Clock
from .messages import Message, parse
from .timeutil import clock_str

log = logging.getLogger(__name__)       #현재 모듈(sink.py) 전용 로거 객체 생성


@runtime_checkable
class Sink(Protocol):
    """실제 엔진이든 가짜 엔진(EchoSink)이든 메시지를 수신하는 부품이라면 반드시 갖춰야 하는 최상위 인터페이스 규약을 선언
    recv_ts 를 인자로 받지 않음-> 구현체가 주입받은 Clock 에게 물음
    """

    def ingest(self, topic: str, payload: bytes | str) -> None: ...     #메시지 한 건을 밀어 넣는(Ingest) 추상 메서드


class EchoSink:
    """Sink 프로토콜을 구현한 가짜 엔진(더미/스텁) 클래스
    받은 메시지를 파싱해 기록하고 찍음-> 파싱까지 하므로 messages.py 가 실제 입력에서 도는지도 함께 확인
    폐기된 메시지는 기록하지 않되 건수는 셈(dropped)
    """

    def __init__(self, clock: Clock, *, echo: bool = True) -> None:
        self._clock = clock     #시각을 조회할 Clock 객체를 주입받음
        self._echo = echo       #콘솔 출력 여부
        self.received: list[Message] = []       #정상적으로 파싱 완료된 Message 객체들을 순서대로 담아둘 리스트
        self.dropped = 0

    def ingest(self, topic: str, payload: bytes | str) -> None:     #외부에서 메시지 패킷이 들어올 때 불리는 핵심 메서드
        recv_ts = self._clock.now()
        msg = parse(topic, payload, recv_ts)        #원시 데이터를 Message 객체로 파싱

        if msg is None:
            self.dropped += 1
            return

        self.received.append(msg)       #정상적으로 파싱된 Message 객체를 received 리스트에 저장
        if self._echo:                  #콘솔 출력 옵션이 True 라면
            print(f"{clock_str(recv_ts)}  {topic}  {type(msg).__name__}")       #화면에 [HH:MM:SS]  [토픽]  [메시지_클래스명] 형태로 수신 현황 출력

    def summary(self) -> str:
        return f"수신 {len(self.received)}건, 폐기 {self.dropped}건"