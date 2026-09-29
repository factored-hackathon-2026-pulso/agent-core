"""Métricas de calibración y de runtime (spec §3.4.5 y §8). Python puro, deterministas."""

import math
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass

_BINS = 10


@dataclass(frozen=True)
class Scored:
    """Un ejemplo evaluado: verdad, predicción (`None` si falló), `p_cal` y si superó su umbral."""
    truth: str
    predicted: str | None
    p_cal: float | None
    accepted: bool


def ece(pairs: Sequence[tuple[float, bool]]) -> float | None:
    """ECE con 10 bins de ancho igual: promedio ponderado de `|acierto - confianza|` por bin."""
    if not pairs:
        return None
    bins: dict[int, list[tuple[float, bool]]] = defaultdict(list)
    for confidence, correct in pairs:
        bins[min(int(confidence * _BINS), _BINS - 1)].append((confidence, correct))
    total = len(pairs)
    gap = 0.0
    for items in bins.values():
        accuracy = sum(correct for _, correct in items) / len(items)
        confidence = sum(p for p, _ in items) / len(items)
        gap += len(items) / total * abs(accuracy - confidence)
    return gap


def macro_f1(truths: Sequence[str], predicted: Sequence[str | None]) -> float | None:
    """F1 macro sobre los valores que aparecen; una predicción faltante cuenta como fallo del valor real."""
    labels = sorted({*truths, *(p for p in predicted if p is not None)})
    if not truths or not labels:
        return None
    scores: list[float] = []
    for label in labels:
        tp = sum(t == label and p == label for t, p in zip(truths, predicted, strict=True))
        fp = sum(t != label and p == label for t, p in zip(truths, predicted, strict=True))
        fn = sum(t == label and p != label for t, p in zip(truths, predicted, strict=True))
        scores.append(2 * tp / (2 * tp + fp + fn) if tp else 0.0)
    return sum(scores) / len(scores)


def precision_at_threshold(scored: Sequence[Scored]) -> float | None:
    accepted = [s for s in scored if s.accepted]
    if not accepted:
        return None
    return sum(s.predicted == s.truth for s in accepted) / len(accepted)


def coverage(scored: Sequence[Scored]) -> float | None:
    if not scored:
        return None
    return sum(s.accepted for s in scored) / len(scored)


def recall_at_threshold(scored: Sequence[Scored]) -> float | None:
    """Recall macro por valor real: aceptados y acertados / ejemplos con ese valor real."""
    if not scored:
        return None
    by_value: dict[str, list[Scored]] = defaultdict(list)
    for item in scored:
        by_value[item.truth].append(item)
    recalls = [sum(s.accepted and s.predicted == s.truth for s in items) / len(items)
               for items in by_value.values()]
    return sum(recalls) / len(recalls)


def percentile(values: Sequence[int], q: int) -> int | None:
    """Percentil por rango más cercano (nearest-rank)."""
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(math.ceil(q / 100 * len(ordered)), 1) - 1]
