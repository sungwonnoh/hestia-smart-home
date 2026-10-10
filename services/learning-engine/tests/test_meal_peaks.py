"""meal_time peaks — 끼니 구간과 끼니별 predictability (Context Engine 합의)."""

import copy
import sys
from pathlib import Path

import numpy as np
import pytest

from baseline import MEAL_MAX_MEALS_PER_DAY, MEAL_MIN_DAYS_RATIO, build_model, calculate_predictability, fit_distribution, meal_peaks
from meal_slots import find_peaks, slot_ranges
from model_payload import PayloadError, build_kde_payload, to_json, validate_kde_payload
from samples import KdeSample
from weighting import SampleWeighting


REPO = Path(__file__).resolve().parents[3]
RAW_DIR = REPO / "data" / "raw" / "casas" / "aruba"
CONTEXT_ENGINE = REPO / "services" / "context-engine"


def meals(centers=(7 * 60 + 30, 12 * 60 + 30, 18 * 60 + 30), spread=(20, 30, 40), n=40, seed=0):
    rng = np.random.default_rng(seed)
    out = []
    for c, sd in zip(centers, spread):
        for i, v in enumerate(rng.normal(c, sd, n)):
            out.append(KdeSample("meal_time", float(v % 1440), f"2026-09-{1 + i % 28:02d}", "sensor"))
    return out


@pytest.fixture(scope="module")
def three_meal_model():
    return build_model(meals())


# ============================================================ 생성


def test_three_meals_have_three_peaks(three_meal_model):
    peaks = three_meal_model["distributions"]["meal_time"]["peaks"]
    assert len(peaks) == 3
    assert [p["center"] for p in peaks] == sorted(p["center"] for p in peaks)
    assert set(peaks[0]) == {"center", "from", "to", "predictability", "days_ratio", "meals_per_day"}


def test_peak_units_are_grid_minutes(three_meal_model):
    for p in three_meal_model["distributions"]["meal_time"]["peaks"]:
        assert all(p[k] % 15 == 0 and 0 <= p[k] < 1440 for k in ("center", "from", "to"))
    centers = [p["center"] for p in three_meal_model["distributions"]["meal_time"]["peaks"]]
    assert [abs(c - t) <= 30 for c, t in zip(centers, (450, 750, 1110))] == [True] * 3


def test_slots_partition_the_day(three_meal_model):
    """구간은 하루를 빈틈없이 나눈다 — 앞 끼니 to == 다음 끼니 from, 마지막은 자정을 넘어 처음으로."""
    peaks = three_meal_model["distributions"]["meal_time"]["peaks"]
    for a, b in zip(peaks, peaks[1:] + peaks[:1]):
        assert a["to"] == b["from"]
    assert peaks[-1]["from"] > peaks[-1]["to"]                    # 자정을 넘는 마지막 끼니


def test_per_meal_predictability_is_per_slot_kde(three_meal_model):
    """끼니별 predictability = 그 구간 식사만으로 다시 그린 KDE (96칸) — 전체보다 높다."""
    peaks = three_meal_model["distributions"]["meal_time"]["peaks"]
    whole = three_meal_model["predictability"]["meal_time"]
    assert all(p["predictability"] > whole for p in peaks)
    # 퍼짐이 작은 끼니일수록 규칙적
    assert peaks[0]["predictability"] > peaks[1]["predictability"] > peaks[2]["predictability"]


def test_single_meal_has_no_peaks():
    model = build_model(meals(centers=(8 * 60,), spread=(20,)))
    assert "peaks" not in model["distributions"]["meal_time"]


def test_other_distributions_have_no_peaks():
    samples = meals() + [KdeSample("wake_time", float(v), f"2026-09-{i + 1:02d}", "sensor")
                         for i, v in enumerate((400, 420, 440, 410))]
    model = build_model(samples)
    assert "peaks" not in model["distributions"]["wake_time"]


def test_sparse_slot_predictability_is_null():
    """구간에 식사가 2개 미만이면 predictability = null."""
    density = np.full(96, 0.2 / 96)
    density[30] += 0.4
    density[70] += 0.4
    density /= density.sum()
    values = np.array([450.0, 455.0, 460.0, 1050.0])         # 두 번째 끼니에는 1개
    peaks, _ = meal_peaks(values, density.tolist(), min_days_ratio=None, max_meals_per_day=None)
    assert len(peaks) == 2
    assert peaks[0]["predictability"] is not None
    assert peaks[1]["predictability"] is None


