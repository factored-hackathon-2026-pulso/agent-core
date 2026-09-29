"""Regresión isotónica por PAV (pool adjacent violators), determinista.

Entrada: `(p_raw, acierto, id)`. Los puntos se ordenan por `(p_raw, id)`; las `p_raw` iguales se funden en un
punto ponderado antes de agrupar, de modo que los bloques resultantes tienen rangos de `x` disjuntos.
Cada bloque aporta `(x_min, y)` y `(x_max, y)` (uno solo si coinciden): `IsotonicMap.apply` interpola."""

from collections.abc import Sequence
from dataclasses import dataclass

from agent_core.decision.calibration.artifact import IsotonicMap


@dataclass
class _Block:
    x_min: float
    x_max: float
    weight: float
    total: float  # suma ponderada de y

    @property
    def mean(self) -> float:
        return self.total / self.weight


def fit_isotonic(points: Sequence[tuple[float, float, str]]) -> IsotonicMap | None:
    """Mapa isotónico de `p_raw` a frecuencia de acierto; `None` si no hay puntos."""
    if not points:
        return None
    merged: list[_Block] = []
    for x, y, _ in sorted(points, key=lambda point: (point[0], point[2])):
        if merged and merged[-1].x_max == x:
            merged[-1].weight += 1.0
            merged[-1].total += y
        else:
            merged.append(_Block(x, x, 1.0, y))
    stack: list[_Block] = []
    for block in merged:
        stack.append(block)
        while len(stack) > 1 and stack[-2].mean >= stack[-1].mean:
            top = stack.pop()
            below = stack[-1]
            below.x_max = top.x_max
            below.weight += top.weight
            below.total += top.total
    xs: list[float] = []
    ys: list[float] = []
    for block in stack:
        xs.append(block.x_min)
        ys.append(block.mean)
        if block.x_max > block.x_min:
            xs.append(block.x_max)
            ys.append(block.mean)
    return IsotonicMap(xs=xs, ys=ys)
