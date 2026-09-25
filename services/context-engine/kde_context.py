from kde_model import (
    calculate_tail_probability,
    get_predictability,
)

from model_store import ModelStore


def get_meal_time_context(
    store: ModelStore,
    query_time: str,
) -> dict:
    """
    현재 시각에 대한 meal_time KDE 정보를 반환한다.
    """

    model = store.get_kde()

    if model is None:
        return {
            "available": False,
            "tail_probability": None,
            "predictability": None,
        }

    tail_probability = (
        calculate_tail_probability(
            model,
            "meal_time",
            query_time,
        )
    )

    predictability = (
        get_predictability(
            model,
            "meal_time",
        )
    )

    return {
        "available": True,
        "tail_probability": (
            tail_probability
        ),
        "predictability": (
            predictability
        ),
    }