def test_weights_apply_to_per_meal_predictability():
    samples = meals()
    plain = build_model(samples)["distributions"]["meal_time"]["peaks"]
    weighted = build_model(samples, sample_weighting=SampleWeighting(recent_lambda=0.1))["distributions"]["meal_time"]["peaks"]
    assert [p["predictability"] for p in plain] != [p["predictability"] for p in weighted]


def test_slot_ranges_wrap_midnight():
    density = np.full(96, 0.2 / 96)
    density[2] += 0.4                                        # 00:30 봉우리
    density[50] += 0.4
    density /= density.sum()
    peaks = find_peaks(density)
    ranges = slot_ranges(density, peaks)
    assert len(ranges) == 2
    assert any(start > end for start, end in ranges)          # 하나는 자정을 넘는다


# ============================================================ days_ratio / meals_per_day / 거르기


def daily_meals(slots, days=20, seed=0):
    """slots: (center 분, sd, 먹는 날 수, 하루 횟수)"""
    rng = np.random.default_rng(seed)
    out = []
    for day in range(days):
        date = f"2026-{1 + day // 28:02d}-{1 + day % 28:02d}"
        for center, sd, eat_days, times in slots:
            if day < eat_days:
                for _ in range(times):
                    out.append(KdeSample("meal_time", float(rng.normal(center, sd) % 1440), date, "sensor"))
    return out


def test_days_ratio_and_meals_per_day():
    model = build_model(daily_meals([(450, 10, 40, 1), (750, 10, 28, 1), (1110, 10, 40, 2)], days=40),
                        meal_min_days_ratio=None, meal_max_meals_per_day=None)
    peaks = model["distributions"]["meal_time"]["peaks"]
    assert [round(p["days_ratio"], 2) for p in peaks] == [1.0, 0.7, 1.0]
    assert [round(p["meals_per_day"], 2) for p in peaks] == [1.0, 0.7, 2.0]


def test_default_thresholds():
    """Context Engine MEAL 설계 합의값 (임시)."""
    assert (MEAL_MIN_DAYS_RATIO, MEAL_MAX_MEALS_PER_DAY) == (0.5, 1.5)


def test_merged_peak_is_not_sent():
    """두 끼가 한 봉우리로 합쳐지면(meals_per_day ≈ 2) 보내지 않는다 — 뒤 끼니 거름을 못 잡기 때문."""
    samples = daily_meals([(450, 10, 40, 1), (1110, 10, 40, 2)], days=40)
    model = build_model(samples)
    peaks = model["distributions"]["meal_time"]["peaks"]
    assert len(peaks) == 1 and abs(peaks[0]["center"] - 450) <= 30
    [dropped] = model["meta"]["meal_time"]["meal_peaks_dropped"]
    assert dropped["meals_per_day"] == 2.0 and dropped["reason"] == ["meals_per_day > 1.5"]


def test_meals_per_day_boundary_is_kept():
    density = np.full(96, 0.2 / 96)
    density[30] += 0.4
    density[70] += 0.4
    density /= density.sum()
    values = np.array([450.0, 452.0, 455.0, 1050.0, 1055.0])
    dates = ["d1", "d1", "d2", "d1", "d2"]                          # 아침 3끼 / 2일 = 1.5
    peaks, dropped = meal_peaks(values, density.tolist(), dates=dates, min_days_ratio=None)
    assert [p["meals_per_day"] for p in peaks] == [1.5, 1.0] and dropped == []


def test_both_reasons_recorded():
    density = np.full(96, 0.2 / 96)
    density[30] += 0.4
    density[70] += 0.4
    density /= density.sum()
    values = np.array([450.0, 452.0, 455.0, 1050.0, 1052.0])
    dates = ["d1", "d1", "d1", "d1", "d2"]                          # 아침: 1일 / 2일 = 0.5, 3끼 / 2일 = 1.5
    _, dropped = meal_peaks(values, density.tolist(), dates=dates, min_days_ratio=0.8, max_meals_per_day=1.4)
    assert dropped[0]["reason"] == ["days_ratio < 0.8", "meals_per_day > 1.4"]


