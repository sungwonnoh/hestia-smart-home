from dataclasses import dataclass


TAIL_THRESHOLD = 0.05
PREDICTABILITY_THRESHOLD = 0.05


@dataclass
class MealPolicyResult:
    candidate: bool
    tail_probability: float
    predictability: float
    reason: str


def evaluate_meal_policy(
    tail_probability: float,
    predictability: float,
) -> MealPolicyResult:
    """
    KDE 기반 최소 Meal Policy.

    현재 단계에서는 아래 두 조건만 사용한다.

    1. 현재 시각이 meal_time 분포의 tail 5% 이내
    2. 해당 사용자의 meal_time 패턴이 충분히 규칙적

    향후 HOME/AWAY, SINGLE/MULTI, meal_done 등의
    Context 조건을 추가한다.
    """

    time_anomaly = (
        tail_probability
        < TAIL_THRESHOLD
    )

    pattern_reliable = (
        predictability
        >= PREDICTABILITY_THRESHOLD
    )

    if (
        time_anomaly
        and pattern_reliable
    ):
        return MealPolicyResult(
            candidate=True,
            tail_probability=tail_probability,
            predictability=predictability,
            reason="MEAL_TIME_ANOMALY",
        )

    if not time_anomaly:
        reason = "MEAL_TIME_NORMAL"

    elif not pattern_reliable:
        reason = "MEAL_PATTERN_UNRELIABLE"

    else:
        reason = "NO_CANDIDATE"

    return MealPolicyResult(
        candidate=False,
        tail_probability=tail_probability,
        predictability=predictability,
        reason=reason,
    )