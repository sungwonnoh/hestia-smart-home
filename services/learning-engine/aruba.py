"""
CASAS Aruba 원본(aruba.txt)에서 KDE 학습용 시각 표본을 추출한다.

    Meal_Preparation begin → meal_time
    Sleeping begin         → sleep_time (proxy)
    Sleeping end           → wake_time  (proxy)

sleep_time / wake_time 은 HESTIA Context Engine 이 생성한 t0 가 아니라
공개 데이터셋의 활동 라벨에서 만든 proxy 다. 실제 sleep t0 계약이
정해지면 그 입력으로 교체한다.

밤잠 / 낮잠 구분과 구간 병합은 sleep_sessions.py 가 한다 (출처 무관).
이 모듈은 CASAS 원본 파싱과 라벨 → SleepSession 변환까지만 맡는다.
다른 CASAS 데이터셋(Milan / Cairo / Tulum2)은 casas.py 가 같은 파서를 쓴다.

원본 형식 (공백/탭 구분):

    2010-11-04 00:03:50.209589 M003 ON Sleeping begin   ← 라벨 줄 (6칸)
    2010-11-04 00:03:57.399391 M003 OFF                 ← 센서 줄 (4칸)

라벨 문자열은 원본에서 직접 확인한 값이다 (2010-11-04 ~ 2011-06-11):

    Meal_Preparation  begin/end 1606
    Sleeping          begin/end  401
    Bed_to_Toilet     begin/end  157
"""

from __future__ import annotations

import argparse
import logging
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from samples import KdeSample
from sleep_sessions import (
    MERGE_GAP_MIN,
    NIGHT_ANCHOR_HOUR,
    ClassifiedSession,
    SleepSession,
    classify_sessions,
    clock_minutes,
    drop_implausible,
    merge_sessions,
    naps,
    night_samples,
    nights,
)


log = logging.getLogger(__name__)


RAW_PATH = Path("data/raw/casas/aruba/aruba.txt")

MEAL_LABEL = "Meal_Preparation"
SLEEP_LABEL = "Sleeping"
TOILET_LABEL = "Bed_to_Toilet"


@dataclass(frozen=True)
class ActivityEvent:
    ts: datetime        # Aruba 로컬 시각 (tz 없음)
    label: str
    kind: str           # begin / end
    line_no: int


@dataclass(frozen=True)
class Segment:
    """라벨 begin ~ end 한 쌍."""

    begin: ActivityEvent
    end: ActivityEvent

    @property
    def minutes(self) -> float:
        return (self.end.ts - self.begin.ts).total_seconds() / 60


def time_sample(
    distribution: str,
    date: str,
    ts: datetime,
    proxy: bool,
) -> KdeSample:
    """
    Aruba 시각 표본 → 공통 KdeSample

    date
      - meal_time : 식사 준비를 시작한 날
      - sleep_time: 그 밤이 시작된 날 (00:30 취침이면 전날)
      - wake_time : 깬 날

    Aruba 에는 HESTIA 개입이 없었으므로 prompted 는 항상 False 다.
    """

    return KdeSample(
        distribution=distribution,
        value=clock_minutes(ts),
        date=date,
        source="aruba",
        prompted=False,
        proxy=proxy,
    )


@dataclass(frozen=True)
class ArubaLog:
    events: list[ActivityEvent]
    last_line_no: int       # 파일 마지막 줄 번호. 기록 종료로 닫힌 라벨 판별용


@dataclass
class SleepExtraction:
    """sleep/wake 추출 결과와 중간 산출물. 검증·보고용."""

    segments: list[Segment] = field(default_factory=list)
    classified: list[ClassifiedSession] = field(default_factory=list)
    dropped_segments: int = 0
    implausible: list[SleepSession] = field(default_factory=list)   # 24시간 이상 — 기록 오류
    source: str = "aruba"

    @property
    def episodes(self) -> list[SleepSession]:
        """병합한 수면 전체 (밤잠 + 낮잠)"""

        return [c.session for c in self.classified]

    @property
    def main(self) -> list[SleepSession]:
        """밤잠"""

        return [c.session for c in nights(self.classified)]

    @property
    def naps(self) -> list[SleepSession]:
        return [c.session for c in naps(self.classified)]

    @property
    def sleep_time(self) -> list[KdeSample]:
        return night_samples(self.classified, self.source, proxy=True)["sleep_time"]

    @property
    def wake_time(self) -> list[KdeSample]:
        return night_samples(self.classified, self.source, proxy=True)["wake_time"]


# ==================================================================== 파싱


