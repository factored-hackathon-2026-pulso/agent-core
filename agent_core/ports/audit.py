from typing import Protocol

from agent_core.domain.events import EngineEvent
from agent_core.domain.shared import OutboxMessage


class AuditSink(Protocol):
    """Lectura y appends fuera de un turno; la escritura del turno va por la UoW."""

    def read(self, run_id: str) -> list[EngineEvent]: ...

    def append_outside_turn(self, run_id: str, events: list[EngineEvent]) -> None: ...


class Outbox(Protocol):
    """Lo consume la unidad 4; se escribe por la UoW."""

    def pending(self, limit: int) -> list[OutboxMessage]: ...

    def mark_delivered(self, message_id: str) -> None: ...
