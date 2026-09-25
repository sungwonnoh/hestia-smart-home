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


from clock import ReplayClock
from engine import ContextEngine


def build_user_a_payload():
    density = [0.0] * 96

    density[30] = 0.25
    density[31] = 0.35
    density[32] = 0.25
    density[33] = 0.10
    density[34] = 0.03
    density[35] = 0.01
    density[36] = 0.005
    density[37] = 0.005

    return {
        "version": 1,
        "sent_ts": 1790340000,
        "src_id": "rpi4",
        "trained_at": 1790340000,
        "sample_days": 21,
        "distributions": {
            "meal_time": {
                "grid_min": 0,
                "grid_step": 15,
                "density": density,
            }
        },
        "predictability": {
            "meal_time": 0.35,
        },
    }


def build_user_b_payload():
    density = [0.0] * 96

    for i in range(30, 39):
        density[i] = 0.05

    for i in range(39, 45):
        density[i] = 0.075

    density[45] = 0.05
    density[46] = 0.05

    return {
        "version": 1,
        "sent_ts": 1790340000,
        "src_id": "rpi4",
        "trained_at": 1790340000,
        "sample_days": 21,
        "distributions": {
            "meal_time": {
                "grid_min": 0,
                "grid_step": 15,
                "density": density,
            }
        },
        "predictability": {
            "meal_time": 0.25,
        },
    }


def make_engine():
    clock = ReplayClock(
        start=1790340000
    )

    return ContextEngine(
        clock=clock
    )


def test_user_a_is_candidate_at_0940():
    engine = make_engine()

    engine.ingest(
        "hestia/model/kde",
        build_user_a_payload(),
    )

    result = (
        engine.evaluate_meal_intervention(
            "09:40"
        )
    )

    assert result["available"] is True

    assert result["candidate"] is True

    assert (
        result["reason"]
        == "MEAL_TIME_ANOMALY"
    )

    assert (
        result["tail_probability"]
        < 0.05
    )


def test_user_b_is_not_candidate_at_0940():
    engine = make_engine()

    engine.ingest(
        "hestia/model/kde",
        build_user_b_payload(),
    )

    result = (
        engine.evaluate_meal_intervention(
            "09:40"
        )
    )

    assert result["available"] is True

    assert result["candidate"] is False

    assert (
        result["reason"]
        == "MEAL_TIME_NORMAL"
    )

    assert (
        result["tail_probability"]
        >= 0.05
    )


def test_same_time_different_kde_models():
    engine_a = make_engine()
    engine_b = make_engine()

    engine_a.ingest(
        "hestia/model/kde",
        build_user_a_payload(),
    )

    engine_b.ingest(
        "hestia/model/kde",
        build_user_b_payload(),
    )

    result_a = (
        engine_a.evaluate_meal_intervention(
            "09:40"
        )
    )

    result_b = (
        engine_b.evaluate_meal_intervention(
            "09:40"
        )
    )

    assert result_a["candidate"] is True
    assert result_b["candidate"] is False

    assert (
        result_a["tail_probability"]
        <
        result_b["tail_probability"]
    )