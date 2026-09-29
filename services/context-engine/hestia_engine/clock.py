""" Clock은 시각 공급자

엔진 코드는 time.time() / datetime.now() 를 직접 호출하지 않음 (주입받은 Clock에게만 호출)

HESTIA 의 시각 기준은 RPi5 의 수신 시각(recv_ts)-> 노드가 보낸 sent_ts 는 순서 판별과 지연 측정에만 쓰며, 시계와는 무관
날짜·타임존 변환은 timeutil.py 가 담당
"""

from __future__ import annotations #annotations-> 타입 힌트 평가 지연

import time # RealClock이 현재 시각을 읽기 위해, time.time() 호출
from typing import Protocol, runtime_checkable


@runtime_checkable
class Clock(Protocol):
    """Unix timestamp(초, float)를 돌려주는 최소 인터페이스"""
    """Clock을 따르는 클래스들은 float 타입을 반환하는 now(self) 메서드를 구현해야 함"""

    def now(self) -> float: ...


class RealClock:
    """실제 시계, 운영 시 사용"""

    __slots__ = () #RealClock은 내부에 저장할 변수가 없으므로, 메모리 공간을 아예 할당 받지 않음 (메모리 최적화)

    def now(self) -> float:
        return time.time()

    def __repr__(self) -> str: #객체를 문자열로 표현할 때 사용되는 매직 메서드(Magic Method)
        return "RealClock()"


class ReplayClock:
    """가상 시계, JSONL 재생과 테스트에서 사용

    advance_to / advance_by 로만 시간이 흐름
    호출하지 않는 한 now() 는 영원히 같은 값을 돌려줌
    """

    __slots__ = ("_t",)

    def __init__(self, start: float = 0.0) -> None:
        self._t = float(start)

    def now(self) -> float:
        return self._t

    def advance_to(self, ts: float) -> None:
        """시계를 ts 로 옮김
        과거로 되돌리는 것은 금지

        [참고]
        recv_ts 는 RPi5 가 수신 순서대로 붙이므로 단조 증가가 보장
        조용히 통과시키면 지연·쿨다운 계산이 음수가 되어 한참 뒤에 통계 이상으로 나타남
        """
        ts = float(ts)
        if ts < self._t:
            raise ValueError(
                f"시계를 과거로 되돌릴 수 없음: now={self._t} -> {ts}. "
                "입력 로그가 recv_ts 오름차순인지 확인할 것."
            )
        self._t = ts

    def advance_by(self, seconds: float) -> None:
        """현재 시간 + seconds"""
        if seconds < 0:
            raise ValueError(f"음수 경과 불가: {seconds}")
        self._t += float(seconds)

    def __repr__(self) -> str:
        return f"ReplayClock(t={self._t})"