"""Doble de `DecisionPort` (M2): resultados guionados en orden. No decide nada."""

from collections import deque
from collections.abc import Sequence
from copy import deepcopy
from decimal import Decimal
from typing import TYPE_CHECKING

from agent_core.domain import Decision, EntityRef, JsonValue, Locale
from agent_core.interpreter import DecisionResult


def make_decision(value: dict[str, JsonValue], above: dict[str, bool], *, decision_id: str = "decision-0001",
                  tokens: int = 0, cost: str = "0") -> DecisionResult:
    """Decisión sintética: `p_cal` alto en los campos sobre el umbral y bajo en los demás."""
    decision = Decision(
        decision_id=decision_id, value=value,
        p_cal={field: (0.95 if flag else 0.2) for field, flag in above.items()},
        provider_used="scripted", model_version="scripted-1",
    )
    return DecisionResult(decision=decision, above_threshold=dict(above), tokens=tokens,
                          cost_usd=Decimal(cost))


class ScriptedDecision:
    def __init__(self, script: Sequence[DecisionResult] = ()) -> None:
        self._script = deque(script)
        self.calls: list[tuple[EntityRef, dict[str, JsonValue], str]] = []

    def push(self, *results: DecisionResult) -> None:
        self._script.extend(results)

    def decide(self, model: EntityRef, inputs_model_view: dict[str, JsonValue],
               locale: Locale) -> DecisionResult:
        self.calls.append((model, deepcopy(inputs_model_view), locale))
        if not self._script:
            raise AssertionError("ScriptedDecision sin resultado guionado")
        return self._script.popleft()


if TYPE_CHECKING:
    from agent_core.interpreter import DecisionPort

    def _conforms(x: ScriptedDecision) -> DecisionPort:
        return x