def test_rare_meal_is_not_sent_and_its_hours_stay_empty():
    """저녁을 40일 중 12일만 먹으면 습관이 아니다 — 보내지 않고, 아침 구간을 늘리지도 않는다."""
    samples = daily_meals([(450, 8, 40, 1), (1110, 8, 12, 1)], days=40)
    every = build_model(samples, meal_min_days_ratio=None)["distributions"]["meal_time"]["peaks"]
    model = build_model(samples)
    kept = model["distributions"]["meal_time"]["peaks"]

    assert [round(p["days_ratio"], 2) for p in every] == [1.0, 0.3]
    assert kept == every[:1]                                   # 1개만 남음, 아침 구간 그대로
    dropped = model["meta"]["meal_time"]["meal_peaks_dropped"]
    assert [round(d["days_ratio"], 2) for d in dropped] == [0.3]
    validate_kde_payload(build_kde_payload(model))


def test_threshold_is_configurable_and_middle_hours_stay_empty():
    samples = daily_meals([(450, 10, 40, 1), (750, 10, 28, 1), (1110, 10, 40, 1)], days=40)
    every = build_model(samples, meal_min_days_ratio=None)["distributions"]["meal_time"]["peaks"]
    kept = build_model(samples, meal_min_days_ratio=0.8)["distributions"]["meal_time"]["peaks"]
    assert kept == [every[0], every[2]]
    assert kept[0]["to"] != kept[1]["from"]                     # 점심 시간대는 비어 있다


def test_filtered_to_empty():
    samples = daily_meals([(450, 15, 20, 1), (1110, 15, 20, 1)])
    model = build_model(samples, meal_min_days_ratio=1.01)
    assert model["distributions"]["meal_time"]["peaks"] == []
    validate_kde_payload(build_kde_payload(model))


def test_counts_ignore_weights():
    """가중치는 KDE 에만 쓰고 days_ratio / meals_per_day 는 실제 횟수로 센다."""
    samples = daily_meals([(450, 15, 20, 1), (750, 15, 14, 1), (1110, 15, 20, 1)])
    plain = build_model(samples)["distributions"]["meal_time"]["peaks"]
    weighted = build_model(samples, sample_weighting=SampleWeighting(recent_lambda=0.2))["distributions"]["meal_time"]["peaks"]
    assert [p["days_ratio"] for p in plain] == [p["days_ratio"] for p in weighted]


# ============================================================ 발행 검증


@pytest.fixture(scope="module")
def payload(three_meal_model):
    return build_kde_payload(three_meal_model)


def test_payload_with_peaks_is_valid(payload):
    validate_kde_payload(payload)
    assert "peaks" in payload["distributions"]["meal_time"]


def with_peaks(payload, peaks, name="meal_time"):
    p = copy.deepcopy(payload)
    p["distributions"][name]["peaks"] = peaks
    return p


GOOD = [
    {"center": 450, "from": 300, "to": 600, "predictability": 0.41, "days_ratio": 0.87, "meals_per_day": 0.87},
    {"center": 1080, "from": 600, "to": 300, "predictability": None,     # 자정 넘음, null
     "days_ratio": 0.76, "meals_per_day": 1.2},
]


def test_wrap_and_null_accepted(payload):
    validate_kde_payload(with_peaks(payload, GOOD))


def test_peaks_absent_is_fine(payload):
    p = copy.deepcopy(payload)
    del p["distributions"]["meal_time"]["peaks"]
    validate_kde_payload(p)


@pytest.mark.parametrize("peaks", [[], [GOOD[0]], [{**GOOD[0], "from": 900, "to": 600, "center": 1000}]])
def test_filtered_peaks_may_be_one_or_empty(payload, peaks):
    """자주 먹지 않는 끼니를 거른 결과 — 1개, 빈 배열, 하루를 다 덮지 않는 구간 모두 통과."""
    validate_kde_payload(with_peaks(payload, peaks))


