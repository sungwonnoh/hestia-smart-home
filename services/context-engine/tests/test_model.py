import pytest

from hestia_engine.messages import ModelMessage
from hestia_engine.model import (
    DISTRIBUTIONS,
    ModelError,
    ModelStore,
    grid_slot,
    percentile,
    predictability,
    query,
    tail_probability,
    validate_distribution,
    validate_kde,
)


def uniform(step: int = 15) -> dict:
    n = 1440 // step
    return {"grid_min": 0, "grid_step": step, "density": [1.0 / n] * n}


def peaked(center_min: int, step: int = 15, width: int = 2) -> dict:
    """center 주변에만 질량이 있는 분포."""
    n = 1440 // step
    density = [0.0] * n
    center = center_min // step
    for i in range(max(0, center - width), min(n, center + width + 1)):
        density[i] = 1.0
    total = sum(density)
    return {"grid_min": 0, "grid_step": step, "density": [d / total for d in density]}


def kde_payload(**dists) -> dict:
    return {
        "version": 1,
        "sent_ts": 1790296800,
        "src_id": "rpi4",
        "trained_at": 1790280000,
        "sample_days": 21,
        "distributions": dists or {"meal_time": uniform()},
        "predictability": {name: 0.33 for name in (dists or {"meal_time": None})},
    }


def msg(name: str = "kde", payload: dict | None = None) -> ModelMessage:
    return ModelMessage(
        recv_ts=1790296800.0,
        src_id="rpi4",
        sent_ts=1790296798.0,
        name=name,
        trained_at=1790280000.0,
        payload=payload if payload is not None else kde_payload(),
    )


# ============================================================ 검증


def test_accepts_valid_payload():
    validate_kde(kde_payload())


def test_grid_length_must_match_step():
    """길이가 어긋나면 꼬리확률이 조용히 틀린다."""
    bad = {"grid_min": 0, "grid_step": 15, "density": [0.01] * 50}
    with pytest.raises(ModelError, match="길이 불일치"):
        validate_distribution("meal_time", bad)


def test_grid_check_generalizes_beyond_meal_time():
    """legacy 는 meal_time 에만 96칸을 하드코딩했다."""
    five_min = {"grid_min": 0, "grid_step": 5, "density": [1 / 288] * 288}
    validate_distribution("wake_time", five_min)

    with pytest.raises(ModelError):
        validate_distribution("wake_time", {**five_min, "density": [1 / 96] * 96})


def test_relative_distribution_skips_day_length():
    """hydration_lag 는 기준점이 자정이 아니다 — 기상 시점 기준."""
    lag = {"grid_min": 0, "grid_step": 5, "density": [0.1] * 10}
    validate_distribution("hydration_lag", lag)


def test_missing_field_rejected():
    with pytest.raises(ModelError, match="grid_step"):
        validate_distribution("meal_time", {"grid_min": 0, "density": [1.0]})


def test_empty_density_rejected():
    with pytest.raises(ModelError):
        validate_distribution("meal_time", {"grid_min": 0, "grid_step": 15, "density": []})


def test_negative_density_rejected():
    d = uniform()
    d["density"][0] = -0.1
    with pytest.raises(ModelError, match="음수"):
        validate_distribution("meal_time", d)


def test_zero_sum_rejected():
    d = {"grid_min": 0, "grid_step": 15, "density": [0.0] * 96}
    with pytest.raises(ModelError, match="합이 0"):
        validate_distribution("meal_time", d)


def test_empty_distributions_rejected():
    with pytest.raises(ModelError):
        validate_kde({"distributions": {}})


# ============================================================ 보관


def test_store_starts_empty():
    s = ModelStore()
    assert s.has("kde") is False
    assert s.get("kde") is None
    assert s.distribution("meal_time") is None


def test_apply_and_get():
    s = ModelStore()
    assert s.apply(msg()) is True
    assert s.has("kde") is True
    assert s.trained_at("kde") == 1790280000.0
    assert s.sample_days() == 21


