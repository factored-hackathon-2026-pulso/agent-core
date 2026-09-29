import pytest

from agent_core.decision.calibration.metrics import (
    Scored,
    coverage,
    ece,
    macro_f1,
    percentile,
    precision_at_threshold,
    recall_at_threshold,
)


def test_ece_perfectly_calibrated_is_zero() -> None:
    pairs = [(0.9, True)] * 9 + [(0.9, False)]  # confianza 0.9, acierto 0.9
    assert ece(pairs) == pytest.approx(0.0)


def test_ece_always_confident_and_always_wrong_is_the_confidence() -> None:
    assert ece([(0.95, False)] * 10) == pytest.approx(0.95)


def test_ece_uses_ten_equal_width_bins_weighted_by_size() -> None:
    # bin [0.1, 0.2): 4 ejemplos, conf 0.15, acierto 0.5 -> 0.35; bin [0.9, 1.0]: 6 ejemplos, conf 1.0 (cae en
    # el último bin), acierto 1.0 -> 0. ECE = 0.4 * 0.35.
    pairs = [(0.15, True), (0.15, True), (0.15, False), (0.15, False)] + [(1.0, True)] * 6
    assert ece(pairs) == pytest.approx(0.4 * 0.35)


def test_ece_of_nothing_is_undefined() -> None:
    assert ece([]) is None


def test_macro_f1_on_a_known_confusion_matrix() -> None:
    # a: tp 1, fp 0, fn 1 -> f1 2/3; b: tp 2, fp 1, fn 0 -> f1 0.8
    assert macro_f1(["a", "a", "b", "b"], ["a", "b", "b", "b"]) == pytest.approx((2 / 3 + 0.8) / 2)


def test_macro_f1_counts_missing_predictions_as_misses() -> None:
    assert macro_f1(["a", "a"], ["a", None]) == pytest.approx(2 / 3)
    assert macro_f1([], []) is None


def _scored() -> list[Scored]:
    return [
        Scored("a", "a", 0.9, True), Scored("a", "a", 0.8, True), Scored("a", "b", 0.7, True),
        Scored("b", "b", 0.4, False), Scored("a", None, None, False),
    ]


def test_precision_at_threshold_and_coverage() -> None:
    scored = _scored()
    assert precision_at_threshold(scored) == pytest.approx(2 / 3)  # 3 aceptados, 2 aciertos
    assert coverage(scored) == pytest.approx(3 / 5)


def test_precision_and_coverage_undefined_without_data() -> None:
    assert precision_at_threshold([Scored("a", "a", 0.1, False)]) is None
    assert coverage([]) is None


def test_recall_at_threshold_is_macro_over_values() -> None:
    scored = [Scored("x", "x", 0.9, True), Scored("x", "x", 0.2, False),  # x: 1/2
              Scored("y", "y", 0.9, True), Scored("y", None, None, False)]  # y: 1/2
    assert recall_at_threshold(scored) == pytest.approx(0.5)
    both = [Scored("x", "x", 0.9, True), Scored("y", "y", 0.9, True)]
    assert recall_at_threshold(both) == pytest.approx(1.0)
    assert recall_at_threshold([]) is None


def test_percentile_is_nearest_rank() -> None:
    values = [40, 10, 30, 20]
    assert percentile(values, 50) == 20 and percentile(values, 95) == 40 and percentile(values, 100) == 40
    assert percentile([7], 50) == 7 and percentile([], 50) is None
