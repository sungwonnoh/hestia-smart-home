"""v2 Phase 7 — predictability 검증 실험이 기대한 성질을 보이는지."""

from pathlib import Path

import validate_predictability as vp


BREAKFAST = Path(__file__).resolve().parents[3] / "data" / "processed" / "aruba" / "breakfast_preparation.csv"


def test_predictability_decreases_with_std():
    rows = vp.time_of_day_sweep(seeds=3)
    assert [r.target_std for r in rows] == list(vp.SWEEP_STDS)
    preds = [r.predictability for r in rows]
    assert preds == sorted(preds, reverse=True)
    assert all(0 <= p <= 1 for p in preds)


def test_actual_std_tracks_target():
    for r in vp.time_of_day_sweep(seeds=3):
        assert abs(r.actual_std - r.target_std) < 0.1 * r.target_std + 2


def test_midnight_center_matches_daytime():
    """circular KDE — 07:30 중심과 00:00 중심 결과가 같다."""
    for _, day, night in vp.midnight_check(seeds=3):
        assert abs(day - night) < 1e-9


def test_more_samples_do_not_lower_predictability_much():
    rows = {(r.target_std, r.n): r.predictability for r in vp.sample_size_sweep(seeds=5)}
    for std in vp.SAMPLE_SIZE_STDS:
        assert rows[(std, 212)] >= rows[(std, 14)] - 0.01


def test_hydration_regular_beats_irregular():
    regular, irregular = vp.hydration_rows(seeds=3)
    assert regular.predictability > irregular.predictability
    assert regular.actual_std < irregular.actual_std


def test_aruba_breakfast_matches_model():
    """기존 모델 값(0.330)과 같은 함수로 계산한다."""
    rows, note = vp.aruba_rows(raw_path=None, breakfast_path=BREAKFAST)
    assert round(rows[0].predictability, 3) == 0.330
    assert rows[0].n == 212
    assert note is not None


def test_render_without_raw():
    text = vp.render(seeds=2, raw_path=None, breakfast_path=BREAKFAST)
    for heading in ("## 1.", "## 2.", "## 3.", "## 4."):
        assert heading in text
    assert "Aruba 원본 없음" in text


def test_casas_rows_without_raw(tmp_path):
    rows, note = vp.casas_rows(tmp_path)
    assert rows == [] and "없음" in note
    assert vp.casas_rows(None)[0] == []