def test_broken_model_does_not_replace():
    """깨진 모델로 교체하는 것보다 직전 모델을 유지하는 편이 낫다.

    명세: RPi4 가 죽어도 RPi5 는 마지막 모델로 계속 판단한다.
    """
    s = ModelStore()
    s.apply(msg())
    good = s.get("kde")

    bad = kde_payload(meal_time={"grid_min": 0, "grid_step": 15, "density": [1.0] * 10})
    assert s.apply(msg(payload=bad)) is False
    assert s.get("kde") is good


def test_unknown_model_name_rejected():
    s = ModelStore()
    assert s.apply(msg(name="whatever")) is False


def test_models_are_separate():
    """갱신 주기가 달라 이름을 나눈다 (명세)."""
    s = ModelStore()
    s.apply(msg("kde"))
    s.apply(msg("hmm", payload={"states": [], "transition": []}))
    assert s.has("kde") and s.has("hmm")
    assert s.has("classifier") is False


def test_distribution_lookup():
    s = ModelStore()
    s.apply(msg())
    assert s.distribution("meal_time") is not None
    assert s.distribution("sleep_time") is None


# ============================================================ 조회


def test_grid_slot():
    d = uniform()
    assert grid_slot(d, 0) == 0
    assert grid_slot(d, 580) == 38          # 09:40
    assert grid_slot(d, 585) == 39          # 09:45 — 경계는 내림


def test_grid_slot_clamps():
    d = uniform()
    assert grid_slot(d, -10) == 0
    assert grid_slot(d, 2000) == 95


def test_tail_of_uniform():
    d = uniform()
    assert tail_probability(d, 0) == pytest.approx(1.0)
    assert tail_probability(d, 720) == pytest.approx(0.5, abs=0.02)


def test_tail_after_peak_is_small():
    """07:30 봉우리에서 09:40 을 조회하면 거의 0 이다."""
    d = peaked(450)                          # 07:30
    assert tail_probability(d, 580) < 0.01   # 09:40


def test_tail_before_peak_is_large():
    d = peaked(450)
    assert tail_probability(d, 300) > 0.99   # 05:00


def test_percentile_is_complement():
    d = peaked(450)
    for m in (300, 450, 580):
        assert percentile(d, m) == pytest.approx(1.0 - tail_probability(d, m))


def test_window_limits_integration():
    """다봉 분포에서 뒤쪽 봉우리가 섞이지 않게 한다.

    meal_time 에 아침·점심·저녁이 있으면 점심 조회 시 저녁 질량이
    통째로 들어와 '늦었다' 가 안 잡힌다.
    """
    density = [0.0] * 96
    for i in range(28, 33):                  # 07:00~08:15
        density[i] = 0.1
    for i in range(48, 53):                  # 12:00~13:15
        density[i] = 0.1
    d = {"grid_min": 0, "grid_step": 15, "density": density}

    whole = tail_probability(d, 480)         # 08:00 — 점심이 섞인다
    windowed = tail_probability(d, 480, window_min=120)
    assert whole > windowed
    assert windowed < 0.3


def test_wrap_not_implemented():
    """조용히 틀린 값을 주는 것보다 터지는 편이 낫다."""
    with pytest.raises(NotImplementedError):
        tail_probability(uniform(), 500, wrap=True)


def test_predictability_read():
    s = ModelStore()
    s.apply(msg())
    assert predictability(s, "meal_time") == pytest.approx(0.33)
    assert predictability(s, "sleep_time") is None


def test_predictability_without_model():
    assert predictability(ModelStore(), "meal_time") is None


def test_query_bundles():
    s = ModelStore()
    s.apply(msg())
    r = query(s, "meal_time", 580)
    assert set(r) == {
        "tail_probability", "percentile", "predictability",
        "sample_days", "trained_at",
    }
    assert r["percentile"] == pytest.approx(1.0 - r["tail_probability"])


def test_query_returns_none_without_model():
    """'모른다' 와 '꼬리확률이 0 이다' 는 다르다."""
    assert query(ModelStore(), "meal_time", 580) is None


def test_distribution_names():
    assert set(DISTRIBUTIONS) == {
        "wake_time", "sleep_time", "meal_time", "hydration_lag"
    }