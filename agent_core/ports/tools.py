from typing import Protocol

from agent_core.domain.base import Model, UtcDatetime
from agent_core.domain.entities import ToolDef
from agent_core.domain.identity import AuthLevel, OnBehalfOf, Principal, SubjectRef
from agent_core.domain.json import JsonValue
from agent_core.domain.refs import EntityRef
from agent_core.domain.shared import ToolStatus

__all__ = ["ToolCallContext", "ToolExecutor", "ToolResult", "ToolStatus"]


class ToolCallContext(Model):
    """Contexto de una llamada a tool: run, release, principal y sujeto (M0 §2.9)."""
    run_id: str
    release: str
    principal: Principal
    on_behalf_of: OnBehalfOf | None = None
    subject: SubjectRef | None = None
    turn_id: str | None = None
    # Instante del turno (Clock), para `ToolDef.max_auth_age` (ADR 0010); None fuera de un turno.
    at: UtcDatetime | None = None


class ToolResult(Model):
    """Solo `result_full` y su `source`: las vistas `model`/`audit` las calcula M7 (M0 rev. 2)."""

    status: ToolStatus
    result_full: JsonValue = None
    source: str | None = None
    call_id: str
    error: str | None = None
    required_level: AuthLevel | None = None


class ToolExecutor(Protocol):
    """Puerto de ejecución de tools (M0 §2.9)."""
    def execute(self, tool: EntityRef, args: dict[str, JsonValue], bound_params: dict[str, str],
                ctx: ToolCallContext, idempotency_key: str | None = None) -> ToolResult:
        """Lectura/compute: ok|error|timeout|denied|step_up_required. Escritura: ok|denied|uncertain|
        step_up_required, y step_up_required siempre antes de cualquier efecto."""
        ...

    def definition(self, tool: EntityRef) -> ToolDef: ...
