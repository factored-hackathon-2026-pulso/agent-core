"""Doble de `DecisionProvider` (M5): salidas, fallas y latencias guionadas. No decide nada.

No confundir con `ScriptedDecision` (doble del `DecisionPort` de M2)."""

from collections import deque
from collections.abc import Sequence
from copy import deepcopy
from dataclasses import dataclass, replace
from datetime import timedelta
from typing import TYPE_CHECKING

from agent_core.decision import ProviderError, ProviderTimeout, RawPrediction
from agent_core.domain import JsonValue, Locale, ProviderSpec
from testing.fakes.clock import FakeClock


@dataclass(frozen=True)
class Timeout:
    """`predict` avanza el reloj `after_ms` y lanza `ProviderTimeout`."""
    after_ms: int = 0


@dataclass(frozen=True)
class Failure:
    """`predict` lanza `ProviderError(message)`."""
    message: str = "falla guionada"


@dataclass(frozen=True)
class Reply:
    """Una salida con una latencia guionada: el reloj avanza `latency_ms` y `raw.latency_ms` la refleja."""
    raw: RawPrediction
    latency_ms: int


type Step = RawPrediction | Timeout | Failure | Reply


class ScriptedProvider:
    def __init__(self, name: str, script: Sequence[Step] = (), clock: FakeClock | None = None) -> None:
        self.name = name
        self._script = deque(script)
        self._clock = clock
        self.calls: list[tuple[ProviderSpec, dict[str, JsonValue], str]] = []
        self.schemas: list[dict[str, JsonValue]] = []  # esquema efectivo de cada llamada

    def push(self, *steps: Step) -> None:
        self._script.extend(steps)

    def predict(self, spec: ProviderSpec, inputs_model_view: dict[str, JsonValue],
                schema: dict[str, JsonValue], locale: Locale) -> RawPrediction:
        self.calls.append((spec, deepcopy(inputs_model_view), locale))
        self.schemas.append(deepcopy(schema))
        if not self._script:
            raise AssertionError("ScriptedProvider sin salida guionada")
        step = self._script.popleft()
        if isinstance(step, Timeout):
            self._advance(step.after_ms)
            raise ProviderTimeout(f"{self.name}: timeout guionado")
        if isinstance(step, Failure):
            raise ProviderError(step.message)
        if isinstance(step, Reply):
            self._advance(step.latency_ms)
            return replace(step.raw, latency_ms=step.latency_ms)
        self._advance(step.latency_ms)
        return step

    def _advance(self, ms: int) -> None:
        if self._clock is not None and ms:
            self._clock.advance(timedelta(milliseconds=ms))


if TYPE_CHECKING:
    from agent_core.decision import DecisionProvider

    def _conforms(x: ScriptedProvider) -> DecisionProvider:
        return x
