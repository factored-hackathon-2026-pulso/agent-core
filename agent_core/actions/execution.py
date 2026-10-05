"""Escritura con dos commits propios, readback por clave y recuperación (M3 §3.4–§3.6; ADR 0007)."""

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from types import MappingProxyType

from agent_core.actions.context import ActionContext
from agent_core.actions.events import EventFactory
from agent_core.actions.machine import VERIFIABLE, Trigger
from agent_core.actions.results import VerifyResult, WriteResult
from agent_core.actions.store import add_action, move, replace_action, single_action
from agent_core.domain import (
    Action,
    ActionState,
    EngineEvent,
    EntityRef,
    Fact,
    FactSource,
    IllegalTransition,
    JsonValue,
    RiskClass,
    RunState,
    ToolCalled,
    ToolCalledPayload,
    ToolDef,
    VerifyNode,
    WriteToolNode,
    canonical_bytes,
    sha256_hex,
)
from agent_core.ports import Clock, IdKind, IdSource, ToolResult, ToolStatus

_NS_PER_MS = 1_000_000
# Todo lo demás (error, timeout, 5xx, uncertain, excepción) es `uncertain` (ADR 0007, C4).
_WRITE_TRIGGER: Mapping[ToolStatus, Trigger] = MappingProxyType({
    ToolStatus.ok: Trigger.tool_ok,
    ToolStatus.denied: Trigger.tool_denied,
    ToolStatus.step_up_required: Trigger.tool_step_up,
})
_WRITE_RESULT: Mapping[Trigger, WriteResult] = MappingProxyType({
    Trigger.tool_ok: "ok",
    Trigger.tool_denied: "denied",
    Trigger.tool_step_up: "step_up_required",
    Trigger.tool_uncertain: "uncertain",
})


def commit_point(ctx: ActionContext, state: RunState, events: list[EngineEvent]) -> RunState:
    """Commit propio de M3: UoW nueva, `save_run` con la versión esperada y eventos por el `EventRecorder`.

    Un fallo aquí se propaga: la acción queda como estaba en el último commit aplicado y la recuperación
    (M4) la lleva a `verify` si quedó en `executing` (M3 §5)."""
    with ctx.uow_factory() as uow:
        saved = uow.save_run(state, state.state_version)
        ctx.record(uow, saved, events)
        uow.commit()
    return saved


@dataclass(frozen=True)
class _Call:
    result: ToolResult
    latency_ms: int


