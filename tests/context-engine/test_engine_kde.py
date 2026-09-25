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


from clock import Clock
from engine import ContextEngine


class FakeClock(Clock):
    def now(self):
        return 1000.0


def build_fake_kde_payload():
    density = [0.0] * 96

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
            "meal_time": 0.33,
        },
    }


def test_engine_stores_kde_model():
    engine = ContextEngine(
        clock=FakeClock()
    )

    handled = engine.handle_model(
        "hestia/model/kde",
        build_fake_kde_payload(),
    )

    assert handled is True

    model = (
        engine.model_store.get_kde()
    )

    assert model is not None
    assert model.sample_days == 212
    assert model.src_id == "rpi4"


def test_engine_gets_meal_context():
    engine = ContextEngine(
        clock=FakeClock()
    )

    engine.handle_model(
        "hestia/model/kde",
        build_fake_kde_payload(),
    )

    context_0730 = (
        engine.get_meal_kde_context(
            "07:30"
        )
    )

    context_0940 = (
        engine.get_meal_kde_context(
            "09:40"
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


def test_engine_without_kde():
    engine = ContextEngine(
        clock=FakeClock()
    )

    context = (
        engine.get_meal_kde_context(
            "09:40"
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
    
def test_engine_ingest_routes_kde():
    engine = ContextEngine(
        clock=FakeClock()
    )

    engine.ingest(
        "hestia/model/kde",
        build_fake_kde_payload(),
    )

    assert (
        engine.model_store.has_kde()
        is True
    )

    model = (
        engine.model_store.get_kde()
    )

    assert model is not None
    assert model.sample_days == 212


def test_engine_ingest_accepts_sensor():
    engine = ContextEngine(
        clock=FakeClock()
    )

    engine.ingest(
        "hestia/sensor/vs-03/state",
        {
            "version": 1,
            "type": "motion",
            "motion": True,
        },
    )