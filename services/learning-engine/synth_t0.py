"""
합성 t0 로그 생성기 (개발·시연용).

실기기를 한 달 켜 둘 수 없을 때, RPi4 t0_logger 가 쌓는 것과 같은 모양의 t0_log.jsonl 을 만든다.
run_learning.py(KDE) 와 수면 분석이 실데이터처럼 읽는다. 나중에 실기기 로그로 바꾸기만 하면 된다.

    RPi5 FileT0Log → hestia/log/t0 {version, sent_ts, src_id, **T0Entry} → t0_logger → t0_log.jsonl
                                     ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^ 이 한 줄을 흉내 낸다

Context Engine 동작을 따라 만든다 (hestia_engine/scenarios.py, fsm.py).
  - sleep_start t0 = 정지가 시작된 시각. 기록(sent_ts)은 확정 뒤 (프로브 15~30분)
  - sleep_start.awake_areas = 직전 sleep_end 이후 들른 구역 − 잠든 구역.
    밤중 화장실 → ["bathroom"], 누운 채 깸 → [], 엔진 시작 직후 첫 수면 → None
  - sleep_end t0 = 침대를 비운 시각 / 움직임이 시작된 시각
  - meal t0 = 묶음 시작(조리 또는 먹기), eat_t0 = 첫 먹기. 조리만 하고 안 먹으면 null. 기록은 묶음이 닫힌 뒤
  - hydration t0 = 급수 시각
  - medication 은 넣지 않는다 (KDE·수면 분석 대상이 아님)

사람마다 Persona 하나. 시각은 고정값이 아니라 그 사람의 평균 ± 흔들림으로 뽑는다.

    python3 services/learning-engine/synth_t0.py --persona p1 --output data/synthetic/p1/t0_log.jsonl
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np

from baseline import parse_t0_record
from sleep_sessions import KST


SCHEMA_VERSION = 1
SRC_ID = "rpi5"

BEDROOM = "bedroom"
LIVING = "living"
KITCHEN = "kitchen"
BATHROOM = "bathroom"
ENTRANCE = "entrance"


@dataclass(frozen=True)
class Persona:
    """
    시각은 자정 기준 분, 흔들림은 표준편차(분).
    nocturia: (그 밤부터, 밤중 화장실 평균 횟수). 뒤 항목이 앞 항목을 덮는다 — 추이 시연용.
    """

    key: str
    note: str
    bed_min: float
    bed_sd: float
    wake_min: float
    wake_sd: float
    nocturia: tuple[tuple[int, float], ...]
    long_wake_p: float              # 밤중에 길게(20~60분) 깨어 다른 방에 다녀온 밤의 비율
    in_bed_wake_p: float            # 누운 채 깬 밤의 비율 (침대를 안 떠남)
    nap_p: float
    nap_min: float
    meals: tuple[tuple[str, float, float, float, float], ...]   # (끼니, 시각, 흔들림, 거르는 비율, 조리 비율)
    hydration_per_day: tuple[int, int]
    outing_p: float                 # 현관 밖에 나간 날의 비율
    restart_nights: tuple[int, ...] = field(default=())   # 엔진이 재시작된 밤 → awake_areas None


PERSONAS = {
    "p1": Persona(
        key="p1",
        note="78세 독거. 규칙적. 밤중 화장실 평균 1회, 마지막 2주는 2회로 늘어남 (야간뇨 증가 시연)",
        bed_min=21 * 60 + 50, bed_sd=25,
        wake_min=5 * 60 + 40, wake_sd=20,
        nocturia=((0, 1.0), (42, 2.0)),
        long_wake_p=0.12,
        in_bed_wake_p=0.2,
        nap_p=0.35, nap_min=13 * 60 + 30,
        meals=(
            ("breakfast", 6 * 60 + 40, 20, 0.03, 0.4),
            ("lunch", 12 * 60, 20, 0.10, 0.5),
            ("dinner", 17 * 60 + 40, 25, 0.02, 0.8),
        ),
        hydration_per_day=(4, 7),
        outing_p=0.5,
        restart_nights=(20,),
    ),
}

# 밤중 화장실 사이 최소 간격(분). 이보다 가까우면 한 번 다녀온 것이다.
TOILET_SPACING_MIN = 25


# ==================================================================== 레코드


def epoch(ts: datetime) -> float:
    return round(ts.timestamp(), 3)


def record(type_: str, t0: datetime, sent: datetime, *, prompted: bool = False, duration_sec: float = 0.0, **extra) -> dict:
    """T0Entry 전 필드 + MQTT 봉투. FileT0Log 는 asdict 라 안 쓰는 필드도 null 로 나간다."""

    entry = {
        "date": t0.strftime("%Y-%m-%d"),
        "type": type_,
        "t0": epoch(t0),
        "source": "sensor",
        "prompted": prompted,
        "duration_sec": round(duration_sec, 1),
        "area": None,
        "method": None,
        "confidence": None,
        "awake_areas": None,
        "eat_t0": None,
    }
    entry.update(extra)
    return {"version": SCHEMA_VERSION, "sent_ts": int(sent.timestamp()), "src_id": SRC_ID, **entry}


# ==================================================================== 생성


class Generator:
    def __init__(self, persona: Persona, start: date, days: int, seed: int) -> None:
        self.p = persona
        self.start = datetime(start.year, start.month, start.day, tzinfo=KST)
        self.days = days
        self.rng = np.random.default_rng(seed)
        self.out: list[dict] = []
        self.visits: list[tuple[datetime, str]] = []
        self.last_end: datetime | None = None      # None = 엔진이 직전 sleep_end 를 못 봄

    # ------------------------------------------------------------ 도우미

    def at(self, day: int, minute: float) -> datetime:
        return self.start + timedelta(days=day, minutes=float(minute))

    def normal(self, mean: float, sd: float) -> float:
        return float(self.rng.normal(mean, sd))

    def uniform(self, lo: float, hi: float) -> float:
        return float(self.rng.uniform(lo, hi))

    def chance(self, p: float) -> bool:
        return bool(self.rng.random() < p)

    def lognormal_min(self, median: float, sigma: float) -> float:
        return float(median * np.exp(self.rng.normal(0, sigma)))

    def visit(self, ts: datetime, area: str) -> None:
        self.visits.append((ts, area))

    def awake_areas(self, until: datetime, slept_in: str) -> list[str] | None:
        """직전 sleep_end ~ until 사이 들른 구역 − 잠든 구역 (scenarios._take_awake_areas)"""
        if self.last_end is None:
            return None
        areas = {a for t, a in self.visits if self.last_end <= t < until}
        return sorted(areas - {slept_in})

    def nocturia(self, night: int) -> float:
        lam = 0.0
        for from_night, value in self.p.nocturia:
            if night >= from_night:
                lam = value
        return lam

    # ------------------------------------------------------------ 수면

    def sleep_start(self, t0: datetime, area: str, method: str, *, restart: bool = False) -> None:
        awake = None if restart else self.awake_areas(t0, area)
        confidence = {"banner": 0.9, "dim": 0.9, "none": 0.6}[method]
        probe = 900 if method != "none" else 1800       # probe_still_sec / probe_still_sec_no_channel
        sent = t0 + timedelta(seconds=probe + self.uniform(5, 120))
        self.out.append(record(
            "sleep_start", t0, sent,
            area=area, method=method, confidence=confidence, awake_areas=awake,
        ))

    def sleep_end(self, t0: datetime) -> None:
        self.out.append(record("sleep_end", t0, t0 + timedelta(seconds=self.uniform(1, 60))))
        self.last_end = t0

    def night(self, day: int, onset: datetime, wake: datetime) -> None:
        """onset ~ wake 사이 밤중 깸. 각 깸 = sleep_end(나감) → sleep_start(다시 정지)."""

        first, last = onset + timedelta(minutes=60), wake - timedelta(minutes=30)
        span = (last - first).total_seconds() / 60

        kept: list[tuple[datetime, float, list[str], str]] = []    # (나간 시각, 길이 분, 들른 곳, 다시 잠들 때 method)

        def place(minutes: float, areas: list[str], method: str, lo: float = 0.0, tries: int = 20) -> None:
            """다른 깸과 TOILET_SPACING_MIN 이상 떨어진 자리를 뽑는다. 자리가 없으면 그 깸은 없던 것이다."""
            for _ in range(tries):
                left = first + timedelta(minutes=self.uniform(lo, span))
                back = left + timedelta(minutes=minutes)
                if back >= wake - timedelta(minutes=10):
                    continue
                gap = timedelta(minutes=TOILET_SPACING_MIN)
                if all(back + gap <= l or l + timedelta(minutes=m) + gap <= left for l, m, _, _ in kept):
                    kept.append((left, minutes, areas, method))
                    return

        # 화장실이 먼저 자리를 잡는다 — 횟수가 이 사람의 핵심 지표다
        for _ in range(min(int(self.rng.poisson(self.nocturia(day))), 4)):
            out = self.lognormal_min(4, 0.35)          # 화장실 왕복
            settle = self.lognormal_min(3, 0.5)        # 다시 누워 가만해질 때까지
            place(out + settle, [BATHROOM], "dim")

        if self.chance(self.p.long_wake_p):
            areas = [[KITCHEN], [LIVING], [BATHROOM, KITCHEN]][int(self.rng.integers(3))]
            place(self.uniform(20, 60), areas, "dim", lo=30)

        if self.chance(self.p.in_bed_wake_p):
            place(self.uniform(2, 8), [], "none")

        kept.sort(key=lambda w: w[0])

        for left, minutes, areas, method in kept:
            self.sleep_end(left)
            back = left + timedelta(minutes=minutes)
            for i, a in enumerate(areas):
                self.visit(left + timedelta(minutes=minutes * (i + 1) / (len(areas) + 1)), a)
            self.sleep_start(back, BEDROOM, method)

    # ------------------------------------------------------------ 낮

    def meals(self, day: int, wake: datetime) -> list[tuple[datetime, datetime]]:
        """식사 묶음 기록. (t0, 끝) 목록을 돌려준다 — 낮잠이 겹치지 않게."""

        spans = []
        for name, mean, sd, skip_p, cook_p in self.p.meals:
            if self.chance(skip_p):
                continue

            t0 = self.at(day, self.normal(mean, sd))
            if name == "breakfast":
                t0 = max(t0, wake + timedelta(minutes=20))

            prompted = self.chance(0.06)                # 늦어서 알림 받고 먹음
            if prompted:
                t0 += timedelta(minutes=self.uniform(30, 60))

            cooked = self.chance(cook_p)
            if cooked and self.chance(0.03):            # 조리만 하고 안 먹음
                eat, duration = None, self.uniform(20, 40)
            elif cooked:
                eat, duration = t0 + timedelta(minutes=self.uniform(15, 30)), self.uniform(35, 60)
            else:
                eat, duration = t0 + timedelta(minutes=self.uniform(2, 6)), self.uniform(15, 30)

            end = t0 + timedelta(minutes=duration)
            self.visit(t0, KITCHEN)
            self.visit(end + timedelta(minutes=2), LIVING)
            self.out.append(record(
                "meal", t0, end + timedelta(seconds=600),       # 이탈 후 10분 유예가 지나야 닫힌다
                prompted=prompted, duration_sec=duration * 60,
                eat_t0=None if eat is None else epoch(eat),
            ))
            spans.append((t0, end))
        return spans

    def daytime(self, day: int, wake: datetime, bed: datetime) -> None:
        self.visit(wake + timedelta(minutes=2), BATHROOM)
        self.visit(wake + timedelta(minutes=8), LIVING)

        meal_spans = self.meals(day, wake)

        nap: tuple[datetime, datetime] | None = None
        if self.chance(self.p.nap_p):
            start = self.at(day, self.normal(self.p.nap_min, 30))
            after_lunch = [e for s, e in meal_spans if s.hour < 15]
            if after_lunch:
                start = max(start, max(after_lunch) + timedelta(minutes=20))
            end = start + timedelta(minutes=self.uniform(20, 50))
            dinner = [s for s, _ in meal_spans if s > start]
            if not dinner or end < min(dinner) - timedelta(minutes=30):
                nap = (start, end)

        for _ in range(int(self.rng.integers(4, 8))):
            t = wake + timedelta(minutes=self.uniform(60, (bed - wake).total_seconds() / 60 - 30))
            if nap is None or not (nap[0] <= t <= nap[1]):
                self.visit(t, BATHROOM)
                self.visit(t + timedelta(minutes=5), LIVING)

        if self.chance(self.p.outing_p):
            t = self.at(day, self.uniform(9 * 60, 16 * 60))
            if nap is None or not (nap[0] - timedelta(hours=2) <= t <= nap[1]):
                self.visit(t, ENTRANCE)
                self.visit(t + timedelta(minutes=self.uniform(30, 120)), LIVING)

        lo, hi = self.p.hydration_per_day
        first = wake + timedelta(minutes=self.uniform(10, 40))
        times = [first] + [
            wake + timedelta(minutes=self.uniform(60, (bed - wake).total_seconds() / 60 - 60))
            for _ in range(int(self.rng.integers(lo, hi + 1)) - 1)
        ]
        for i, t in enumerate(sorted(times)):
            self.visit(t, KITCHEN)
            prompted = i == 0 and self.chance(0.3)
            self.out.append(record("hydration", t, t, prompted=prompted))

        if nap is not None:
            self.sleep_start(nap[0], LIVING, "banner")
            self.sleep_end(nap[1])

        self.visit(bed - timedelta(minutes=10), BATHROOM)

    # ------------------------------------------------------------ 전체

    def run(self) -> list[dict]:
        wake = self.at(0, self.normal(self.p.wake_min, self.p.wake_sd))

        for day in range(self.days):
            bed = self.at(day, self.normal(self.p.bed_min, self.p.bed_sd))
            next_wake = self.at(day + 1, self.normal(self.p.wake_min, self.p.wake_sd))

            self.daytime(day, wake, bed)

            method = "dim" if self.chance(0.7) else "none"
            self.sleep_start(bed, BEDROOM, method, restart=day in self.p.restart_nights)
            self.night(day, bed, next_wake)
            self.sleep_end(next_wake)
            self.visit(next_wake + timedelta(minutes=2), BATHROOM)

            wake = next_wake

        self.out.sort(key=lambda r: (r["sent_ts"], r["t0"]))
        for r in self.out:
            parse_t0_record(r)                          # t0_logger 와 같은 검증기
        return self.out


def generate(persona: Persona, start: date, days: int, seed: int = 0) -> list[dict]:
    return Generator(persona, start, days, seed).run()


def write_jsonl(records: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fp:
        for r in records:
            fp.write(json.dumps(r, ensure_ascii=False) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="합성 t0 로그 생성 (개발·시연용)")
    parser.add_argument("--persona", default="p1", choices=sorted(PERSONAS))
    parser.add_argument("--start", type=date.fromisoformat, default=date(2026, 8, 17), help="첫날 (KST)")
    parser.add_argument("--days", type=int, default=56)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    persona = PERSONAS[args.persona]
    records = generate(persona, args.start, args.days, args.seed)
    write_jsonl(records, args.output)

    counts: dict[str, int] = {}
    for r in records:
        counts[r["type"]] = counts.get(r["type"], 0) + 1
    print(f"{persona.key}: {persona.note}")
    print(f"{args.start} 부터 {args.days}일, {len(records)}줄 → {args.output}")
    print("  " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items())))


if __name__ == "__main__":
    main()
