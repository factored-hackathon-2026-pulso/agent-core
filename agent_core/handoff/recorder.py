"""Gancho para agregar eventos fuera de la transacción de un turno (patrón de M3).

Por defecto los agrega sin encadenar (fase 1, sin M11); M9 inyecta el que encadena con
`TurnRecorder` de M11."""

from collections.abc import Callable

from agent_core.domain import EngineEvent, RunState
from agent_core.ports import UnitOfWork

EventRecorder = Callable[[UnitOfWork, RunState, list[EngineEvent]], None]


def append_events(uow: UnitOfWork, state: RunState, events: list[EngineEvent]) -> None:
    uow.append_events(state.run_id, events)
