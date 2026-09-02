"""시간 소스 추상화.

이 모듈 밖에서는 time.time()을 직접 호출하지 않는다.
시각이 필요한 모든 코드는 Clock 인스턴스를 주입받는다.
"""

import time
from typing import Protocol


class Clock(Protocol):
    def now(self) -> float:
        """현재 시각을 unix timestamp(초)로 반환."""
        ...


class RealClock:
    """운영 환경용. 실제 시스템 시각을 반환한다."""

    def now(self) -> float:
        return time.time()


class ReplayClock:
    """리플레이용. 외부에서 시각을 주입한다.

    로그를 재생할 때 각 이벤트의 발생 시각으로 advance()를 호출하면,
    엔진 입장에서는 그 시점에 실제로 살고 있는 것과 구분되지 않는다.
    """

    def __init__(self, start: float = 0.0):
        self._now = start

    def now(self) -> float:
        return self._now

    def advance(self, timestamp: float) -> None:
        """시각을 지정한 값으로 이동. 과거로는 되돌리지 않는다."""
        if timestamp < self._now:
            raise ValueError(
                f"시간을 되돌릴 수 없습니다: {self._now} -> {timestamp}"
            )
        self._now = timestamp

    def tick(self, seconds: float) -> None:
        """상대적으로 시간을 흘린다. 타임아웃 테스트용."""
        self._now += seconds