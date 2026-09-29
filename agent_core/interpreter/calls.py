"""Contexto de llamada a tools (M0 §2.9) y `ActionContext` de M3 (M2 §3.3)."""

from agent_core.actions import ActionContext
from agent_core.domain import JsonValue, Message, RefSpec, RunState
from agent_core.interpreter.audit import ViewsAudit
from agent_core.interpreter.context import StepContext
from agent_core.interpreter.jsonlogic import evaluate, truthy
from agent_core.interpreter.refs import make_resolver
from agent_core.interpreter.templates import render_message
from agent_core.ports import ToolCallContext


def tool_call_context(state: RunState, ctx: StepContext) -> ToolCallContext:
    return ToolCallContext(run_id=state.run_id, release=state.release, principal=state.principal,
                           on_behalf_of=state.on_behalf_of, subject=state.subject, turn_id=ctx.turn_id)


def build_action_context(run: RunState, ctx: StepContext) -> ActionContext:
    """Arma el `ActionContext` de M3 con las mismas piezas que usa el resto de M2 (M2 §3.3)."""

    def render(ref: RefSpec, state: RunState) -> Message:
        return render_message(state, ctx, ref)

    def predicate(expr: JsonValue, data: JsonValue) -> bool:
        return truthy(evaluate(expr, data))

    return ActionContext(
        uow_factory=ctx.uow_factory, tools=ctx.tools, call=tool_call_context(run, ctx), render=render,
        predicate=predicate, resolve=make_resolver(ctx), record=ctx.record,
        audit=ViewsAudit(ctx.views, ctx.vault), bound_params=ctx.bound_params,
    )
