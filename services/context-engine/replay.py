"""리플레이 하네스.

MQTT도 하드웨어도 없이 엔진을 돌린다.
JSONL 로그 한 줄이 이벤트 하나: {"ts": 1735689600.0, "topic": "...", "payload": {...}}

사용:
    python replay.py logs/day01.jsonl
"""

import json
import sys

from clock import ReplayClock
from engine import ContextEngine


def load_events(path: str) -> list[dict]:
    events = []
    with open(path) as f:
        for line_no, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError as e:
                print(f"{path}:{line_no} 파싱 실패: {e}", file=sys.stderr)
    return sorted(events, key=lambda e: e["ts"])


def replay(path: str) -> None:
    events = load_events(path)
    if not events:
        print("이벤트 없음", file=sys.stderr)
        return

    clock = ReplayClock(start=events[0]["ts"])
    engine = ContextEngine(clock=clock)

    for ev in events:
        clock.advance(ev["ts"])
        engine.handle_event(ev["topic"], ev["payload"])


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: python replay.py <logfile.jsonl>", file=sys.stderr)
        sys.exit(1)
    replay(sys.argv[1])