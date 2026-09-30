import json
from pathlib import Path

import pytest

from hestia_engine.clock import ReplayClock
from hestia_engine.replay import Replay, read_jsonl, replay_file
from hestia_engine.sink import EchoSink, Sink
from hestia_engine.timers import Scheduler

DATA = Path(__file__).parent / "data" / "morning.jsonl"


def run(path=DATA):
    return replay_file(path, echo=False)


# ============================================================ 기본 동작


def test_all_lines_ingested():
    result, sink = run()
    assert result.lines == 22
    assert sink.dropped == 0
    assert len(sink.received) == 22


def test_comments_and_blanks_skipped():
    lines = list(read_jsonl(DATA))
    assert len(lines) == 22


def test_clock_follows_recv_ts():
    """마지막 줄 처리 시점의 시계가 그 줄의 ts 여야 한다."""
    _, sink = run()
    assert sink.received[-1].recv_ts == 1790297100.0


def test_recv_ts_differs_from_sent_ts():
    """네트워크 지연이 보존된다. sent_ts 는 판단에 쓰지 않는다."""
    _, sink = run()
    msg = sink.received[-1]
    assert msg.recv_ts - msg.sent_ts == 2.0


def test_span_covers_seven_hours():
    result, _ = run()
    assert result.span_sec == 1790297100 - 1790271000


def test_order_preserved():
    _, sink = run()
    ts = [m.recv_ts for m in sink.received]
    assert ts == sorted(ts)


# ============================================================ 결정성


def test_same_input_same_output():
    """0단계 게이트 — 같은 JSONL 을 두 번 재생하면 출력이 완전히 동일하다."""
    _, a = run()
    _, b = run()
    assert a.received == b.received


def test_no_wall_clock_dependency():
    """벽시계와 무관 — 7시간치가 즉시 끝난다."""
    import time

    t0 = time.monotonic()
    run()
    assert time.monotonic() - t0 < 1.0


# ============================================================ 타이머 연동


def test_timer_fires_at_scheduled_virtual_time(tmp_path):
    """이벤트 사이의 빈 구간에 걸린 타이머가 정확한 가상 시각에 발화한다."""
    path = tmp_path / "gap.jsonl"
    path.write_text(
        "\n".join(
            json.dumps({"ts": ts, "topic": "hestia/system/profile",
                        "payload": {"version": 1, "sent_ts": ts, "src_id": "rpi4",
                                    "profile": "REAL"}})
            for ts in (1000, 1000 + 3 * 3600)      # 3시간 간격
        ),
        encoding="utf-8",
    )

    clock = ReplayClock(0)
    sched = Scheduler(clock)
    sink = EchoSink(clock, echo=False)
    fired: list[float] = []

    class Hooked:
        def ingest(self, topic, payload):
            sink.ingest(topic, payload)
            if len(sink.received) == 1:
                sched.after(3600, lambda: fired.append(clock.now()))   # 1시간 뒤

    result = Replay(clock, sched, Hooked()).run(path)

    assert result.timers_fired == 1
    assert fired == [1000 + 3600]                  # 두 번째 줄(3시간 뒤)이 아니라 1시간 뒤
    assert sink.received[-1].recv_ts == 1000 + 3 * 3600


def test_timer_after_last_line_does_not_fire(tmp_path):
    """파일이 끝나면 종료한다. 남은 타이머는 버린다."""
    path = tmp_path / "one.jsonl"
    path.write_text(
        json.dumps({"ts": 1000, "topic": "hestia/system/profile",
                    "payload": {"version": 1, "sent_ts": 1000, "src_id": "rpi4",
                                "profile": "DEMO"}}),
        encoding="utf-8",
    )

    clock = ReplayClock(0)
    sched = Scheduler(clock)
    fired: list[float] = []

    class Hooked:
        def ingest(self, topic, payload):
            sched.after(3600, lambda: fired.append(clock.now()))

    result = Replay(clock, sched, Hooked()).run(path)
    assert result.timers_fired == 0
    assert fired == []
    assert sched.pending() == 1                    # 남아 있지만 발화하지 않음


# ============================================================ 입력 방어


def test_broken_line_skipped_not_fatal(tmp_path):
    path = tmp_path / "mixed.jsonl"
    good = json.dumps({"ts": 1000, "topic": "hestia/system/profile",
                       "payload": {"version": 1, "sent_ts": 1000,
                                   "src_id": "rpi4", "profile": "REAL"}})
    path.write_text(f"{good}\n{{broken\n{good}\n", encoding="utf-8")

    result, _ = run(path)
    assert result.lines == 2                       # 깨진 줄만 건너뜀


def test_missing_key_skipped(tmp_path):
    path = tmp_path / "nokey.jsonl"
    path.write_text('{"topic": "hestia/system/profile", "payload": {}}\n', encoding="utf-8")
    result, _ = run(path)
    assert result.lines == 0


def test_backward_ts_is_fatal(tmp_path):
    """입력이 정렬돼 있지 않다는 뜻. 조용히 넘어가면 지연 계산이 음수가 된다."""
    path = tmp_path / "unsorted.jsonl"
    rows = [
        {"ts": 2000, "topic": "hestia/system/profile",
         "payload": {"version": 1, "sent_ts": 2000, "src_id": "rpi4", "profile": "REAL"}},
        {"ts": 1000, "topic": "hestia/system/profile",
         "payload": {"version": 1, "sent_ts": 1000, "src_id": "rpi4", "profile": "DEMO"}},
    ]
    path.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")

    with pytest.raises(ValueError, match="unsorted.jsonl:2"):
        run(path)


def test_empty_file(tmp_path):
    path = tmp_path / "empty.jsonl"
    path.write_text("# 주석만\n\n", encoding="utf-8")
    result, sink = run(path)
    assert result.lines == 0
    assert result.span_sec == 0.0


# ============================================================ 계약


def test_echo_sink_satisfies_protocol():
    assert isinstance(EchoSink(ReplayClock()), Sink)


def test_payload_arrives_as_string():
    """dict 가 아니라 직렬화된 JSON 으로 넘어가야 실제 MQTT 와 같은 조건이 된다."""
    seen: list[type] = []

    class Spy:
        def ingest(self, topic, payload):
            seen.append(type(payload))

    clock = ReplayClock(0)
    Replay(clock, Scheduler(clock), Spy()).run(DATA)
    assert set(seen) == {str}