"""Métricas puras de la prueba de humo. Sin red ni reloj."""

import math
import re
from collections.abc import Sequence
from dataclasses import dataclass
from itertools import pairwise

_EDGES = (0.0, 0.5, 0.7, 0.9, 1.0)
_WORD = re.compile(r"\w+", re.UNICODE)
_BUCKETS = (("1", 1), ("2-3", 3), ("4-7", 7))


def nearest_rank(values: Sequence[float], percentile: float) -> float | None:
    """Percentil por rango más cercano (el mismo que el informe de M5, spec §3.5)."""
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(math.ceil(percentile / 100 * len(ordered)), 1) - 1]


def accuracy(hits: Sequence[bool]) -> float | None:
    return sum(hits) / len(hits) if hits else None


@dataclass(frozen=True)
class Bin:
    lo: float
    hi: float
    n: int
    mean_p: float | None
    accuracy: float | None


def reliability(pairs: Sequence[tuple[float, bool]]) -> list[Bin]:
    """Curva de calibración cruda: por tramo de `p`, la `p` media y el acierto observado."""
    bins: list[Bin] = []
    for index, (lo, hi) in enumerate(pairwise(_EDGES)):
        last = index == len(_EDGES) - 2
        items = [(p, ok) for p, ok in pairs if lo <= p < hi or (last and p == hi)]
        bins.append(Bin(lo=lo, hi=hi, n=len(items),
                        mean_p=sum(p for p, _ in items) / len(items) if items else None,
                        accuracy=sum(ok for _, ok in items) / len(items) if items else None))
    return bins


def word_count(text: str) -> int:
    return len(_WORD.findall(text))


def bucket(words: int) -> str:
    """Tramo de largo del mensaje: 1, 2-3, 4-7 u 8+ palabras."""
    return next((label for label, top in _BUCKETS if words <= top), "8+")
