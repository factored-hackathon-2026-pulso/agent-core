"""`agent` (M2 §3.7, ADR 0019): ReAct acotado de solo lectura y cálculo.

El modelo elige tools de `tools_allowed`; el motor las ejecuta con los `bound_params` del principal (la
autorización por llamada es de la capa de tools) y le devuelve el resultado en vista `model`. La salida final
se valida contra `output_schema` y entra como un hecho de origen `agent`. Nunca escribe: una tool que no sea
`read` o `compute` no se ejecuta, aunque esté listada (G0-07 lo impide en el flow)."""

import re
from typing import Any

from agent_core.domain import (
    AgentNode,
    AgentStepPayload,
    EngineEvent,
    EntityKind,
    EntityRef,
    Fact,
    FactSource,
    IllegalTransition,
    JsonValue,
    RiskClass,
    RunState,
    ToolCalledPayload,
    ToolDef,
)
from agent_core.interpreter.audit import ViewsAudit
from agent_core.interpreter.budgets import charge_model, run_budget_exhausted
from agent_core.interpreter.calls import tool_call_context
from agent_core.interpreter.context import Resume, StepContext
from agent_core.interpreter.events import Events
from agent_core.interpreter.handlers.base import NodeResult, escalate_now
from agent_core.interpreter.handlers.tool import call_tool
from agent_core.interpreter.ports import AgentFinal, AgentObservation, AgentRequest, AgentToolCall
from agent_core.interpreter.refs import exact_ref
from agent_core.interpreter.schema import check_output
from agent_core.ports import IdKind, ToolStatus
from agent_core.views import TOKEN_PATTERN, TokenVault

_TOKEN = re.compile(TOKEN_PATTERN)
_READ_ONLY = frozenset({RiskClass.read, RiskClass.compute})
_RETRIABLE = frozenset({ToolStatus.timeout, ToolStatus.error})
_NS_PER_MS = 1_000_000


def _detokenize(value: Any, vault: TokenVault) -> Any:
    """Un argumento que es exactamente un token del run vuelve a su valor: la tool nunca ve el token."""
    if isinstance(value, str):
        clear = vault.resolve(value) if _TOKEN.fullmatch(value) else None
        return value if clear is None else clear
    if isinstance(value, list):
        return [_detokenize(v, vault) for v in value]
    if isinstance(value, dict):
        return {k: _detokenize(v, vault) for k, v in value.items()}
    return value


