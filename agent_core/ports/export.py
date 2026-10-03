"""Lectura paginada de runs y eventos de auditoría para quien los ingiere (N-08, §31.12).

Es una vista de solo lectura sin datos de cliente: del run solo salen identificadores, release, agente, modo,
idioma y estado; nunca slots, hechos ni el id del principal. Los eventos son los de auditoría (ya sin PII,
regla 6). El cursor de los runs es el orden de commit de su último cambio: un run que cambia (se cierra)
vuelve a salir con un cursor mayor, y uno que confirma tarde nunca queda detrás del cursor; el consumidor
reemplaza por `run_id`. El `seq` de cada evento es continuo dentro de su run (la cadena de hashes detecta
huecos), así que un consumidor se pone al día con dos cursores sin perder nada."""

from typing import Protocol

from agent_core.domain import EngineEvent, RunState
from agent_core.domain.base import Model, UtcDatetime
from agent_core.domain.refs import EntityRef


class RunSummary(Model):
    """Resumen de un run para la ingesta: identificadores y estado, sin datos del cliente."""

    run_seq: int  # orden de creación (informativo; no es el cursor)
    cursor: int  # orden de commit del último cambio: el run reaparece cuando cambia (p. ej. al cerrarse)
    run_id: str
    session_id: str | None
    release: str
    agent: EntityRef
    principal_type: str
    mode: str
    locale: str
    status: str
    outcome: str | None
    created_at: UtcDatetime
    closed_at: UtcDatetime | None

    @classmethod
    def of(cls, run_seq: int, state: RunState, cursor: int) -> "RunSummary":
        return cls(run_seq=run_seq, cursor=cursor, run_id=state.run_id, session_id=state.session_id,
                   release=state.release,
                   agent=state.agent, principal_type=str(state.principal.type.value),
                   mode=str(state.mode), locale=str(state.locale), status=state.status,
                   outcome=None if state.outcome is None else str(state.outcome),
                   created_at=state.created_at, closed_at=state.closed_at)


class RunExport(Protocol):
    """Lectura paginada de runs y de sus eventos de auditoría (solo lectura)."""

    def list_runs(self, after_cursor: int, limit: int) -> list[RunSummary]:
        """Runs con `cursor > after_cursor`, por `cursor` ascendente. `limit` cuenta commits, no runs: los
        runs de un mismo commit (p. ej. origen y destino de una transferencia) no se parten en páginas."""
        ...

    def events_after(self, run_id: str, after_seq: int, limit: int) -> list[EngineEvent]:
        """Eventos del run con `seq > after_seq`, por `seq` ascendente, hasta `limit`."""
        ...
