"""Elección del umbral por `(campo, valor, proveedor, idioma)` según el objetivo (spec §3.4.3, P5).

- `precision`: el **mínimo** umbral con precisión >= objetivo (`p_cal >= umbral`, igual que en runtime).
- `recall`: el **máximo** umbral con recall >= objetivo. Con recall, subir el umbral solo lo baja, así que el
  "mínimo que cumple" sería trivial: el criterio útil es el más alto que aún alcanza el objetivo.

Una combinación sin soporte (`< min_support`) o sin umbral que cumpla el objetivo devuelve `None`: se omite de
la tabla y en runtime queda en 1.0 (nunca pasa)."""

from collections.abc import Sequence
from dataclasses import dataclass

from agent_core.decision.calibration.artifact import Target


@dataclass(frozen=True)
class Prediction:
    """Resultado de un ejemplo para un campo: verdad, valor predicho (`None` si falló) y `p_cal`."""
    id: str
    truth: str
    predicted: str | None
    p_cal: float | None


def choose_threshold(preds: Sequence[Prediction], value: str, target: Target,
                     min_support: int) -> float | None:
    if target.metric == "precision":
        return _precision(preds, value, target.value, min_support)
    return _recall(preds, value, target.value, min_support)


def _precision(preds: Sequence[Prediction], value: str, goal: float, min_support: int) -> float | None:
    selected = [p for p in preds if p.predicted == value and p.p_cal is not None]
    if len(selected) < max(min_support, 1):
        return None
    for threshold in sorted({p.p_cal for p in selected if p.p_cal is not None}):
        above = [p for p in selected if p.p_cal is not None and p.p_cal >= threshold]
        if sum(p.truth == value for p in above) / len(above) >= goal:
            return threshold
    return None


def _recall(preds: Sequence[Prediction], value: str, goal: float, min_support: int) -> float | None:
    positives = [p for p in preds if p.truth == value]
    if len(positives) < max(min_support, 1):
        return None
    hits = [p.p_cal for p in positives if p.predicted == value and p.p_cal is not None]
    for threshold in sorted(set(hits), reverse=True):
        if sum(p >= threshold for p in hits) / len(positives) >= goal:
            return threshold
    return None
