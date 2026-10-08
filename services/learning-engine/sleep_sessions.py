"""
수면 기록 → 밤잠 / 낮잠 구분 → sleep_time / wake_time 학습 표본.

이 모듈은 "학습 데이터 선별"만 한다 (RPi4 배치).
지금 이 수면이 밤잠인지 실시간으로 판정하는 일은 RPi5 Context Engine 의 책임이다.

    수면 기록 (CASAS 라벨 / Context Engine t0 의 sleep_start · sleep_end)
        ↓ merge_sessions      화장실 등으로 끊긴 구간을 한 번의 수면으로
        ↓ classify_sessions   밤마다 가장 긴 수면 = 밤잠(night), 나머지 = 낮잠(nap)
        ↓ night_samples       밤잠만 sleep_time / wake_time 표본으로

임의의 취침·기상 시각을 기준으로 쓰지 않는다. "가장 긴 수면"만 본다.

Context Engine 은 수면 시작과 끝을 t0 로그에 따로 남긴다 (meal / wake / hydration 과 같은 형식).

    {"type": "sleep_start", "t0": epoch, "area": "bedroom", "date": ..., "source": ..., "prompted": ..., "duration_sec": ...}
    {"type": "sleep_end",   "t0": epoch, "date": ..., "source": ..., "prompted": ..., "duration_sec": ...}
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from typing import Iterable, Sequence

from samples import KdeSample


log = logging.getLogger(__name__)


NIGHT = "night"
NAP = "nap"

# 수면 구간 사이 간격이 이 이하이면 한 번의 수면으로 합친다.
# Aruba 구간 간격 185건 중 162건이 10분 미만이고 15분 이후는 드문드문하다.
# 팀 검토 전 잠정값이므로 인자로 바꿀 수 있게 둔다.
MERGE_GAP_MIN = 15.0

# 하룻밤의 경계. 이 시각부터 다음 날 이 시각 전까지를 같은 밤으로 본다.
# Aruba 수면 시작 시각은 08~13시에 거의 없다 (401건 중 2건).
NIGHT_ANCHOR_HOUR = 12

# 한 번의 수면은 하룻밤 범위(24시간)를 넘을 수 없다. 넘으면 기록 오류로 본다.
# 예: Tulum2 는 센서 기록이 거의 끊긴 날(하루 30여 줄)에 수면 라벨이 이어져 32~34시간이 된다.
MAX_SESSION_HOURS = 24

# Context Engine(timeutil.KST)과 같은 기준. epoch 입력을 로컬 시각으로 바꿀 때 쓴다.
KST = timezone(timedelta(hours=9))


@dataclass(frozen=True)
class SleepSession:
    """
    한 번의 수면. 시각은 로컬(tz 없음) 기준이다.

    wake_observed
        False 면 end 가 실제 기상이 아니다 (예: 기록 종료로 닫힘, 기상 기록 없음).
        취침 시각은 쓰되 기상 시각은 학습에 쓰지 않는다.
    """

    start: datetime
    end: datetime
    wake_observed: bool = True
    parts: int = 1
    prompted: bool = False
    area: str | None = None     # sleep_start 의 area. 저장만 하고 판단에는 쓰지 않는다
    source: str = ""

    @property
    def minutes(self) -> float:
        return (self.end - self.start).total_seconds() / 60

    def night(self, anchor_hour: int = NIGHT_ANCHOR_HOUR) -> str:
        """
        이 수면이 속한 밤의 날짜.
        00:30 에 잠들었으면 전날 밤이다.
        """

        return (
            self.start - timedelta(hours=anchor_hour)
        ).date().isoformat()


@dataclass(frozen=True)
class ClassifiedSession:
    session: SleepSession
    kind: str           # night / nap
    night: str          # 속한 밤의 날짜


# ==================================================================== 병합


def merge_sessions(
    sessions: Sequence[SleepSession],
    merge_gap_min: float = MERGE_GAP_MIN,
    bridges: Iterable[datetime] = (),
) -> list[SleepSession]:
    """
    끊긴 수면 구간을 합친다. 입력 순서(기록 순서)를 그대로 쓴다.

    다음 중 하나면 앞 구간과 합친다.
      - 두 구간 사이 간격이 0 이상 merge_gap_min 이하
      - 두 구간 사이에 bridge 시각이 있다 (예: Bed_to_Toilet 시작)

    간격이 음수면(시각 역전) 합치지 않는다.
    """

    if not sessions:
        return []

    bridges = sorted(bridges)

    def bridged(a: SleepSession, b: SleepSession) -> bool:
        return any(a.end < t < b.start for t in bridges)

    merged = []
    current = sessions[0]

    for nxt in sessions[1:]:
        gap = (nxt.start - current.end).total_seconds() / 60

        if gap >= 0 and (gap <= merge_gap_min or bridged(current, nxt)):
            current = replace(
                current,
                end=nxt.end,
                wake_observed=nxt.wake_observed,
                parts=current.parts + nxt.parts,
                prompted=current.prompted or nxt.prompted,
            )
        else:
            merged.append(current)
            current = nxt

    merged.append(current)

    return merged


def drop_implausible(
    sessions: Sequence[SleepSession],
    max_hours: float = MAX_SESSION_HOURS,
) -> tuple[list[SleepSession], list[SleepSession]]:
    """(쓸 수 있는 수면, 버린 수면). 병합한 뒤에 적용한다."""

    kept, dropped = [], []

    for s in sessions:
        (dropped if s.minutes >= max_hours * 60 else kept).append(s)

    return kept, dropped


# ==================================================================== 밤잠 / 낮잠


def classify_sessions(
    sessions: Sequence[SleepSession],
    anchor_hour: int = NIGHT_ANCHOR_HOUR,
) -> list[ClassifiedSession]:
    """
    밤(anchor_hour ~ 다음 날 anchor_hour)마다 가장 긴 수면을 밤잠, 나머지를 낮잠으로 나눈다.
    길이가 같으면 먼저 기록된 것을 밤잠으로 본다. 결과는 입력 순서다.

    알려진 한계: 그 범위에 밤 수면 기록이 없으면(데이터 공백, 마지막 날) 오후 낮잠이
    가장 긴 수면이 되어 밤잠으로 분류된다. 막으려면 "평소 밤잠 길이 대비" 같은 기준값이
    필요한데 근거가 없어 두지 않았다 (실제 CASAS 6명 631밤 중 1건).
    """

    longest: dict[str, int] = {}

    for i, s in enumerate(sessions):
        night = s.night(anchor_hour)

        if night not in longest or s.minutes > sessions[longest[night]].minutes:
            longest[night] = i

    nights = set(longest.values())

    return [
        ClassifiedSession(
            session=s,
            kind=NIGHT if i in nights else NAP,
            night=s.night(anchor_hour),
        )
        for i, s in enumerate(sessions)
    ]


def nights(classified: Sequence[ClassifiedSession]) -> list[ClassifiedSession]:
    return [c for c in classified if c.kind == NIGHT]


def naps(classified: Sequence[ClassifiedSession]) -> list[ClassifiedSession]:
    return [c for c in classified if c.kind == NAP]


# ==================================================================== 학습 표본


def clock_minutes(ts: datetime) -> float:
    """로컬 시각 → 자정 기준 분"""

    return (
        ts.hour * 60
        + ts.minute
        + ts.second / 60
        + ts.microsecond / 60_000_000
    )


def night_samples(
    classified: Sequence[ClassifiedSession],
    source: str,
    proxy: bool,
) -> dict[str, list[KdeSample]]:
    """
    밤잠만 sleep_time / wake_time 표본으로 만든다. 낮잠은 학습하지 않는다.
    source 는 수면 기록에 출처가 없을 때 쓴다.

    date
      - sleep_time: 그 밤이 시작된 날 (00:30 취침이면 전날)
      - wake_time : 깬 날
    """

    sleep, wake = [], []

    for c in nights(classified):
        s = c.session

        origin = s.source or source

        sleep.append(
            KdeSample("sleep_time", clock_minutes(s.start), c.night, origin, s.prompted, proxy)
        )

        if s.wake_observed:
            wake.append(
                KdeSample("wake_time", clock_minutes(s.end), s.end.date().isoformat(), origin, s.prompted, proxy)
            )

    return {"sleep_time": sleep, "wake_time": wake}


# ==================================================================== Context Engine 이벤트


SLEEP_START = "sleep_start"
SLEEP_END = "sleep_end"
SLEEP_EVENT_TYPES = frozenset({SLEEP_START, SLEEP_END})


def _parse_t0(value) -> datetime:
    """epoch seconds(KST 로 변환) 또는 ISO 문자열(로컬 시각) → tz 없는 로컬 시각"""

    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
        return datetime.fromtimestamp(value, KST).replace(tzinfo=None)

    if isinstance(value, str):
        ts = datetime.fromisoformat(value)
        return ts.replace(tzinfo=None) if ts.tzinfo is None else ts.astimezone(KST).replace(tzinfo=None)

    raise ValueError(f"t0는 epoch seconds 또는 ISO 문자열이어야 합니다: {value!r}")


def _epoch(ts: datetime) -> float:
    """tz 없는 로컬(KST) 시각 → epoch seconds"""

    return round(ts.replace(tzinfo=KST).timestamp(), 3)


def sessions_from_events(records: Iterable[dict]) -> tuple[list[SleepSession], int]:
    """
    sleep_start / sleep_end 이벤트 → SleepSession. 다른 type 은 무시한다.

    t0 순으로 정렬해 start 와 그다음 end 를 짝짓는다.
      - end 없이 다음 start 가 오면 앞 start 는 기상 미관측 (길이 0)
      - 마지막 start 에 end 가 없으면 (아직 자는 중이거나 기록 누락) 기상 미관측
      - start 없는 end, t0 를 읽을 수 없는 이벤트는 버린다 (개수를 돌려준다)

    길이 0 수면은 같은 밤의 더 긴 수면에 밀린다. 그 밤의 유일한 수면이면 취침 시각만 학습한다.
    """

    events = []
    dropped = 0

    for r in records:
        if not isinstance(r, dict) or r.get("type") not in SLEEP_EVENT_TYPES:
            continue

        try:
            events.append((_parse_t0(r.get("t0")), r))
        except ValueError as exc:
            log.warning("sleep 이벤트 건너뜀 — %s: %s", exc, r)
            dropped += 1

    events.sort(key=lambda e: e[0])

    def open_session(ts: datetime, r: dict) -> SleepSession:
        area = r.get("area")
        source = r.get("source")
        return SleepSession(
            start=ts,
            end=ts,
            wake_observed=False,
            prompted=r.get("prompted") is True,
            area=area if isinstance(area, str) else None,
            source=source if isinstance(source, str) else "",
        )

    sessions = []
    pending: SleepSession | None = None

    for ts, r in events:
        if r["type"] == SLEEP_START:
            if pending is not None:
                sessions.append(pending)
            pending = open_session(ts, r)
            continue

        if pending is None:
            dropped += 1
            continue

        sessions.append(replace(pending, end=ts, wake_observed=True))
        pending = None

    if pending is not None:
        sessions.append(pending)

    return sessions, dropped


def samples_from_events(
    records: Iterable[dict],
    source: str = "sensor",
    merge_gap_min: float = MERGE_GAP_MIN,
    anchor_hour: int = NIGHT_ANCHOR_HOUR,
) -> tuple[dict[str, list[KdeSample]], list[ClassifiedSession]]:
    """
    Context Engine sleep 이벤트 → 병합 → 기록 오류 제외 → 밤잠 / 낮잠 구분 → 밤잠만 표본.
    """

    sessions, dropped = sessions_from_events(records)

    if dropped:
        log.info("짝이 맞지 않거나 읽을 수 없는 sleep 이벤트 %d건 제외", dropped)

    kept, implausible = drop_implausible(merge_sessions(sessions, merge_gap_min))

    if implausible:
        log.info("%d시간 이상 수면 %d건 제외 (기록 오류)", MAX_SESSION_HOURS, len(implausible))

    classified = classify_sessions(kept, anchor_hour)

    return night_samples(classified, source, proxy=False), classified


def session_events(
    classified: Sequence[ClassifiedSession],
    source: str,
) -> list[dict]:
    """
    수면 전체(밤잠 + 낮잠) → Context Engine t0 로그와 같은 sleep_start / sleep_end 이벤트.
    CASAS 개발 데이터를 실제 입력과 같은 모양으로 만들 때 쓴다.

    병합이 끝난 수면을 내보내므로 다시 읽어도 같은 결과가 나온다.
    기상을 관측하지 못한 수면은 sleep_start 만 낸다.
    """

    out = []

    for c in classified:
        s = c.session
        base = {"source": s.source or source, "prompted": s.prompted, "duration_sec": 0}

        start = {"date": s.start.date().isoformat(), "type": SLEEP_START, "t0": _epoch(s.start), **base}
        if s.area is not None:
            start["area"] = s.area
        out.append(start)

        if s.wake_observed:
            out.append({"date": s.end.date().isoformat(), "type": SLEEP_END, "t0": _epoch(s.end), **base})

    return out


def write_records(records: Sequence[dict], path) -> None:
    with open(path, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
