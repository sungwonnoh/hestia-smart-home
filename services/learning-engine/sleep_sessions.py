"""
수면 기록 → 밤잠 / 낮잠 구분 → sleep_time / wake_time 학습 표본.

이 모듈은 "학습 데이터 선별"만 한다 (RPi4 배치).
지금 이 수면이 밤잠인지 실시간으로 판정하는 일은 RPi5 Context Engine 의 책임이다.

    수면 기록 (CASAS 라벨 / 내부 sleep 레코드)
        ↓ merge_sessions      화장실 등으로 끊긴 구간을 한 번의 수면으로
        ↓ classify_sessions   밤마다 가장 긴 수면 = 밤잠(night), 나머지 = 낮잠(nap)
        ↓ night_samples       밤잠만 sleep_time / wake_time 표본으로

임의의 취침·기상 시각을 기준으로 쓰지 않는다. "가장 긴 수면"만 본다.
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

    date
      - sleep_time: 그 밤이 시작된 날 (00:30 취침이면 전날)
      - wake_time : 깬 날
    """

    sleep, wake = [], []

    for c in nights(classified):
        s = c.session

        sleep.append(
            KdeSample("sleep_time", clock_minutes(s.start), c.night, source, s.prompted, proxy)
        )

        if s.wake_observed:
            wake.append(
                KdeSample("wake_time", clock_minutes(s.end), s.end.date().isoformat(), source, s.prompted, proxy)
            )

    return {"sleep_time": sleep, "wake_time": wake}


# ==================================================================== 내부 레코드


# 기상 레코드가 수면 끝과 같은 시각인지 볼 때의 허용 오차 (초 단위 반올림 대비)
WAKE_MATCH_SEC = 1.0


def session_records(
    classified: Sequence[ClassifiedSession],
    source: str,
) -> list[dict]:
    """
    수면 전체(밤잠 + 낮잠) → SLEEP.md 의 내부 sleep / wake 레코드.
    Context Engine 이 수면 기록을 넘겨주는 상황을 흉내 낸다 — 밤잠 / 낮잠 구분은 읽는 쪽이 한다.

        sleep  t0 = 잠든 시각, duration_sec = 수면 길이
        wake   t0 = 깬 시각 (기상을 관측한 수면만)

    production hestia/log/t0 schema 가 아니다 (현재 명세에 sleep type 없음).
    """

    out = []

    for c in classified:
        s = c.session

        out.append({
            "date": c.night,
            "type": "sleep",
            "t0": s.start.isoformat(timespec="seconds"),
            "source": source,
            "prompted": s.prompted,
            "duration_sec": round(s.minutes * 60, 3),
        })

        if s.wake_observed:
            out.append({
                "date": s.end.date().isoformat(),
                "type": "wake",
                "t0": s.end.isoformat(timespec="seconds"),
                "source": source,
                "prompted": s.prompted,
                "duration_sec": 0,
            })

    return out


def write_records(records: Sequence[dict], path) -> None:
    with open(path, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def load_records(path) -> list[dict]:
    """JSONL → 레코드 목록. 깨진 줄은 경고 후 건너뛴다."""

    out = []

    with open(path, "r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, start=1):
            line = line.strip()

            if not line:
                continue

            try:
                obj = json.loads(line)
            except json.JSONDecodeError as exc:
                log.warning("%s:%d 건너뜀 — 잘못된 JSON: %s", path, line_no, exc)
                continue

            if not isinstance(obj, dict):
                log.warning("%s:%d 건너뜀 — 객체가 아님", path, line_no)
                continue

            out.append(obj)

    return out


def _parse_t0(value) -> datetime:
    """ISO 문자열(로컬 시각) 또는 epoch seconds(KST 로 변환)"""

    if isinstance(value, str):
        ts = datetime.fromisoformat(value)
        return ts.replace(tzinfo=None) if ts.tzinfo is None else ts.astimezone(KST).replace(tzinfo=None)

    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
        return datetime.fromtimestamp(value, KST).replace(tzinfo=None)

    raise ValueError(f"t0는 ISO 문자열 또는 epoch seconds여야 합니다: {value!r}")


def parse_session_record(obj) -> SleepSession:
    """
    내부 sleep 레코드 한 건 → SleepSession.

        {"type": "sleep", "t0": 잠든 시각, "duration_sec": 수면 길이, "prompted": ...}

    duration_sec = 0 은 끝을 모르는 수면이다 (예: 취침만 기록되고 기상 기록 없음).
    실제 Context Engine sleep 계약이 정해지면 이 adapter 를 그 형식에 맞춘다.
    """

    if not isinstance(obj, dict):
        raise ValueError("레코드가 객체가 아닙니다.")

    if obj.get("type") != "sleep":
        raise ValueError(f"sleep 레코드가 아닙니다: type={obj.get('type')!r}")

    for key in ("t0", "duration_sec"):
        if key not in obj:
            raise ValueError(f"필수 필드 누락: {key}")

    duration = obj["duration_sec"]

    if (
        not isinstance(duration, (int, float))
        or isinstance(duration, bool)
        or not math.isfinite(duration)
        or duration < 0
    ):
        raise ValueError(f"duration_sec는 0 이상이어야 합니다: {duration!r}")

    prompted = obj.get("prompted", False)

    if not isinstance(prompted, bool):
        raise ValueError("prompted는 bool이어야 합니다.")

    start = _parse_t0(obj["t0"])

    return SleepSession(
        start=start,
        end=start + timedelta(seconds=duration),
        prompted=prompted,
    )


def samples_from_records(
    records: Iterable[dict],
    source: str = "sensor",
    merge_gap_min: float = MERGE_GAP_MIN,
    anchor_hour: int = NIGHT_ANCHOR_HOUR,
) -> tuple[dict[str, list[KdeSample]], list[ClassifiedSession]]:
    """
    내부 sleep / wake 레코드 → 밤잠 / 낮잠 구분 → 밤잠만 표본.

    기상 시각은 sleep 의 끝(t0 + duration_sec)에서 얻는다.
    그 시각과 맞는 wake 레코드가 있을 때만 기상을 관측한 것으로 본다
    (예: 기록 종료로 끊긴 수면은 wake 레코드가 없다 → 취침 시각만 학습).
    잘못된 sleep 레코드는 경고 후 건너뛴다.
    """

    records = list(records)

    wakes = [
        _parse_t0(r["t0"])
        for r in records
        if isinstance(r, dict) and r.get("type") == "wake" and "t0" in r
    ]

    sessions = []

    for r in records:
        if not (isinstance(r, dict) and r.get("type") == "sleep"):
            continue

        try:
            s = parse_session_record(r)
        except ValueError as exc:
            log.warning("sleep 레코드 건너뜀 — %s: %s", exc, r)
            continue

        observed = any(abs((w - s.end).total_seconds()) <= WAKE_MATCH_SEC for w in wakes)
        sessions.append(replace(s, wake_observed=observed))

    sessions.sort(key=lambda s: s.start)

    kept, _ = drop_implausible(merge_sessions(sessions, merge_gap_min))
    classified = classify_sessions(kept, anchor_hour)

    return night_samples(classified, source, proxy=False), classified
