"""meal_time peaks — 끼니 구간과 끼니별 predictability (Context Engine 합의)."""

import copy
import sys
from pathlib import Path

import numpy as np
import pytest

from baseline import build_model, calculate_predictability, fit_distribution, meal_peaks
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
    assert set(peaks[0]) == {"center", "from", "to", "predictability"}


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
    peaks = meal_peaks(values, density.tolist())
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
    {"center": 450, "from": 300, "to": 600, "predictability": 0.41},
    {"center": 1080, "from": 600, "to": 300, "predictability": None},    # 자정 넘음, null
]


def test_wrap_and_null_accepted(payload):
    validate_kde_payload(with_peaks(payload, GOOD))


def test_peaks_absent_is_fine(payload):
    p = copy.deepcopy(payload)
    del p["distributions"]["meal_time"]["peaks"]
    validate_kde_payload(p)


@pytest.mark.parametrize("peaks, match", [
    ([GOOD[0]], "2개 이상"),
    ("x", "2개 이상"),
    ([GOOD[0], {**GOOD[1], "extra": 1}], "center / from / to / predictability"),
    ([GOOD[0], {k: v for k, v in GOOD[1].items() if k != "center"}], "center / from / to / predictability"),
    ([{**GOOD[0], "from": -15}, GOOD[1]], "0 이상"),
    ([{**GOOD[0], "to": 1440}, GOOD[1]], "1440 미만"),
    ([{**GOOD[0], "center": "450"}, GOOD[1]], "center"),
    ([{**GOOD[0], "to": 300}, GOOD[1]], "같습니다"),
    ([{**GOOD[0], "center": 700}, GOOD[1]], "구간 밖"),
    ([GOOD[0], {**GOOD[1], "center": 450}], "구간 밖"),
    ([{**GOOD[0], "predictability": 1.2}, GOOD[1]], "null 또는 0~1"),
    ([{**GOOD[0], "predictability": "0.4"}, GOOD[1]], "null 또는 0~1"),
    ([GOOD[0], {"center": 500, "from": 450, "to": 700, "predictability": 0.3}], "겹칩니다"),
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
    model = build_model(samples)
    peaks = model["distributions"]["meal_time"]["peaks"]
    assert len(peaks) == 3
    assert model["predictability"]["meal_time"] < 0.1                      # 전체는 다봉이라 낮다
    assert [round(p["predictability"], 2) for p in peaks] == [0.41, 0.54, 0.65]
