import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

sys.path.append(
    str(
        ROOT
        / "services"
        / "context-engine"
    )
)

from kde_model import (
    parse_kde_payload,
    calculate_tail_probability,
    get_predictability,
)


def build_fake_payload():
    density = [0.0] * 96

    # 07:30 이후 일부 분포가 있다고 가정
    density[30] = 0.20
    density[31] = 0.30
    density[32] = 0.25
    density[33] = 0.15
    density[40] = 0.10

    return {
        "version": 1,
        "sent_ts": 1790341434,
        "src_id": "rpi4",
        "trained_at": 1790341434,
        "sample_days": 212,
        "distributions": {
            "meal_time": {
                "grid_min": 0,
                "grid_step": 15,
                "density": density,
            }
        },
        "predictability": {
            "meal_time": 0.33
        },
    }


def test_parse_kde_payload():
    payload = build_fake_payload()

    model = parse_kde_payload(
        payload
    )

    assert model.sample_days == 212
    assert model.src_id == "rpi4"
    assert len(
        model.distributions[
            "meal_time"
        ].density
    ) == 96


def test_tail_probability():
    payload = build_fake_payload()

    model = parse_kde_payload(
        payload
    )

    tail_0730 = (
        calculate_tail_probability(
            model,
            "meal_time",
            "07:30",
        )
    )

    tail_0940 = (
        calculate_tail_probability(
            model,
            "meal_time",
            "09:40",
        )
    )

    assert (
        tail_0730
        >= tail_0940
    )


def test_predictability():
    payload = build_fake_payload()

    model = parse_kde_payload(
        payload
    )

    value = get_predictability(
        model,
        "meal_time",
    )

    assert value == 0.33