def parse_ts(date: str, time: str) -> datetime:
    """
    원본에는 소수점 초가 없는 줄(09:22:15)과 자릿수가 짧은 줄(.95895)이 섞여 있다.
    Python 3.9 의 fromisoformat 은 후자를 못 읽으므로 strptime 을 쓴다.
    """

    fmt = "%Y-%m-%d %H:%M:%S.%f" if "." in time else "%Y-%m-%d %H:%M:%S"

    return datetime.strptime(f"{date} {time}", fmt)


def parse_line(line: str, line_no: int = 0) -> ActivityEvent | None:
    """
    라벨 줄이면 ActivityEvent, 센서 줄·빈 줄이면 None.
    라벨 줄 형식이 깨졌으면 ValueError.
    """

    parts = line.split()

    if len(parts) <= 4:
        return None

    if len(parts) != 6:
        raise ValueError(
            f"라벨 줄은 6칸이어야 합니다: {len(parts)}칸"
        )

    kind = parts[5]

    if kind not in ("begin", "end"):
        raise ValueError(
            f"begin/end 가 아닙니다: {kind!r}"
        )

    try:
        ts = parse_ts(parts[0], parts[1])
    except ValueError as exc:
        raise ValueError(
            f"시각 형식 오류: {parts[0]} {parts[1]}"
        ) from exc

    return ActivityEvent(ts, parts[4], kind, line_no)


def read_events(
    path: Path = RAW_PATH,
    labels: set[str] | None = None,
) -> ArubaLog:
    """
    원본을 한 줄씩 읽어 라벨 이벤트만 파일 순서대로 돌려준다.
    깨진 라벨 줄은 경고 후 건너뛴다.
    """

    events = []
    line_no = 0

    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        for line_no, line in enumerate(f, start=1):
            try:
                event = parse_line(line, line_no)
            except ValueError as exc:
                log.warning(
                    "Aruba %s:%d 건너뜀 — %s",
                    path,
                    line_no,
                    exc,
                )
                continue

            if event is None:
                continue

            if labels is None or event.label in labels:
                events.append(event)

    return ArubaLog(events, line_no)


def count_labels(events: list[ActivityEvent]) -> Counter:
    return Counter(
        (e.label, e.kind) for e in events
    )


def pair_segments(
    events: list[ActivityEvent],
    label: str,
) -> tuple[list[Segment], int]:
    """
    같은 라벨의 begin/end 를 파일 순서대로 짝짓는다.

    버리는 경우 (개수를 함께 돌려준다):
      - 짝 없는 begin / end
      - end 가 begin 보다 이른 구간
        원본 2011-05-23 처럼 날짜가 넘어가지 않고 찍힌 구간이 있다.
        실제 날짜를 원본만으로 확정할 수 없으므로 보정하지 않는다.
    """

    segments = []
    dropped = 0
    begin = None

    for event in events:
        if event.label != label:
            continue

        if event.kind == "begin":
            if begin is not None:
                dropped += 1
                log.warning("짝 없는 %s begin: line %d", label, begin.line_no)
            begin = event
            continue

        if begin is None:
            dropped += 1
            log.warning("짝 없는 %s end: line %d", label, event.line_no)
            continue

        if event.ts < begin.ts:
            dropped += 1
            log.warning(
                "%s 구간 시각 역전: line %d~%d (%s → %s)",
                label,
                begin.line_no,
                event.line_no,
                begin.ts,
                event.ts,
            )
        else:
            segments.append(Segment(begin, event))

        begin = None

    if begin is not None:
        dropped += 1
        log.warning("짝 없는 %s begin: line %d", label, begin.line_no)

    return segments, dropped


# ==================================================================== 수면


def segments_to_sessions(
    segments: list[Segment],
    last_line_no: int | None = None,
) -> list[SleepSession]:
    """
    라벨 구간 → SleepSession.

    end 라벨이 파일 마지막 줄에 붙어 있으면 실제 기상이 아니라
    기록 종료로 닫힌 것이다 (Aruba 원본 2011-06-11 23:58).
    취침 시각은 유효하지만 기상 시각은 쓸 수 없다.
    """

    return [
        SleepSession(
            start=seg.begin.ts,
            end=seg.end.ts,
            wake_observed=seg.end.line_no != last_line_no,
        )
        for seg in segments
    ]


def label_times(
    events: list[ActivityEvent],
    label: str | None,
) -> list[datetime]:
    """라벨 begin 시각 목록. 병합 bridge(예: Bed_to_Toilet)에 쓴다."""

    if label is None:
        return []

    return [e.ts for e in events if e.label == label and e.kind == "begin"]


