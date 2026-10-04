import pytest

from hestia_engine.clock import ReplayClock
from hestia_engine.config import load
from hestia_engine.context import AwayContext, OccupancyContext, SuppressionContext
from hestia_engine.model import ModelStore
from hestia_engine.policy import (
    ALREADY_DONE,
    ANOMALY,
    MULTI_OCCUPANT,
    NO_MODEL,
    NOT_HOME,
    PATTERN_UNRELIABLE,
    SUPPRESSED,
    TIME_NORMAL,
    InterventionPolicy,
)

from test_activity import HOME, POLICY, MORNING      # 09:40 KST
from test_model import msg, kde_payload, peaked


def ctx_away(state: str = "HOME") -> AwayContext:
    return AwayContext(name="away", since=0.0, state=state)


def ctx_occupancy(state: str = "SINGLE") -> OccupancyContext:
    return OccupancyContext(name="occupancy", since=0.0, state=state)


def ctx_suppression(active: bool = False, reason: str | None = None) -> SuppressionContext:
    return SuppressionContext(
        name="suppression", since=0.0,
        active=active, reason=reason,
        except_=("SAFETY",) if active else (),
    )


@pytest.fixture
def policy(tmp_path):
    h = tmp_path / "home.toml"
    p = tmp_path / "policy.toml"
    h.write_text(HOME, encoding="utf-8")
    p.write_text(POLICY, encoding="utf-8")
    cfg = load(h, p)
    clock = ReplayClock(MORNING)
    store = ModelStore()
    return InterventionPolicy(clock, cfg, store), store


def late_model(predictability: float = 0.33):
    """07:30 봉우리 — 09:40 은 꼬리다."""
    payload = kde_payload(meal_time=peaked(450))
    payload["predictability"] = {"meal_time": predictability}
    return msg(payload=payload)


def evaluate(policy, *, away="HOME", occupancy="SINGLE",
             suppressed=False, done=False):
    pol, _ = policy
    return pol.evaluate_meal(
        ctx_away(away), ctx_occupancy(occupancy),
        ctx_suppression(suppressed, "COOLDOWN" if suppressed else None),
        meal_done=done,
    )


# ============================================================ 게이팅 순서


def test_no_model(policy):
    """모델이 없으면 비교할 기준이 없다."""
    d = evaluate(policy)
    assert d.candidate is False
    assert d.reason == NO_MODEL


def test_not_home(policy):
    """외출 중이면 집 안 채널로 보내도 무의미하다."""
    _, store = policy
    store.apply(late_model())
    d = evaluate(policy, away="AWAY")
    assert d.reason == NOT_HOME


def test_sensor_fault_also_blocks(policy):
    _, store = policy
    store.apply(late_model())
    assert evaluate(policy, away="SENSOR_FAULT").reason == NOT_HOME


def test_multi_occupant(policy):
    """명세: MULTI 판정 시 개인화 알림 중단."""
    _, store = policy
    store.apply(late_model())
    assert evaluate(policy, occupancy="MULTI").reason == MULTI_OCCUPANT


def test_already_done(policy):
    _, store = policy
    store.apply(late_model())
    assert evaluate(policy, done=True).reason == ALREADY_DONE


def test_suppressed(policy):
    _, store = policy
    store.apply(late_model())
    d = evaluate(policy, suppressed=True)
    assert d.reason == SUPPRESSED
    assert d.factors["suppression_reason"] == "COOLDOWN"


def test_gating_order_model_first(policy):
    """모델이 없으면 다른 조건을 보기 전에 빠진다."""
    d = evaluate(policy, away="AWAY", done=True, suppressed=True)
    assert d.reason == NO_MODEL


# ============================================================ 분포 판정


def test_normal_time_no_intervention(tmp_path):
    """07:30 봉우리에서 07:30 조회 — 평소 시각이다.

    ReplayClock 은 역행을 막으므로 fixture 를 쓰지 않고 새로 만든다.
    """
    h = tmp_path / "home.toml"
    p = tmp_path / "policy.toml"
    h.write_text(HOME, encoding="utf-8")
    p.write_text(POLICY, encoding="utf-8")
    cfg = load(h, p)

    store = ModelStore()
    store.apply(late_model())
    pol = InterventionPolicy(ReplayClock(MORNING - 7800), cfg, store)   # 07:30

    d = pol.evaluate_meal(
        ctx_away(), ctx_occupancy(), ctx_suppression(), meal_done=False
    )
    assert d.candidate is False
    assert d.reason == TIME_NORMAL


def test_late_time_is_candidate(policy):
    """09:40 은 꼬리 — 개입 후보."""
    _, store = policy
    store.apply(late_model())
    d = evaluate(policy)
    assert d.candidate is True
    assert d.reason == ANOMALY
    assert d.tail_probability < 0.05


def test_unreliable_pattern_blocks(policy):
    """단순히 시간이 늦다는 이유만으로 개입하지 않는다.

    사용자의 패턴 자체가 충분히 규칙적인 경우에만 개입 후보다 (명세).
    """
    _, store = policy
    store.apply(late_model(predictability=0.01))
    d = evaluate(policy)
    assert d.candidate is False
    assert d.reason == PATTERN_UNRELIABLE


def test_missing_predictability_blocks(policy):
    _, store = policy
    payload = kde_payload(meal_time=peaked(450))
    payload["predictability"] = {}
    store.apply(msg(payload=payload))
    assert evaluate(policy).reason == PATTERN_UNRELIABLE


# ============================================================ 결과


def test_decision_carries_evidence(policy):
    """왜 안 보냈는지가 보낸 이유만큼 중요하다."""
    _, store = policy
    store.apply(late_model())
    d = evaluate(policy, away="AWAY")
    assert d.factors["away_state"] == "AWAY"
    assert d.factors["sample_days"] == 21


def test_confidence_rises_with_margin(policy):
    """임계를 겨우 넘은 것과 한참 넘은 것은 다르다."""
    _, store = policy
    store.apply(late_model(predictability=0.33))
    strong = evaluate(policy)

    store.apply(late_model(predictability=0.06))
    weak = evaluate(policy)

    assert strong.confidence > weak.confidence


def test_payload_shape(policy):
    _, store = policy
    store.apply(late_model())
    p = evaluate(policy).payload()
    assert set(p) == {
        "candidate", "reason", "kind",
        "tail_probability", "predictability", "confidence", "factors",
    }
    assert p["kind"] == "meal"


def test_ab_same_time_different_distribution(policy):
    """프로젝트의 차별점 — 같은 시각, 다른 분포, 갈리는 판단.

    명세: 같은 09:40 이벤트가 사용자 A 의 분포에서는 개입으로,
    B 의 분포에서는 무개입으로 갈린다.
    """
    pol, store = policy

    store.apply(late_model())                     # A — 07:30 에 먹는 사람
    a = evaluate(policy)

    payload = kde_payload(meal_time=peaked(580))  # B — 09:40 에 먹는 사람
    payload["predictability"] = {"meal_time": 0.33}
    store.apply(msg(payload=payload))
    b = evaluate(policy)

    assert a.candidate is True
    assert b.candidate is False
    assert b.reason == TIME_NORMAL