"""
여러 CASAS 데이터셋에서 거주자별 sleep_time / wake_time proxy 표본을 만든다.

Aruba 한 사람만으로는 "규칙적인 사람 / 불규칙한 사람"의 predictability 차이를
실제 데이터로 볼 수 없어서 다른 데이터셋을 더한다. 전부 proxy 다.

라벨은 원본에서 직접 확인한 문자열이다 (data/raw/casas/aruba/).

    데이터셋  기간                   거주자  수면 라벨
    aruba    2010-11-04~2011-06-11   1     Sleeping begin/end  (+ Bed_to_Toilet)
    milan    2009-10-16~2010-01-06   1     Sleep begin/end     (+ Bed_to_Toilet)
    tulum2   2009-09-25~2010-03-28   2     R1_/R2_Sleeping_in_Bed begin/end
    cairo    2009-06-10~2009-08-05   2     R1_/R2_Sleep, R1_/R2_Wake (짧은 활동)

수면 기록 방식이 두 가지다.

    segment  수면 자체를 begin~end 로 기록 (aruba, milan, tulum2)
    event    잠자리에 드는 활동과 일어나는 활동을 따로 기록 (cairo)
             예: R1_Sleep 20:54~21:21, R1_Wake 05:46~05:51
             → 취침 활동 시작 ~ 다음 기상 활동 시작을 한 번의 수면으로 본다 (해석)
"""

from __future__ import annotations

import argparse
import logging
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from aruba import (
    ActivityEvent,
    ArubaLog,
    SleepExtraction,
    extract_session_label,
    read_events,
)
from sleep_sessions import (
    MERGE_GAP_MIN,
    NIGHT_ANCHOR_HOUR,
    SleepSession,
    classify_sessions,
    drop_implausible,
    session_events,
    write_records,
)


log = logging.getLogger(__name__)


RAW_DIR = Path("data/raw/casas/aruba")

SEGMENT = "segment"
EVENT = "event"


@dataclass(frozen=True)
class SleepSpec:
    dataset: str
    person: str
    file: str
    mode: str                       # segment / event
    sleep_label: str
    wake_label: str | None = None   # event 방식에서만
    toilet_label: str | None = None # segment 병합 bridge. 누구 것인지 알 수 있을 때만
    note: str = ""

    @property
    def key(self) -> str:
        return f"{self.dataset}/{self.person}"


SPECS = (
    SleepSpec("aruba", "R1", "aruba.txt", SEGMENT, "Sleeping", toilet_label="Bed_to_Toilet"),
    SleepSpec("milan", "R1", "milan.txt", SEGMENT, "Sleep", toilet_label="Bed_to_Toilet"),
    SleepSpec(
        "tulum2", "R1", "tulum2.txt", SEGMENT, "R1_Sleeping_in_Bed",
        note="2인 가구 — Bed_Toilet_Transition 은 누구 것인지 몰라 간격으로만 병합",
    ),
    SleepSpec(
        "tulum2", "R2", "tulum2.txt", SEGMENT, "R2_Sleeping_in_Bed",
        note="2인 가구 — Bed_Toilet_Transition 은 누구 것인지 몰라 간격으로만 병합",
    ),
    SleepSpec(
        "cairo", "R1", "cairo.txt", EVENT, "R1_Sleep", wake_label="R1_Wake",
        note="취침 활동 시작 ~ 다음 기상 활동 시작을 수면으로 해석",
    ),
    SleepSpec(
        "cairo", "R2", "cairo.txt", EVENT, "R2_Sleep", wake_label="R2_Wake",
        note="취침 활동 시작 ~ 다음 기상 활동 시작을 수면으로 해석",
    ),
)


def event_sessions(
    events: list[ActivityEvent],
    sleep_label: str,
    wake_label: str,
) -> tuple[list[SleepSession], int]:
    """
    취침 활동 begin → 다음 기상 활동 begin 을 한 번의 수면으로 묶는다.

    - 기상 없이 다음 취침이 오면 앞 취침은 기상 미관측 (길이 0) 으로 남긴다.
      밤잠 선택에서 같은 밤의 더 긴 수면에 밀린다.
    - 취침 없이 온 기상, 시각이 거꾸로인 기상은 버린다 (개수를 돌려준다).
    """

    sessions = []
    dropped = 0
    pending: ActivityEvent | None = None

    for e in events:
        if e.kind != "begin":
            continue

        if e.label == sleep_label:
            if pending is not None:
                sessions.append(SleepSession(pending.ts, pending.ts, wake_observed=False))
            pending = e
            continue

        if e.label != wake_label:
            continue

        if pending is None or e.ts < pending.ts:
            dropped += 1
            continue

        sessions.append(SleepSession(pending.ts, e.ts))
        pending = None

    if pending is not None:
        sessions.append(SleepSession(pending.ts, pending.ts, wake_observed=False))

    return sessions, dropped


