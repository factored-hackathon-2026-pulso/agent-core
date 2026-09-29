"""Ejecutor de tools guionable. No ejecuta nada real: solo resultados guionados, handlers del test y un
almacén de efectos en memoria. Los argumentos y resultados se copian (nadie comparte estado mutable con el
llamador) y ningún mensaje de error incluye argumentos ni parámetros (pueden contener PII o secretos)."""

from collections import deque
from collections.abc import Callable, Sequence
from copy import deepcopy
from dataclasses import dataclass
from typing import TYPE_CHECKING

from agent_core.domain import EntityRef, JsonValue, ToolDef
from agent_core.ports import IdKind, IdSource, ToolCallContext, ToolResult, ToolStatus

Handler = Callable[[dict[str, JsonValue]], JsonValue]
_WRITE_STATUSES = frozenset(
    {ToolStatus.ok, ToolStatus.denied, ToolStatus.uncertain, ToolStatus.step_up_required}
)


@dataclass(frozen=True)
class Scripted:
    """Resultado guionado. En escrituras, `effect` dice si el backend aplicó el efecto aunque falle."""

    status: ToolStatus
    result: JsonValue = None
    error: str | None = None
    effect: bool = True


@dataclass(frozen=True)
class RecordedCall:
    """Llamada registrada solo como datos (copias); nunca el contexto ni el principal."""

    tool: EntityRef
    args: dict[str, JsonValue]
    bound_params: dict[str, str]
    idempotency_key: str | None
    status: ToolStatus


class FakeToolExecutor:
    def __init__(self, ids: IdSource) -> None:
        self._ids = ids
        self._defs: dict[EntityRef, ToolDef] = {}
        self._scripts: dict[EntityRef, deque[Scripted]] = {}
        self._handlers: dict[EntityRef, Handler] = {}
        self._readbacks: dict[EntityRef, EntityRef] = {}
        self.calls: list[RecordedCall] = []
        self.effects: dict[EntityRef, dict[str, JsonValue]] = {}

    def __repr__(self) -> str:
        return f"FakeToolExecutor(tools={len(self._defs)}, calls={len(self.calls)})"

    def register(self, tool_def: ToolDef, *, script: Sequence[Scripted] = (),
                 handler: Handler | None = None) -> None:
        ref = EntityRef(id=tool_def.id, version=tool_def.version)
        self._defs[ref] = tool_def
        self._scripts[ref] = deque(script)
        if handler is not None:
            self._handlers[ref] = handler

    def register_readback(self, readback_def: ToolDef, *, of: EntityRef) -> None:
        ref = EntityRef(id=readback_def.id, version=readback_def.version)
        self._defs[ref] = readback_def
        self._readbacks[ref] = of

    def definition(self, tool: EntityRef) -> ToolDef:
        return self._defs[tool]

    def execute(self, tool: EntityRef, args: dict[str, JsonValue], bound_params: dict[str, str],
                ctx: ToolCallContext, idempotency_key: str | None = None) -> ToolResult:
        tool_def = self._defs[tool]
        if tool_def.is_write and idempotency_key is None:
            raise ValueError("una escritura siempre lleva idempotency_key (ADR 0007)")
        if ctx.principal.auth.level < tool_def.min_auth_level:
            return self._result(tool, args, bound_params, idempotency_key, tool_def,
                                Scripted(ToolStatus.step_up_required), required=True)
        if tool in self._readbacks:
            key = args.get("idempotency_key")
            resource = self.effects.get(self._readbacks[tool], {}).get(key) if isinstance(key, str) else None
            return self._result(tool, args, bound_params, idempotency_key, tool_def,
                                Scripted(ToolStatus.ok, result=resource))
        if tool_def.is_write and idempotency_key is not None:
            return self._write(tool, args, bound_params, idempotency_key, tool_def)
        outcome = self._next(tool, args)
        return self._result(tool, args, bound_params, idempotency_key, tool_def, outcome,
                            required=outcome.status is ToolStatus.step_up_required)

    def _next(self, tool: EntityRef, args: dict[str, JsonValue]) -> Scripted:
        script = self._scripts[tool]
        if script:
            return script.popleft()
        handler = self._handlers.get(tool)
        return Scripted(ToolStatus.ok, result=handler(deepcopy(args)) if handler else deepcopy(args))

    def _write(self, tool: EntityRef, args: dict[str, JsonValue], bound_params: dict[str, str],
               key: str, tool_def: ToolDef) -> ToolResult:
        store = self.effects.setdefault(tool, {})
        if key in store:
            replay = Scripted(ToolStatus.ok, result=store[key])
            return self._result(tool, args, bound_params, key, tool_def, replay)
        outcome = self._next(tool, args)
        if outcome.status not in _WRITE_STATUSES:
            raise ValueError(f"estado de escritura fuera de contrato: {outcome.status}")
        if outcome.status is ToolStatus.step_up_required:  # el backend lo pide antes de cualquier efecto
            return self._result(tool, args, bound_params, key, tool_def, outcome, required=True)
        resource = outcome.result if outcome.result is not None else deepcopy(args)
        if outcome.status is ToolStatus.ok or (outcome.status is ToolStatus.uncertain and outcome.effect):
            store[key] = deepcopy(resource)
        shown = Scripted(outcome.status, result=resource if outcome.status is ToolStatus.ok else None,
                         error=outcome.error)
        return self._result(tool, args, bound_params, key, tool_def, shown)

    def _result(self, tool: EntityRef, args: dict[str, JsonValue], bound_params: dict[str, str],
                key: str | None, tool_def: ToolDef, outcome: Scripted, required: bool = False) -> ToolResult:
        self.calls.append(RecordedCall(tool, deepcopy(args), dict(bound_params), key, outcome.status))
        return ToolResult(
            status=outcome.status,
            result_full=deepcopy(outcome.result),
            source=tool_def.source,
            call_id=self._ids.new_id(IdKind.call),
            error=outcome.error,
            required_level=tool_def.min_auth_level if required else None,
        )


if TYPE_CHECKING:
    from agent_core.ports import ToolExecutor

    def _conforms(x: FakeToolExecutor) -> ToolExecutor:
        return x
