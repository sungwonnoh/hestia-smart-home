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


from meal_policy import (
    evaluate_meal_policy,
)


def test_meal_policy_intervenes():
    result = evaluate_meal_policy(
        tail_probability=0.03,
        predictability=0.30,
    )

    assert result.candidate is True
    assert (
        result.reason
        == "MEAL_TIME_ANOMALY"
    )


def test_meal_policy_normal_time():
    result = evaluate_meal_policy(
        tail_probability=0.40,
        predictability=0.30,
    )

    assert result.candidate is False
    assert (
        result.reason
        == "MEAL_TIME_NORMAL"
    )


def test_meal_policy_unreliable_pattern():
    result = evaluate_meal_policy(
        tail_probability=0.03,
        predictability=0.02,
    )

    assert result.candidate is False
    assert (
        result.reason
        == "MEAL_PATTERN_UNRELIABLE"
    )