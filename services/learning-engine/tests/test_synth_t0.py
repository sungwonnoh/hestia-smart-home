"""합성 t0 로그 — 실기기 로그와 같은 모양인지, 학습·수면 분석이 그대로 읽는지."""

import argparse
from datetime import date, datetime

import numpy as np
import pytest

import synth_t0 as st
from baseline import add_input_arguments, build_model, samples_from_args
from model_payload import build_kde_payload, validate_kde_payload
from sleep_sessions import KST


P1 = st.PERSONAS["p1"]
START = date(2026, 8, 17)
DAYS = 56

T0_FIELDS = {"date", "type", "t0", "source", "prompted", "duration_sec", "area", "method", "confidence", "awake_areas", "eat_t0"}


@pytest.fixture(scope="module")
def records():
    return st.generate(P1, START, DAYS, seed=0)


@pytest.fixture(scope="module")
def log_path(records, tmp_path_factory):
    path = tmp_path_factory.mktemp("synth") / "t0_log.jsonl"
    st.write_jsonl(records, path)
    return path


def local(ts):
    return datetime.fromtimestamp(ts, KST)


def test_same_seed_same_log():
    assert st.generate(P1, START, 7, seed=3) == st.generate(P1, START, 7, seed=3)
    assert st.generate(P1, START, 7, seed=3) != st.generate(P1, START, 7, seed=4)


def test_record_shape_matches_t0_logger(records):
    """FileT0Log(asdict) + runner 봉투. 안 쓰는 필드도 null 로 있다."""
    for r in records:
        assert set(r) == T0_FIELDS | {"version", "sent_ts", "src_id"}
        assert r["date"] == local(r["t0"]).strftime("%Y-%m-%d")
        assert r["sent_ts"] >= int(r["t0"])
    assert {r["type"] for r in records} == {"meal", "hydration", "sleep_start", "sleep_end"}


def test_lines_are_in_arrival_order(records):
    sent = [r["sent_ts"] for r in records]
    assert sent == sorted(sent)


def test_sleep_events_alternate(records):
    sleep = sorted((r for r in records if r["type"].startswith("sleep_")), key=lambda r: r["t0"])
    assert [r["type"] for r in sleep] == ["sleep_start", "sleep_end"] * (len(sleep) // 2)


def test_awake_areas(records):
    starts = [r for r in records if r["type"] == "sleep_start"]
    for r in starts:
        if r["awake_areas"] is not None:
            assert r["area"] not in r["awake_areas"]

    # 밤 첫 잠 = 직전 sleep_end 에서 2시간 넘게 지난 침실 sleep_start (밤중 화장실 뒤 다시 잠든 것 제외)
    sleep = sorted((r for r in records if r["type"].startswith("sleep_")), key=lambda r: r["t0"])
    nights = [
        r for prev, r in zip([None] + sleep, sleep)
        if r["type"] == "sleep_start" and r["area"] == "bedroom"
        and (prev is None or r["t0"] - prev["t0"] > 2 * 3600)
    ]
    assert len(nights) == DAYS
    restarted = [r for r in nights if r["awake_areas"] is None]
    assert len(restarted) == 1 + len(P1.restart_nights)     # 첫날 밤은 엔진이 막 켜져 sleep_end 를 못 봤다
    assert all("kitchen" in r["awake_areas"] for r in nights if r["awake_areas"] is not None)

    resumed = [r["awake_areas"] for r in starts if 0 <= local(r["t0"]).hour < 5]
    assert ["bathroom"] in resumed and [] in resumed      # 화장실 / 누운 채 깸


def test_cook_only_meal_has_null_eat_t0():
    records = st.generate(P1, START, 200, seed=0)
    meals = [r for r in records if r["type"] == "meal"]
    assert any(r["eat_t0"] is None for r in meals)
    assert all(r["eat_t0"] >= r["t0"] for r in meals if r["eat_t0"] is not None)


def test_nocturia_trend(records):
    """밤중 화장실 = 화장실만 다녀와 다시 잠든 sleep_start. 그 밤(정오 기준)으로 센다."""
    trips = np.zeros(DAYS)
    for r in records:
        if r["type"] == "sleep_start" and r["awake_areas"] == ["bathroom"]:
            night = (local(r["t0"]).replace(tzinfo=None) - datetime(2026, 8, 17, 12)).days
            trips[night] += 1
    change_night, _ = P1.nocturia[-1]
    assert 0.7 <= trips[:change_night].mean() <= 1.3
    assert trips[change_night:].mean() > trips[:change_night].mean() + 0.4


def test_kde_learns_persona(log_path):
    parser = argparse.ArgumentParser()
    add_input_arguments(parser)
    model = build_model(samples_from_args(parser.parse_args(["--t0-jsonl", str(log_path)])))
    payload = build_kde_payload(model)
    validate_kde_payload(payload, require_all=True)

    def peak(name):
        d = payload["distributions"][name]
        return d["grid_min"] + d["grid_step"] * int(np.argmax(d["density"]))

    assert abs(peak("sleep_time") - P1.bed_min) <= 45
    assert abs(peak("wake_time") - P1.wake_min) <= 45
    assert len(payload["distributions"]["meal_time"]["peaks"]) == len(P1.meals)
