"""JSONL 재생기

*과거에 저장된 JSONL 로그 파일의 메시지들을 읽어와 실제 시간이 흘러간 것처럼 가상 시계(ReplayClock)와 타이머 스케줄러(Scheduler), 수신부(Sink)에 순서대로 밀어넣어 검증하는 오프라인 재생기(Replay Engine)
*메시지 수신 시각 사이사이에 존재했던 타이머 이벤트들까지 완벽하게 시뮬레이션

입력 형식 (한 줄에 JSON 하나):

    {"ts": 1790296800, "topic": "hestia/sensor/vs-01/state",
     "payload": {"version": 1, "sent_ts": 1790296798, ...}}

바깥의 ts 는 RPi5 의 수신 시각(recv_ts)-> 페이로드에 없는 값이므로 기록하는 쪽이 붙임

실행:
    python -m hestia_engine.replay logs/morning.jsonl
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from .clock import ReplayClock
from .sink import Sink
from .timers import Scheduler

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Line:               #JSONL 파일의 한 줄을 읽어 보관하는 불변(frozen) 객체
    ts: float
    topic: str
    payload: str          # 직렬화된 상태로 넘김 — 실제 MQTT 와 같은 조건

    @property
    def lineno(self) -> int:  # 진단용
        return 0


def read_jsonl(path: Path) -> Iterator[tuple[int, Line]]:
    """JSONL 로그 파일을 한 줄씩 읽어서 (줄번호, Line 객체) 형태로 변환해 내보내는 파이썬 제너레이터 함수

    빈 줄과 '#' 주석은 건너뜀, 깨진 줄은 경고 후 건너뛰되 전체를 멈추지 않음
    ts 역전은 ReplayClock 이 잡음
    """
    #파일 경로 객체(pathlib.Path)를 인자로 받음
    with path.open(encoding="utf-8") as fp:
        for lineno, raw in enumerate(fp, start=1):      #파일의 각 줄(raw)을 순회하면서 1번부터 시작하는 줄 번호(lineno)를 함께 추적
            raw = raw.strip()
            if not raw or raw.startswith("#"):
                continue
            try:
                obj = json.loads(raw)                   #텍스트 줄을 파이썬 딕셔너리로 변환
                yield lineno, Line(                     #성공적으로 파싱되면 Line 객체를 생성하여 줄 번호(lineno)와 함께 외부로 전달
                    ts=float(obj["ts"]),
                    topic=str(obj["topic"]),
                    payload=json.dumps(obj["payload"], ensure_ascii=False),
                )
            except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
                #에러 종류: JSON 형식이 아닐 때 / ts, topic, payload 키가 없을 때 / ts를 float로 변환하지 못할 때
                log.warning("%s:%d 건너뜀 — %s", path.name, lineno, exc)


@dataclass
class ReplayResult:
    lines: int = 0
    timers_fired: int = 0
    first_ts: float | None = None
    last_ts: float | None = None

    @property
    def span_sec(self) -> float:
        if self.first_ts is None or self.last_ts is None:
            return 0.0
        return self.last_ts - self.first_ts


class Replay:
    """JSONL → (타이머 처리) → 시계 이동 → sink.ingest"""

    def __init__(self, clock: ReplayClock, scheduler: Scheduler, sink: Sink) -> None:
        self._clock = clock
        self._sched = scheduler
        self._sink = sink

    def run(self, path: Path) -> ReplayResult:
        result = ReplayResult()

        for lineno, line in read_jsonl(path):
            # [준비] 첫 번째 메시지를 만났을 때 가상 시계의 초기 위치 설정
            if result.first_ts is None:
                result.first_ts = line.ts
                self._clock.advance_to(line.ts)

            # 1단계: 메시지 시각(line.ts)보다 이전에 만료 예정인 타이머들 '먼저' 실행
            result.timers_fired += self._fire_until(line.ts)

            # 2단계: 가상 시계를 '현재 메시지 수신 시각'으로 완전히 이동
            try:
                self._clock.advance_to(line.ts)
            except ValueError as exc:
                # 시계가 과거로 돌아가려 하면 로그 줄 번호와 함께 에러 발생
                raise ValueError(f"{path.name}:{lineno} {exc}") from exc

            # 3단계: 메시지를 엔진(Sink)에 주입
            self._sink.ingest(line.topic, line.payload)
            result.lines += 1
            result.last_ts = line.ts

        return result

    def _fire_until(self, ts: float) -> int:
        """이 줄에 닿기 전에 만료되는 타이머를 먼저 발화시킴
        매 바퀴 next_due 를 다시 보는 이유: 콜백이 새 타이머를 걸 수 있기 때문
        """
        fired = 0
        while True:
            due = self._sched.next_due()
            if due is None or due > ts:
                return fired
            fired += self._sched.run_due(until=due)


def replay_file(path: Path, *, start: float | None = None, echo: bool = True):
    """한 줄짜리 조립. 테스트와 CLI 가 공유"""
    from .sink import EchoSink

    clock = ReplayClock(start if start is not None else 0.0)
    sched = Scheduler(clock)
    sink = EchoSink(clock, echo=echo)
    result = Replay(clock, sched, sink).run(path)
    return result, sink


def main(argv: list[str] | None = None) -> int:
    #CLI에서 이 모듈을 직접 실행했을 때 사용자의 명령줄 인자를 받아 처리하고 로그 재생을 실행한 뒤 최종 결과를 리포트
    import argparse

    parser = argparse.ArgumentParser(description="HESTIA JSONL 재생기")
    parser.add_argument("path", type=Path, help="입력 JSONL")
    parser.add_argument("-q", "--quiet", action="store_true", help="메시지별 출력 생략")
    args = parser.parse_args(argv)      #입력받은 인자들을 분석하여 args 객체에 저장

    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")

    if not args.path.exists():
        parser.error(f"파일 없음: {args.path}")

    result, sink = replay_file(args.path, echo=not args.quiet)

    print(
        f"\n{result.lines}줄 재생, 가상 경과 {result.span_sec / 3600:.1f}시간, "
        f"타이머 {result.timers_fired}건 — {sink.summary()}"
        #출력 예시: 3500줄 재생, 가상 경과 8.5시간, 타이머 12건 — 수신 3498건, 폐기 2건
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())