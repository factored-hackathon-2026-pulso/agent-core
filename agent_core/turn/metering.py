"""Medición por etapas del turno (m04 §3.7). Solo `Clock.monotonic_ns()`; nada de esto decide nada."""

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Literal

from agent_core.domain import TurnStages
from agent_core.ports import Clock

StageName = Literal["guards", "understand", "flow", "response"]
_STAGES = ("guards", "understand", "flow", "response")
_NS_PER_MS = 1_000_000


def _ms(ns: int) -> int:
    return max(ns, 0) // _NS_PER_MS


class StageMeter:
    """`duration_ms` corre desde la construcción (recepción del turno); cada etapa acumula sus ns."""

    def __init__(self, clock: Clock) -> None:
        self._clock = clock
        self._start = clock.monotonic_ns()
        self._ns: dict[str, int] = {}

    @contextmanager
    def stage(self, name: StageName) -> Iterator[None]:
        if name not in _STAGES:
            raise ValueError(f"etapa desconocida: {name}")
        began = self._clock.monotonic_ns()
        try:
            yield
        finally:
            self._ns[name] = self._ns.get(name, 0) + max(self._clock.monotonic_ns() - began, 0)

    def duration_ms(self) -> int:
        return _ms(self._clock.monotonic_ns() - self._start)

    def stages(self) -> TurnStages:
        """Una etapa que nunca corrió queda en `None`."""

        def get(name: str) -> int | None:
            return _ms(self._ns[name]) if name in self._ns else None

        return TurnStages(
            guards_ms=get("guards"),
            understand_ms=get("understand"),
            flow_ms=get("flow"),
            response_ms=get("response"),
        )