class _Loop:
    """El estado mutable de un nodo `agent` mientras corre; un `handle_agent` por instancia."""

    def __init__(self, node: AgentNode, state: RunState, ctx: StepContext) -> None:
        self.node, self.state, self.ctx = node, state, ctx
        self.events = Events(ctx)
        self.emitted: list[EngineEvent] = []
        self.observations: list[AgentObservation] = []
        self.call_ids: list[str] = []
        self.allowed = {exact_ref(ctx, EntityKind.tool, ref) for ref in node.config.tools_allowed}

    def _ms(self, started: int) -> int:
        return (self.ctx.clock.monotonic_ns() - started) // _NS_PER_MS

    def _step_event(self, step: int, started: int, **fields: Any) -> None:
        payload = AgentStepPayload(node_id=self.node.id, step=step, latency_ms=self._ms(started), **fields)
        self.emitted.append(self.events.agent_step(self.state, payload))

    def final(self, step: int, started: int, output: JsonValue) -> str | None:
        """`None` si la salida entró como hecho; si no, el motivo (sin datos) para regenerar."""
        error = check_output(self.node.config.output_schema, output)
        fingerprint = self.ctx.views.project(output, "agent", [], self.ctx.vault).fingerprint
        self._step_event(step, started, kind="final", text_fp=fingerprint)
        if error is not None:
            return error
        fact = Fact(fact_id=self.ctx.ids.new_id(IdKind.fact), value=output,
                    source=FactSource(kind="agent", ref=self.node.id, inputs=list(self.call_ids)),
                    ts=self.ctx.clock.now())
        facts = {**self.state.facts, self.node.config.save_as: fact}
        self.state = self.state.model_copy(update={"facts": facts})
        return None

    def _denied(self, step: int, started: int, call: AgentToolCall) -> None:
        self.emitted.append(self.events.access_denied(self.state, call.tool))
        self._step_event(step, started, kind="tool", tool=call.tool, status=ToolStatus.denied)
        self.observations.append(AgentObservation(call.tool, call.args, ToolStatus.denied))

    def tool(self, step: int, started: int, call: AgentToolCall) -> None:
        if call.tool not in self.allowed:
            return self._denied(step, started, call)
        definition: ToolDef = self.ctx.tools.definition(call.tool)
        if definition.risk_class not in _READ_ONLY:
            return self._denied(step, started, call)
        args = _detokenize(call.args, self.ctx.vault)
        context = tool_call_context(self.state, self.ctx)
        result, latency_ms, cut = call_tool(self.ctx, call.tool, args, context)
        self.call_ids.append(result.call_id)
        self._record_call(call.tool, definition, args, result, latency_ms)
        if result.status in _RETRIABLE and not cut:
            self.ctx.breaker.record_failure(call.tool, self.ctx.clock.now())
        elif result.status is ToolStatus.ok:
            self.ctx.breaker.record_success(call.tool)
        # Elevar el nivel a mitad del bucle no es posible: para el modelo es una tool denegada.
        status = ToolStatus.denied if result.status is ToolStatus.step_up_required else result.status
        shown: JsonValue = None
        if status is ToolStatus.ok:
            source = definition.source or definition.id
            shown = self.ctx.views.project(result.result_full, source, definition.untrusted_fields,
                                           self.ctx.vault).model
        self._step_event(step, started, kind="tool", tool=call.tool, call_id=result.call_id, status=status)
        self.observations.append(AgentObservation(call.tool, call.args, status, shown, result.error))

    def _record_call(self, tool: EntityRef, definition: ToolDef, args: dict[str, JsonValue],
                     result: Any, latency_ms: int) -> None:
        audit = ViewsAudit(self.ctx.views, self.ctx.vault)
        ok = result.status is ToolStatus.ok
        shown, fingerprint = audit.result(result.result_full, definition) if ok else (None, None)
        payload = ToolCalledPayload(
            node_id=self.node.id, tool=tool, call_id=result.call_id, status=result.status,
            args=audit.args(args, definition), result=shown, result_fp=fingerprint, error=result.error,
            attempt=1, latency_ms=latency_ms)
        self.emitted.append(self.events.tool_called(self.state, payload))


def handle_agent(node: AgentNode, state: RunState, ctx: StepContext, resume: Resume) -> NodeResult:
    if ctx.degraded:  # `injection_flagged`: sin modelo en este turno (spec general §4.1 paso 6)
        return NodeResult(state, result_key="gave_up")
    if ctx.agents is None:
        raise IllegalTransition(f"el nodo agent {node.id} necesita un AgentPort en el StepContext")
    loop = _Loop(node, state, ctx)
    feedback: str | None = None
    regenerated = False
    for step in range(1, node.config.max_steps + 1):
        if run_budget_exhausted(loop.state, ctx):
            return escalate_now(loop.state, ctx, "budget_exceeded", loop.emitted)
        started = ctx.clock.monotonic_ns()
        result = ctx.agents.step(
            AgentRequest(node.id, node.config, step, tuple(loop.observations), feedback), loop.state)
        loop.state = charge_model(loop.state, calls=result.model_calls, tokens=result.tokens,
                                  cost=result.cost_usd)
        feedback = None
        if isinstance(result.action, AgentFinal):
            error = loop.final(step, started, result.action.output)
            if error is None:
                return NodeResult(loop.state, result_key="answered", events=loop.emitted)
            if regenerated:
                break  # una sola regeneración; la regeneración también cuenta como paso
            regenerated, feedback = True, error
        else:
            loop.tool(step, started, result.action)
    return NodeResult(loop.state, result_key="gave_up", events=loop.emitted)
