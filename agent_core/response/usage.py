"""Acumulador del uso del LLM de una respuesta (spec §3.2): `LlmUsage` con la hora del `Clock` inyectado."""

from collections.abc import Callable
from decimal import Decimal

from agent_core.domain import GatewayError, LlmUsage
from agent_core.ports import Clock, GenerationResult

_NS_PER_MS = 1_000_000


class UsageMeter:
    def __init__(self, clock: Clock) -> None:
        self._clock = clock
        self._calls = 0
        self._latency_ms = 0
        self._tokens_in = 0
        self._tokens_out = 0
        self._cost = Decimal(0)
        self._cost_known = True
        self._models: list[str] = []

    def call(self, fn: Callable[[], GenerationResult]) -> GenerationResult:
        """Ejecuta una llamada al gateway y acumula su uso (si falla, lo que el error informe)."""
        started = self._clock.monotonic_ns()
        try:
            result = fn()
        except GatewayError as error:
            self._record(started)
            if error.tokens_in is not None:
                self._tokens_in += error.tokens_in
            if error.tokens_out is not None:
                self._tokens_out += error.tokens_out
            if error.cost_usd is None:
                self._cost_known = False
            else:
                self._cost += error.cost_usd
            self._add_model(error.model)
            raise
        self._record(started)
        self._tokens_in += result.tokens_in
        self._tokens_out += result.tokens_out
        self._cost += result.cost_usd
        self._add_model(result.model)
        return result

    def usage(self) -> LlmUsage | None:
        if self._calls == 0:
            return None
        return LlmUsage(calls=self._calls, latency_ms=self._latency_ms, tokens_in=self._tokens_in,
                        tokens_out=self._tokens_out, cost_usd=self._cost, cost_known=self._cost_known,
                        models=list(self._models))

    def _record(self, started: int) -> None:
        self._calls += 1
        self._latency_ms += (self._clock.monotonic_ns() - started) // _NS_PER_MS

    def _add_model(self, model: str | None) -> None:
        if model is not None and model not in self._models:
            self._models.append(model)
