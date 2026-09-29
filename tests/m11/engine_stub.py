"""Motor de prueba: re-emite los eventos grabados pasando por los puertos grabados, con mutaciones opcionales.
Reproduce en pequeño lo que hará M4 + M2: pide tools/LLM a los puertos y devuelve la secuencia producida."""

from collections.abc import Callable

from agent_core.audit.replay.ports import RecordedPorts
from agent_core.audit.replay.runner import ReplayCase
from agent_core.domain import EngineEvent

Mutation = Callable[[EngineEvent], EngineEvent]


class StubEngine:
    def __init__(self, mutate: Mutation | None = None, *, touch_full: bool = False) -> None:
        self._mutate, self._touch_full = mutate, touch_full
        self.calls: list[str] = []

    def run(self, case: ReplayCase, ports: RecordedPorts) -> list[EngineEvent]:
        out: list[EngineEvent] = []
        for e in case.recorded:
            kind = getattr(e, "type", "")
            if kind == "tool_called":
                self.calls.append("tool")
                ports.tools.execute(e.payload.tool, {}, {}, None)  # type: ignore[attr-defined, arg-type]
                if self._touch_full:
                    ports.full.tool_result(e.payload.call_id)  # type: ignore[attr-defined]
            out.append(self._mutate(e) if self._mutate else e)
        return out
