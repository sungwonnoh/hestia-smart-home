"""시계에 종속된 타이머 큐

엔진의 시간 기반 동작은 전부 타이머를 거침
쿨다운 만료, ack_deadline 에스컬레이션, comply 관측 창 종료,
무활동 N분 판정, context 주기 발행

스스로 돌지 않음-> 누군가 run_due() 를 불러줘야 동작
"""

from __future__ import annotations

import heapq #우선순위 큐(Min-Heap) 데이터 구조를 제공
import itertools #반복자(Iterator) 생성 도구-> itertools.count()를 사용하여 0, 1, 2, ... 형태의 증가하는 고유 번호(카운터)를 생성
from dataclasses import dataclass, field
from typing import Any, Callable

from .clock import Clock, ReplayClock

Callback = Callable[[], Any]
"""인자(매개변수)를 받지 않고, 아무 값이나 반환하는 함수 타입에 Callback이라는 별칭(Type Alias)을 붙임"""


@dataclass(order=True)
class _Entry:
    """타이머 큐(heapq) 내부에서 개별 타이머의 상태 & 정렬 우선순위를 관리하는 데이터 클래스"""
    due: float      #타이머가 발화해야 하는 목표 시각
    order: int      # 동시각 항목의 등록 순서 보존(due가 똑같을 때)
    key: str = field(compare=False)                             #타이머를 식별하거나 취소(cancel)할 때 사용하는 고유 키 이름
    callback: Callback = field(compare=False)                   #시간이 되었을 때 실행할 함수 객체
    cancelled: bool = field(compare=False, default=False)       #타이머 취소 여부를 나타내는 플래그


class Scheduler:
    def __init__(self, clock: Clock) -> None:       #Clock 객체(RealClock 또는 ReplayClock)을 인자로 하여 Scheduler 객체 생성
        self._clock = clock
        self._heap: list[_Entry] = []
        self._by_key: dict[str, _Entry] = {}
        self._counter = itertools.count()           #_Entry.order 또는 사용자가 key를 지정하지 않은 경우 익명 키 생성에 사용

    # -------------------------------------------------------------- 등록

    def at(self, due: float, callback: Callback, *, key: str | None = None) -> str:
        """due 시각에 callback(실행할 함수) 을 발화
        같은 key 로 다시 등록하면 이전 것을 취소하고 덮어씀
        """
        key = key or f"anon-{next(self._counter)}"      #사용자가 key를 지정하지 않은 경우 'anon-1' 등 고유 번호 생성
        self.cancel(key)        #이미 해당 키가 존재한다면, 취소하고 다시 등록
        entry = _Entry(
            due=float(due),
            order=next(self._counter),
            key=key,
            callback=callback,
        )
        self._by_key[key] = entry
        heapq.heappush(self._heap, entry)       #리스트 끝에 새 항목을 넣은 뒤, 부모 노드와 비교하면서 위로 끌어올리는 작업($O(log N))을 수행
        return key

    def after(self, seconds: float, callback: Callback, *, key: str | None = None) -> str:
        return self.at(self._clock.now() + float(seconds), callback, key=key)

    def cancel(self, key: str) -> bool:
        """힙에서 빼지 않고 플래그만 세움(지연 삭제)
        힙 중간 삭제는 비용이 큼: O(n) -> cacelled 플래그를 true로 해서, 힙에 그대로 두고 꺼낼 때 그냥 건너뛰기
        """
        entry = self._by_key.pop(key, None)
        if entry is None:
            return False
        entry.cancelled = True
        return True

    # -------------------------------------------------------------- 조회

    def next_due(self) -> float | None:
        """다음 발화 예정 시각, 없으면 None
        Replay 루프가 '다음 이벤트 전에 처리할 타이머가 있는가'를 판단하는 데 사용
        """
        self._drop_cancelled()
        return self._heap[0].due if self._heap else None

    def pending(self) -> int:
        self._drop_cancelled()
        return len(self._heap)          #살아있는 타이머 수

    def keys(self) -> tuple[str, ...]:
        return tuple(self._by_key)      #현재 스케줄러에 등록되어 대기 중인 모든 타이머의 고유 키(Key) 이름들을 튜플(Tuple) 형태로 반환

    # -------------------------------------------------------------- 실행

    def run_due(self, until: float | None = None) -> int:
        """until(기본값(None): 현재 시각) 이하의 타이머를 이른 순서대로 발화시킴=> 지금 시각(또는 지정한 시각)까지 실행되기로 약속되었던 타이머들을 시각이 빠른 순서대로 하나씩 실행

        콜백이 새 타이머를 걸면 같은 루프(While)에서 처리됨 (에스컬레이션 1단계 -> 2단계)

        ReplayClock 이면 발화 직전에 시계를 예정 시각으로 옮김=> 콜백이 보는 now() 가 '실행된 시각'이 아니라 '실행되기로 한 시각'이어야 Replay 와 실시간이 같은 답을 낼 수 있음
        """
        limit = self._clock.now() if until is None else float(until)        #현재 시각 또는 지정한 시각
        fired = 0       #이번 run_due() 호출 동안 "실제로 실행(발화)된 타이머 콜백의 총 개수
        while True:
            self._drop_cancelled()     #취소된 타이머는 건너뛰기
            if not self._heap or self._heap[0].due > limit:     #당장 실행될 타이머 없음
                return fired
            entry = heapq.heappop(self._heap)
            self._by_key.pop(entry.key, None)
            if isinstance(self._clock, ReplayClock) and entry.due > self._clock.now():
                """ReplayClock이면 시계를 타이머 예정 시각으로 점프"""
                self._clock.advance_to(entry.due)
            entry.callback()
            fired += 1

    # -------------------------------------------------------------- 내부

    def _drop_cancelled(self) -> None:
        while self._heap and self._heap[0].cancelled:
            heapq.heappop(self._heap)