"""Elección de umbral por objetivo (spec §3.4.3; regla de recall fijada en P5)."""

from agent_core.decision.calibration.artifact import Target
from agent_core.decision.calibration.thresholds import Prediction, choose_threshold


def _p(id: str, truth: str, predicted: str | None, p_cal: float | None) -> Prediction:
    return Prediction(id=id, truth=truth, predicted=predicted, p_cal=p_cal)


PRECISION = Target(metric="precision", value=0.9)
RECALL = Target(metric="recall", value=0.75)


def _affirm_preds() -> list[Prediction]:
    """Precisión >= 0.9 solo desde p >= 0.7: 10 con p >= 0.7 (9 aciertos) y ruido por debajo."""
    hits = [_p(f"h{i}", "affirm", "affirm", 0.7 + 0.03 * i) for i in range(9)]
    miss_high = [_p("m-high", "deny", "affirm", 0.72)]
    noise = [_p(f"n{i}", "deny", "affirm", 0.3 + 0.05 * i) for i in range(5)]
    return hits + miss_high + noise


def test_precision_picks_the_minimum_threshold_meeting_the_target() -> None:
    assert choose_threshold(_affirm_preds(), "affirm", PRECISION, min_support=3) == 0.7


def test_precision_unreachable_returns_none() -> None:
    preds = [_p(f"x{i}", "deny", "affirm", 0.5 + 0.05 * i) for i in range(5)]
    assert choose_threshold(preds, "affirm", PRECISION, min_support=3) is None


def test_precision_without_enough_support_returns_none() -> None:
    preds = [_p("a", "affirm", "affirm", 0.9), _p("b", "affirm", "affirm", 0.8)]
    assert choose_threshold(preds, "affirm", PRECISION, min_support=3) is None


def test_precision_ignores_other_predicted_values_and_missing_predictions() -> None:
    preds = [*_affirm_preds(), _p("d", "deny", "deny", 0.99), _p("z", "affirm", None, None)]
    assert choose_threshold(preds, "affirm", PRECISION, min_support=3) == 0.7


def test_recall_picks_the_maximum_threshold_meeting_the_target() -> None:
    # 8 casos reales de `cancelar`; recall 0.75 = 6 recuperados => el umbral es el 6.º p_cal más alto.
    truth = [_p(f"c{i}", "cancelar", "cancelar", 0.95 - 0.1 * i) for i in range(6)]
    missed = [_p("c6", "cancelar", "otro", 0.9), _p("c7", "cancelar", None, None)]
    threshold = choose_threshold(truth + missed, "cancelar", RECALL, min_support=3)
    assert threshold is not None and abs(threshold - 0.45) < 1e-9  # 0.95, 0.85, ..., 0.45
    recovered = [p for p in truth if p.p_cal is not None and p.p_cal >= threshold]
    assert len(recovered) == 6


def test_recall_unreachable_returns_none() -> None:
    preds = [_p("a", "cancelar", "cancelar", 0.9)] + [_p(f"m{i}", "cancelar", None, None) for i in range(5)]
    assert choose_threshold(preds, "cancelar", RECALL, min_support=3) is None


def test_recall_without_enough_support_returns_none() -> None:
    preds = [_p("a", "cancelar", "cancelar", 0.9), _p("b", "cancelar", "cancelar", 0.8)]
    assert choose_threshold(preds, "cancelar", Target(metric="recall", value=0.5), min_support=3) is None


def test_result_does_not_depend_on_input_order() -> None:
    preds = _affirm_preds()
    assert choose_threshold(list(reversed(preds)), "affirm", PRECISION, 3) == choose_threshold(
        preds, "affirm", PRECISION, 3)
