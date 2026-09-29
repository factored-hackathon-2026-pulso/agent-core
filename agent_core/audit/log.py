"""`AuditLog` (M11 §2): encadena y persiste dentro de la transacción del turno (o de una escritura de M3)."""

from collections.abc import Callable

from agent_core.audit.chain import ChainCheck, ChainedEvent, chain_events, check_chain
from agent_core.domain import EngineEvent, RunState
from agent_core.ports import AuditSink, UnitOfWork, UnitOfWorkFactory


class AuditLog:
    def __init__(self, sink: AuditSink, uow_factory: UnitOfWorkFactory | None = None) -> None:
        self._sink = sink
        self._uow_factory = uow_factory

    def append(self, uow: UnitOfWork, run_id: str, events: list[EngineEvent]) -> list[ChainedEvent]:
        """Asigna `seq`, `prev_hash` y `hash` y los agrega a la UoW. Llamadas sucesivas en la misma UoW
        siguen la cadena (`UnitOfWork.last_event` ve lo pendiente)."""
        if not events:
            return []
        chained = chain_events(run_id, events, uow.last_event(run_id))
        uow.append_events(run_id, chained)
        return chained

    def append_standalone(self, run_id: str, events: list[EngineEvent]) -> list[ChainedEvent]:
        """Eventos fuera de un turno (p. ej. `access_denied` de M9): UoW propia, encadenada y commiteada."""
        if self._uow_factory is None:
            raise RuntimeError("AuditLog sin uow_factory: no puede abrir una UoW propia")
        with self._uow_factory() as uow:
            chained = self.append(uow, run_id, events)
            uow.commit()
        return chained

    def verify_chain(self, run_id: str) -> ChainCheck:
        return check_chain(run_id, self._sink.read(run_id))

    def recorder(self) -> Callable[[UnitOfWork, RunState, list[EngineEvent]], None]:
        """Con la firma del `EventRecorder` de M3; M4 lo cablea como `ActionContext.record`.

        Solo agrega los eventos de M3. M3 (`actions/context.py`) y M4 (spec §14) exigen volcar primero los
        eventos pendientes del turno y después los de M3: M4 debe envolver este callable (vuelca lo pendiente
        con `append` y luego estos eventos) o si no el orden de la cadena difiere del orden causal y el
        replay diverge en falso (spec M11, "Riesgos / abiertos para Task 14")."""

        def record(uow: UnitOfWork, state: RunState, events: list[EngineEvent]) -> None:
            self.append(uow, state.run_id, events)

        return record