def extract_person(
    spec: SleepSpec,
    casas_log: ArubaLog,
    merge_gap_min: float = MERGE_GAP_MIN,
    anchor_hour: int = NIGHT_ANCHOR_HOUR,
) -> SleepExtraction:
    if spec.mode == SEGMENT:
        return extract_session_label(
            casas_log,
            spec.sleep_label,
            spec.toilet_label,
            spec.dataset,
            merge_gap_min,
            anchor_hour,
        )

    if spec.mode == EVENT:
        sessions, dropped = event_sessions(casas_log.events, spec.sleep_label, spec.wake_label)
        sessions, implausible = drop_implausible(sessions)

        return SleepExtraction(
            classified=classify_sessions(sessions, anchor_hour),
            dropped_segments=dropped,
            implausible=implausible,
            source=spec.dataset,
        )

    raise ValueError(f"알 수 없는 mode: {spec.mode!r}")


def extract_all(
    raw_dir: Path = RAW_DIR,
    specs: tuple[SleepSpec, ...] = SPECS,
    merge_gap_min: float = MERGE_GAP_MIN,
    anchor_hour: int = NIGHT_ANCHOR_HOUR,
) -> list[tuple[SleepSpec, SleepExtraction]]:
    """
    원본 파일이 있는 데이터셋만 처리한다 (data/raw 는 gitignore).
    파일은 데이터셋마다 한 번만 읽는다.
    """

    out = []
    logs: dict[str, ArubaLog] = {}

    for spec in specs:
        path = raw_dir / spec.file

        if not path.exists():
            log.info("%s 원본 없음: %s", spec.key, path)
            continue

        if spec.file not in logs:
            labels = {
                s.sleep_label for s in specs if s.file == spec.file
            } | {
                s.wake_label for s in specs if s.file == spec.file and s.wake_label
            } | {
                s.toilet_label for s in specs if s.file == spec.file and s.toilet_label
            }
            logs[spec.file] = read_events(path, labels)

        out.append(
            (spec, extract_person(spec, logs[spec.file], merge_gap_min, anchor_hour))
        )

    return out


# ==================================================================== 보고


def person_summary(spec: SleepSpec, sleep: SleepExtraction) -> dict:
    from baseline import InsufficientSamples, calculate_predictability, fit_distribution
    from samples import values

    row = {
        "key": spec.key,
        "nights": len(sleep.main),
        "naps": len(sleep.naps),
        "dropped": sleep.dropped_segments,
        "implausible": len(sleep.implausible),
        "median_hours": float(np.median([s.minutes for s in sleep.main]) / 60) if sleep.main else 0.0,
    }

    for name, samples in (("sleep_time", sleep.sleep_time), ("wake_time", sleep.wake_time)):
        row[f"{name}_n"] = len(samples)

        try:
            _, density = fit_distribution(name, values(samples))
            row[name] = calculate_predictability(density)
        except InsufficientSamples:
            row[name] = None

    return row


def report(raw_dir: Path = RAW_DIR) -> None:
    print(f"\n===== CASAS sleep / wake proxy ({raw_dir}) =====\n")
    print(f"{'person':<11} {'nights':>6} {'naps':>5} {'drop':>5} {'>=24h':>5} {'median h':>8} "
          f"{'sleep n':>7} {'sleep pred':>10} {'wake n':>6} {'wake pred':>9}")

    for spec, sleep in extract_all(raw_dir):
        r = person_summary(spec, sleep)
        fmt = lambda v: "—" if v is None else f"{v:.3f}"
        print(f"{r['key']:<11} {r['nights']:>6} {r['naps']:>5} {r['dropped']:>5} {r['implausible']:>5} {r['median_hours']:>8.1f} "
              f"{r['sleep_time_n']:>7} {fmt(r['sleep_time']):>10} {r['wake_time_n']:>6} {fmt(r['wake_time']):>9}")

    print()


def export_jsonl(out_dir: Path, raw_dir: Path = RAW_DIR) -> list[Path]:
    """
    거주자별 t0 JSONL — Context Engine t0 로그와 같은 형식.
    baseline.py --t0-jsonl 로 다시 읽으면 밤잠 / 낮잠을 다시 구분해 학습한다.

    sleep_start / sleep_end   모든 거주자
    meal (t0 / eat_t0)        1인 가구 Aruba 만 — 2인 가구의 식사 라벨은 누구 것인지 알 수 없다
    """

    from aruba import EAT_LABEL, MEAL_LABEL, meal_records, meal_sessions

    out_dir.mkdir(parents=True, exist_ok=True)
    written = []

    for spec, sleep in extract_all(raw_dir):
        path = out_dir / f"{spec.dataset}_{spec.person}_t0.jsonl"
        records = session_events(sleep.classified, spec.dataset)

        if spec.dataset == "aruba":
            meal_log = read_events(raw_dir / spec.file, {MEAL_LABEL, EAT_LABEL})
            records += meal_records(meal_sessions(meal_log.events), spec.dataset)

        write_records(records, path)
        written.append(path)

    return written


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="CASAS 데이터셋별 sleep / wake proxy")
    parser.add_argument("raw_dir", nargs="?", type=Path, default=RAW_DIR)
    parser.add_argument("--export-jsonl", type=Path, help="거주자별 sleep_start / sleep_end t0 JSONL 저장 디렉터리")
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING)

    report(args.raw_dir)

    if args.export_jsonl:
        for path in export_jsonl(args.export_jsonl, args.raw_dir):
            print(f"[EXPORT] {path}")