@pytest.mark.parametrize("peaks, match", [
    ("x", "배열"),
    ({"center": 450}, "배열"),
    ([GOOD[0], {**GOOD[1], "extra": 1}], "days_ratio / meals_per_day만"),
    ([GOOD[0], {k: v for k, v in GOOD[1].items() if k != "center"}], "days_ratio / meals_per_day만"),
    ([GOOD[0], {k: v for k, v in GOOD[1].items() if k != "meals_per_day"}], "days_ratio / meals_per_day만"),
    ([{**GOOD[0], "days_ratio": 1.2}, GOOD[1]], "days_ratio는 0~1"),
    ([{**GOOD[0], "days_ratio": None}, GOOD[1]], "days_ratio는 0~1"),
    ([{**GOOD[0], "meals_per_day": -0.1}, GOOD[1]], "meals_per_day는 0 이상"),
    ([{**GOOD[0], "meals_per_day": "1"}, GOOD[1]], "meals_per_day는 0 이상"),
    ([{**GOOD[0], "from": -15}, GOOD[1]], "0 이상"),
    ([{**GOOD[0], "to": 1440}, GOOD[1]], "1440 미만"),
    ([{**GOOD[0], "center": "450"}, GOOD[1]], "center"),
    ([{**GOOD[0], "to": 300}, GOOD[1]], "같습니다"),
    ([{**GOOD[0], "center": 700}, GOOD[1]], "구간 밖"),
    ([GOOD[0], {**GOOD[1], "center": 450}], "구간 밖"),
    ([{**GOOD[0], "predictability": 1.2}, GOOD[1]], "null 또는 0~1"),
    ([{**GOOD[0], "predictability": "0.4"}, GOOD[1]], "null 또는 0~1"),
    ([GOOD[0], {**GOOD[0], "center": 500, "from": 450, "to": 700}], "겹칩니다"),
])
def test_invalid_peaks_rejected(payload, peaks, match):
    with pytest.raises(PayloadError, match=match):
        validate_kde_payload(with_peaks(payload, peaks))


def test_wrap_center_inside(payload):
    """자정을 넘는 구간의 center 는 [from, 1440) ∪ [0, to) 어디든 된다."""
    peaks = [GOOD[0], {**GOOD[1], "center": 60}]
    validate_kde_payload(with_peaks(payload, peaks))


def test_peaks_only_on_meal_time():
    samples = meals() + [KdeSample("wake_time", float(v), f"2026-09-{i + 1:02d}", "sensor")
                         for i, v in enumerate((400, 420, 440, 410))]
    p = build_kde_payload(build_model(samples))
    with pytest.raises(PayloadError, match="grid_min / grid_step / density만"):
        validate_kde_payload(with_peaks(p, GOOD, name="wake_time"))


# ============================================================ Context Engine 계약 / 실제 데이터


@pytest.mark.skipif(sys.version_info < (3, 11), reason="context-engine 은 Python 3.11+")
def test_context_engine_accepts_peaks(payload):
    sys.path.insert(0, str(CONTEXT_ENGINE))
    try:
        from hestia_engine.messages import parse
        from hestia_engine.model import ModelStore
    finally:
        sys.path.remove(str(CONTEXT_ENGINE))

    msg = parse("hestia/model/kde", to_json(payload).encode("utf-8"), recv_ts=1790296801.0)
    store = ModelStore()
    assert store.apply(msg) is True
    assert store.distribution("meal_time")["peaks"] == payload["distributions"]["meal_time"]["peaks"]


@pytest.mark.skipif(not (RAW_DIR / "cairo.txt").exists(), reason="CASAS 원본 없음")
def test_cairo_per_meal_predictability():
    from aruba import read_events
    events = [e for e in read_events(RAW_DIR / "cairo.txt", {"Breakfast", "Lunch", "Dinner"}).events
              if e.kind == "begin"]
    samples = [KdeSample("meal_time", e.ts.hour * 60 + e.ts.minute + e.ts.second / 60,
                         e.ts.date().isoformat(), "cairo") for e in events]
    model = build_model(samples, meal_min_days_ratio=None, meal_max_meals_per_day=None)
    peaks = model["distributions"]["meal_time"]["peaks"]
    assert len(peaks) == 3
    assert model["predictability"]["meal_time"] < 0.1                      # 전체는 다봉이라 낮다
    assert [round(p["predictability"], 2) for p in peaks] == [0.41, 0.54, 0.65]
    assert [round(p["days_ratio"], 2) for p in peaks] == [0.87, 0.67, 0.76]
    assert [round(p["meals_per_day"], 2) for p in peaks] == [0.87, 0.67, 0.76]

    # 기본 거르기 (days_ratio ≥ 0.5, meals_per_day ≤ 1.5) — 세 끼 모두 남는다
    kept = build_model(samples)["distributions"]["meal_time"]["peaks"]
    assert [round(p["days_ratio"], 2) for p in kept] == [0.87, 0.67, 0.76]