class Executions:
    """`execute_write`, `verify` y `pending_recovery`: el I/O de M3, siempre a través de `ActionContext`."""

    def __init__(self, ids: IdSource, clock: Clock, events: EventFactory) -> None:
        self._ids = ids
        self._clock = clock
        self._events = events

    def freeze_draft_write(self, state: RunState, node: WriteToolNode, resolved_args: dict[str, JsonValue],
                           tool_def: ToolDef) -> RunState:
        """Congela la acción de una escritura `draft` directo en `confirmed`, sin token: no hay `confirm`.

        Con una acción `confirmed` de ese nodo en el flow activo (reentrada tras un step-up) conserva la
        congelada con sus args (M3 §3.7)."""
        tool = node.config.tool
        if not node.config.draft or tool is None:
            raise ValueError(f"{node.id}: no es una escritura draft")
        if tool_def.risk_class is not RiskClass.write_draft or tool_def.id != tool.id:
            raise ValueError(f"{node.id}: tool_def no es la write_draft que declara el nodo")
        if state.active_flow is None:
            raise IllegalTransition(f"escritura {node.id}: no hay flow activo")
        flow = state.active_flow.flow
        if any(a.write_node_id == node.id and a.flow == flow and a.state is ActionState.confirmed
               for a in state.actions):
            return state
        args = deepcopy(resolved_args)  # congelada: nadie comparte estado mutable con el llamador
        action_id = self._ids.new_id(IdKind.action)
        action = Action(action_id=action_id, write_node_id=node.id, flow=flow,
                        tool=EntityRef(id=tool_def.id, version=tool_def.version), args=args,
                        args_hash=sha256_hex(canonical_bytes(args)), state=ActionState.confirmed,
                        idempotency_key=action_id, created_at=self._clock.now())
        return add_action(state, action)

    def execute_write(self, state: RunState, node: WriteToolNode,
                      ctx: ActionContext) -> tuple[RunState, WriteResult, list[EngineEvent]]:
        """Los eventos devueltos ya quedaron persistidos en los commits 1 y 2: M4 no los vuelve a agregar."""
        if node.config.draft:
            action = single_action(state, {ActionState.confirmed}, write_node_id=node.id)
        else:
            action = single_action(state, {ActionState.confirmed}, node.config.action_from)
        tool_def = ctx.tools.definition(action.tool)
        if not tool_def.is_write:
            raise ValueError(f"{node.id}: {action.tool} no es una tool de escritura")
        executing = move(action, Trigger.dispatch)
        state = replace_action(state, executing)
        dispatched = self._events.dispatched(state, ctx.turn_id, executing,
                                         ctx.audit.args_fingerprint(action.args, tool_def))
        state = commit_point(ctx, state, [dispatched])  # commit 1: executing + action_dispatched
        # Siempre los args congelados, nunca los del nodo; idempotency_key == action_id en todo reintento.
        call = self._call(ctx, action.tool, deepcopy(action.args), action.idempotency_key,
                          failure=ToolStatus.uncertain)
        trigger = _WRITE_TRIGGER.get(call.result.status, Trigger.tool_uncertain)
        state = replace_action(state, move(executing, trigger))
        if trigger is Trigger.tool_ok:
            state = self._save_fact(state, node.config.save_as, call.result.result_full, action.action_id)
        attempt = state.node_attempts.get(node.id, 0) + 1  # cada step-up previo del nodo fue un intento
        called = self._tool_called(state, ctx, node.id, action.tool, tool_def, action.args, call,
                                   action.action_id, attempt)
        state = commit_point(ctx, state, [called])  # commit 2: resultado + tool_called
        return state, _WRITE_RESULT[trigger], [dispatched, called]

    def verify(self, state: RunState, node: VerifyNode,
               ctx: ActionContext) -> tuple[RunState, VerifyResult, list[EngineEvent]]:
        """Readback por `idempotency_key`; nunca re-ejecuta la escritura. Los eventos los persiste M4."""
        if node.config.by != "idempotency_key":
            raise ValueError(f"{node.id}: verify solo consulta por idempotency_key (ADR 0007 §5)")
        action = single_action(state, VERIFIABLE)
        readback = ctx.resolve(node.config.readback)
        readback_def = ctx.tools.definition(readback)
        args: dict[str, JsonValue] = {"idempotency_key": action.idempotency_key}
        call = self._call(ctx, readback, deepcopy(args), None, failure=ToolStatus.error)
        answered = call.result.status is ToolStatus.ok  # contestó: puede probar que el efecto falta
        found = answered and call.result.result_full is not None
        holds = found and self._holds(ctx, node.config.predicate, call.result.result_full)
        result: VerifyResult
        if not answered:
            # Error, timeout o excepción del readback: no prueba que el efecto no exista. La acción no pasa a
            # `failed` (un reintento podría duplicar la escritura); `executing` queda `uncertain`.
            result = "unavailable"
            if action.state is ActionState.executing:
                state = replace_action(state, move(action, Trigger.tool_uncertain))
        else:
            result = "verified" if holds else "failed"
            state = replace_action(state, move(action, Trigger.verified if holds else Trigger.failed))
        if found:
            state = self._save_fact(state, node.config.save_as, call.result.result_full, action.action_id)
        called = self._tool_called(state, ctx, node.id, readback, readback_def, args, call,
                                   action.action_id, 1)
        verified = self._events.verified(state, ctx.turn_id, action.action_id, result, call.result.call_id)
        return state, result, [called, verified]

    @staticmethod
    def pending_recovery(state: RunState) -> list[str]:
        return [a.action_id for a in state.actions if a.state is ActionState.executing]

    @staticmethod
    def _holds(ctx: ActionContext, predicate: JsonValue, readback: JsonValue) -> bool:
        try:
            return ctx.predicate(predicate, {"readback": readback}) is True
        except Exception:  # un predicado que falla no prueba el efecto
            return False

    def _call(self, ctx: ActionContext, tool: EntityRef, args: dict[str, JsonValue],
              idempotency_key: str | None, *, failure: ToolStatus) -> _Call:
        start = self._clock.monotonic_ns()
        try:
            result = ctx.tools.execute(tool, args, dict(ctx.bound_params), ctx.call,
                                       idempotency_key=idempotency_key)
        except Exception as exc:  # reset, timeout del cliente, bug del adaptador: el efecto es desconocido
            result = ToolResult(status=failure, call_id=self._ids.new_id(IdKind.call),
                                error=type(exc).__name__)  # nunca el mensaje: puede traer PII
        return _Call(result, (self._clock.monotonic_ns() - start) // _NS_PER_MS)

    def _save_fact(self, state: RunState, save_as: str, value: JsonValue, action_id: str) -> RunState:
        fact = Fact(fact_id=self._ids.new_id(IdKind.fact), value=value,
                    source=FactSource(kind="tool", ref=action_id), ts=self._clock.now())
        return state.model_copy(update={"facts": {**state.facts, save_as: fact}})

    def _tool_called(
        self, state: RunState, ctx: ActionContext, node_id: str, tool: EntityRef, tool_def: ToolDef,
        args: dict[str, JsonValue], call: _Call, action_id: str, attempt: int,
    ) -> ToolCalled:
        ok = call.result.status is ToolStatus.ok
        result, fingerprint = ctx.audit.result(call.result.result_full, tool_def) if ok else (None, None)
        payload = ToolCalledPayload(
            node_id=node_id,
            tool=tool,
            call_id=call.result.call_id,
            status=call.result.status,
            args=ctx.audit.args(args, tool_def),
            result=result,
            result_fp=fingerprint,
            error=call.result.error,
            attempt=attempt,
            action_id=action_id,
            latency_ms=call.latency_ms,
            tool_source=tool_def.source,
        )
        return self._events.tool_called(state, ctx.turn_id, payload)