def extract_session_label(
    aruba_log: ArubaLog,
    sleep_label: str,
    toilet_label: str | None,
    source: str,
    merge_gap_min: float = MERGE_GAP_MIN,
    anchor_hour: int = NIGHT_ANCHOR_HOUR,
) -> SleepExtraction:
    """
    수면을 begin~end 구간 라벨로 기록한 CASAS 데이터셋 (Aruba, Milan, Tulum2).

    Aruba 는 밤중에 화장실을 다녀오면 Sleeping 이 끊겼다가 다시 시작된다.
    끊긴 begin 을 취침 시각으로 쓰면 sleep_time 분포가 새벽 쪽으로 오염되므로
    toilet_label 이 사이에 있거나 간격이 짧으면 합친다.
    """

    segments, dropped = pair_segments(aruba_log.events, sleep_label)

    sessions, implausible = drop_implausible(
        merge_sessions(
            segments_to_sessions(segments, aruba_log.last_line_no),
            merge_gap_min,
            bridges=label_times(aruba_log.events, toilet_label),
        )
    )

    return SleepExtraction(
        segments=segments,
        classified=classify_sessions(sessions, anchor_hour),
        dropped_segments=dropped,
        implausible=implausible,
        source=source,
    )


def extract_sleep(
    aruba_log: ArubaLog,
    merge_gap_min: float = MERGE_GAP_MIN,
    anchor_hour: int = NIGHT_ANCHOR_HOUR,
) -> SleepExtraction:
    return extract_session_label(
        aruba_log,
        SLEEP_LABEL,
        TOILET_LABEL,
        "aruba",
        merge_gap_min,
        anchor_hour,
    )


# ==================================================================== 식사


def extract_meal(aruba_log: ArubaLog) -> list[KdeSample]:
    """
    Meal_Preparation begin 전부.
    scripts/datasets/parse_aruba.py 의 meal_preparation.csv 와 같은 집합이다.
    아침 대표값 필터(breakfast)는 여기서 하지 않는다.
    """

    return [
        time_sample("meal_time", e.ts.date().isoformat(), e.ts, proxy=False)
        for e in aruba_log.events
        if e.label == MEAL_LABEL and e.kind == "begin"
    ]


def extract_samples(
    path: Path = RAW_PATH,
    merge_gap_min: float = MERGE_GAP_MIN,
    anchor_hour: int = NIGHT_ANCHOR_HOUR,
) -> dict[str, list[KdeSample]]:
    """
    Aruba 원본 하나 → meal_time / sleep_time / wake_time 표본
    """

    aruba_log = read_events(
        path,
        {MEAL_LABEL, SLEEP_LABEL, TOILET_LABEL},
    )

    sleep = extract_sleep(aruba_log, merge_gap_min, anchor_hour)

    return {
        "meal_time": extract_meal(aruba_log),
        "sleep_time": sleep.sleep_time,
        "wake_time": sleep.wake_time,
    }


# ==================================================================== 보고


def _summary(samples: list[KdeSample]) -> str:
    """
    표본 수와 시간대별 개수.
    sleep_time 은 자정을 넘나들어 선형 min/median 이 의미 없으므로 시간대로 보인다.
    """

    hours = Counter(int(s.value // 60) for s in samples)

    return f"{len(samples):>5}  " + " ".join(
        f"{h:02d}h:{n}" for h, n in sorted(hours.items())
    )


def report(path: Path = RAW_PATH) -> None:
    aruba_log = read_events(path)

    print(f"\n===== Aruba labels ({path}) =====")
    for (label, kind), n in sorted(count_labels(aruba_log.events).items()):
        print(f"  {label:<18} {kind:<5} {n:>5}")

    sleep = extract_sleep(aruba_log)
    meal = extract_meal(aruba_log)

    print("\n===== Sleeping =====")
    print(f"  segments          : {len(sleep.segments)} (dropped {sleep.dropped_segments})")
    print(f"  merged episodes   : {len(sleep.episodes)}")
    print(f"  nights (main)     : {len(sleep.main)}")
    print(f"  naps              : {len(sleep.naps)}")
    print(f"  implausible (>=24h): {len(sleep.implausible)}")

    durations = sorted(e.minutes for e in sleep.main)
    short = [d for d in durations if d < 180]
    print(f"  main < 3h         : {len(short)}")
    print(f"  main median       : {durations[len(durations) // 2] / 60:.1f}h")
    print(f"  crosses midnight  : {sum(e.start.date() != e.end.date() for e in sleep.main)}")
    print(f"  truncated (no wake): {sum(not e.wake_observed for e in sleep.main)}")

    print("\n===== Samples =====")
    print(f"  meal_time         : {_summary(meal)}")
    print(f"  sleep_time (proxy): {_summary(sleep.sleep_time)}")
    print(f"  wake_time  (proxy): {_summary(sleep.wake_time)}")
    print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "path",
        nargs="?",
        type=Path,
        default=RAW_PATH,
        help="aruba.txt 경로",
    )

    logging.basicConfig(level=logging.WARNING)

    report(parser.parse_args().path)
