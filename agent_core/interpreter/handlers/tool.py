"""`tool` de lectura y `compute` (M2 §3.3, §3.4, §5). Las escrituras van por `write.py`."""

from typing import Literal

from agent_core.domain import (
    EngineEvent,
    EntityKind,
    EntityRef,
    Fact,
    FactSource,
    JsonValue,
    RiskClass,
    RunState,
    ToolCalledPayload,
    ToolNode,
)
from agent_core.interpreter.audit import ViewsAudit
from agent_core.interpreter.calls import tool_call_context
from agent_core.interpreter.context import Resume, StepContext
from agent_core.interpreter.events import Events
from agent_core.interpreter.handlers.base import NodeResult, clear_attempts, request_step_up
from agent_core.interpreter.refs import exact_ref
from agent_core.interpreter.resolve import MissingPath, input_ids, resolve_args
from agent_core.ports import IdKind, ToolCallContext, ToolResult, ToolStatus

MAX_RETRIES = 2
_RETRIABLE = frozenset({ToolStatus.timeout, ToolStatus.error})
_BRANCH = {ToolStatus.ok: "ok", ToolStatus.error: "error", ToolStatus.timeout: "timeout",
           ToolStatus.denied: "denied", ToolStatus.uncertain: "error"}
_NS_PER_MS = 1_000_000


def call_tool(ctx: StepContext, tool: EntityRef, args: dict[str, JsonValue],
          call: ToolCallContext) -> tuple[ToolResult, int, bool]:
    """`(resultado, latency_ms, cortado_por_el_breaker)`. Nunca propaga `Exception` de la tool."""
    if ctx.breaker.is_open(tool, ctx.clock.now()):
        cut = ToolResult(status=ToolStatus.error, call_id=ctx.ids.new_id(IdKind.call), error="circuit_open")
        return cut, 0, True
    started = ctx.clock.monotonic_ns()
    try:
        result = ctx.tools.execute(tool, args, dict(ctx.bound_params), call)
    except Exception as exc:
        result = ToolResult(status=ToolStatus.error, call_id=ctx.ids.new_id(IdKind.call),
                            error=type(exc).__name__)
    return result, (ctx.clock.monotonic_ns() - started) // _NS_PER_MS, False


def handle_tool(node: ToolNode, state: RunState, ctx: StepContext, resume: Resume) -> NodeResult:
    cfg = node.config
    tool = exact_ref(ctx, EntityKind.tool, cfg.tool)
    definition = ctx.tools.definition(tool)
    try:
        args = resolve_args(state, cfg.args)
    except MissingPath:
        return NodeResult(state, result_key="error")
    events = Events(ctx)
    audit = ViewsAudit(ctx.views, ctx.vault)
    audit_args = audit.args(args, definition)
    call = tool_call_context(state, ctx)
    emitted: list[EngineEvent] = []
    retries = MAX_RETRIES if definition.idempotent else 0
    for attempt in range(1, retries + 2):
        result, latency_ms, cut = call_tool(ctx, tool, args, call)
        ok = result.status is ToolStatus.ok
        shown, fingerprint = audit.result(result.result_full, definition) if ok else (None, None)
        payload = ToolCalledPayload(
            node_id=node.id, tool=tool, call_id=result.call_id, status=result.status, args=audit_args,
            result=shown, result_fp=fingerprint, error=result.error, attempt=attempt, latency_ms=latency_ms)
        emitted.append(events.tool_called(state, payload))
        if result.status in _RETRIABLE and not cut:
            ctx.breaker.record_failure(tool, ctx.clock.now())
            if attempt <= retries:
                continue
        break
    if result.status is ToolStatus.step_up_required:
        level = result.required_level or definition.min_auth_level
        return request_step_up(state, ctx, node.id, level, cfg.step_up_max_attempts, emitted)
    if result.status is ToolStatus.ok:
        ctx.breaker.record_success(tool)
        kind: Literal["tool", "compute"] = "compute" if definition.risk_class is RiskClass.compute else "tool"
        fact = Fact(fact_id=ctx.ids.new_id(IdKind.fact), value=result.result_full,
                    source=FactSource(kind=kind, ref=str(tool), inputs=input_ids(state, cfg.args)),
                    ts=ctx.clock.now())
        facts = {**state.facts, cfg.save_as: fact}
        state = clear_attempts(state, node.id).model_copy(update={"facts": facts})
    elif result.status is ToolStatus.denied:
        emitted.append(events.access_denied(state, tool))
    return NodeResult(state, result_key=_BRANCH[result.status], events=emitted)
