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


from model_ingest import (
    ingest_model_message,
)

from model_store import (
    ModelStore,
)

from kde_context import (
    get_meal_time_context,
)


def build_fake_payload():
    density = [0.0] * 96

    # 07:30 이후 분포
    density[30] = 0.20
    density[31] = 0.30
    density[32] = 0.25
    density[33] = 0.15

    # 10:00 근처 일부 tail
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
            "meal_time": 0.33,
        },
    }


def test_kde_model_ingest():
    store = ModelStore()

    payload = build_fake_payload()

    handled = ingest_model_message(
        "hestia/model/kde",
        payload,
        store,
    )

    assert handled is True

    assert store.has_kde()

    model = store.get_kde()

    assert model is not None
    assert model.sample_days == 212


def test_unknown_topic():
    store = ModelStore()

    handled = ingest_model_message(
        "hestia/model/hmm",
        {},
        store,
    )

    assert handled is False
    assert store.has_kde() is False


def test_kde_context():
    store = ModelStore()

    ingest_model_message(
        "hestia/model/kde",
        build_fake_payload(),
        store,
    )

    context_0730 = (
        get_meal_time_context(
            store,
            "07:30",
        )
    )

    context_0940 = (
        get_meal_time_context(
            store,
            "09:40",
        )
    )

    assert (
        context_0730["available"]
        is True
    )

    assert (
        context_0730[
            "predictability"
        ]
        == 0.33
    )

    assert (
        context_0730[
            "tail_probability"
        ]
        >=
        context_0940[
            "tail_probability"
        ]
    )


def test_context_without_model():
    store = ModelStore()

    context = (
        get_meal_time_context(
            store,
            "09:40",
        )
    )

    assert (
        context["available"]
        is False
    )

    assert (
        context["tail_probability"]
        is None
    )