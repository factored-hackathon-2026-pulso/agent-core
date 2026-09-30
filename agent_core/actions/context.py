"""Contexto que M3 recibe de quien lo llama (M2; M4 en la recuperación) (M3 §2).

M3 solo importa M0 (`.importlinter`). Lo que vive en otros módulos llega como gancho:
- plantillas (M1/M2): `render`;
- JSON Logic (M2): `predicate`;
- `RefSpec` → `EntityRef` exacta (`release_view` de M1): `resolve`;
- encadenado de eventos (M11): `record`;
- vista `audit` y huella (M7): `audit`.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Protocol

from agent_core.domain import (
    EngineEvent,
    EntityRef,
    Fingerprint,
    JsonValue,
    Message,
    RefSpec,
    RunState,
    ToolDef,
)
from agent_core.ports import ToolCallContext, ToolExecutor, UnitOfWork, UnitOfWorkFactory


class TemplateRenderer(Protocol):
    """Renderiza la plantilla `ref` en el `locale` del run (M2 con M1)."""

    def __call__(self, ref: RefSpec, state: RunState) -> Message: ...


class PredicateEvaluator(Protocol):
    """JSON Logic de M2 sobre `data`. Una excepción cuenta como `false`."""

    def __call__(self, expr: JsonValue, data: JsonValue) -> bool: ...


class RefResolver(Protocol):
    """Referencia de autoría → referencia exacta de la release."""

    def __call__(self, ref: RefSpec) -> EntityRef: ...


class EventRecorder(Protocol):
    """Persiste `events` dentro de la UoW de un commit propio de M3.

    M4 lo cablea con M11: primero vuelca y encadena los eventos pendientes del turno, luego estos.
    """

    def __call__(self, uow: UnitOfWork, state: RunState, events: list[EngineEvent]) -> None: ...


class AuditProjector(Protocol):
    """Vista `audit` y huella con clave de argumentos y resultados de tools (M7)."""

    def args(self, args: dict[str, JsonValue], tool: ToolDef) -> dict[str, JsonValue]: ...

    def args_fingerprint(self, args: dict[str, JsonValue], tool: ToolDef) -> Fingerprint | None: ...

    def result(self, result_full: JsonValue, tool: ToolDef) -> tuple[JsonValue, Fingerprint | None]: ...


def exact_ref(ref: RefSpec) -> EntityRef:
    """Por defecto: la referencia ya es exacta (si no, `InvalidRuntimeRef`)."""
    return ref.require_exact()


def append_events(uow: UnitOfWork, state: RunState, events: list[EngineEvent]) -> None:
    """Por defecto: agrega los eventos sin encadenar (fase 1, sin M11)."""
    uow.append_events(state.run_id, events)


class RedactAll:
    """Proyección `audit` por defecto hasta cablear M7: nunca deja pasar un valor."""

    def args(self, args: dict[str, JsonValue], tool: ToolDef) -> dict[str, JsonValue]:
        return {key: "***" for key in sorted(args)}

    def args_fingerprint(self, args: dict[str, JsonValue], tool: ToolDef) -> Fingerprint | None:
        return None

    def result(self, result_full: JsonValue, tool: ToolDef) -> tuple[JsonValue, Fingerprint | None]:
        return None, None


@dataclass(frozen=True)
class ActionContext:
    """Dependencias de una llamada a M3. Cada commit propio abre una UoW nueva con `uow_factory`."""

    uow_factory: UnitOfWorkFactory
    tools: ToolExecutor
    call: ToolCallContext
    render: TemplateRenderer
    predicate: PredicateEvaluator
    resolve: RefResolver = exact_ref
    record: EventRecorder = append_events
    audit: AuditProjector = field(default_factory=RedactAll)
    bound_params: Mapping[str, str] = field(default_factory=dict)

    @property
    def turn_id(self) -> str | None:
        return self.call.turn_id
