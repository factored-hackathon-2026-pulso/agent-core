"""`confirm`, `tool` de escritura y `verify`: todo delega en `ctx.actions` (M3). M2 nunca escribe directo."""

from agent_core.domain import (
    ConfirmNode,
    EngineEvent,
    EntityKind,
    EntityRef,
    IllegalTransition,
    RunState,
    VerifyNode,
    WriteToolNode,
)
from agent_core.interpreter.calls import build_action_context
from agent_core.interpreter.context import Resume, StepContext, Stop
from agent_core.interpreter.events import Events
from agent_core.interpreter.handlers.base import NodeResult, clear_attempts, escalate_now, request_step_up
from agent_core.interpreter.refs import exact_ref
from agent_core.interpreter.resolve import MissingPath, resolve_args

_ANSWERS = ("yes", "no", "unclear")


def handle_confirm(node: ConfirmNode, state: RunState, ctx: StepContext, resume: Resume) -> NodeResult:
    action_ctx = build_action_context(state, ctx)
    try:
        if resume.kind != "confirm_answer":
            args = resolve_args(state, node.config.action.args)
            tool = exact_ref(ctx, EntityKind.tool, node.config.action.tool)
            state, prompt, events = ctx.actions.propose(state, node, args, ctx.tools.definition(tool),
                                                        action_ctx)
            return NodeResult(state, stop=Stop.awaiting_confirmation, confirmation=prompt, events=events)
        answer = resume.value if resume.value in _ANSWERS else "unclear"
        state, result, events = ctx.actions.answer(state, node, answer, resume.token,  # type: ignore[arg-type]
                                                   action_ctx)
    except MissingPath:
        return escalate_now(state, ctx, "validation_failed")
    return NodeResult(state, result_key=result, events=events)


def _action_tool(state: RunState, node: WriteToolNode) -> EntityRef:
    for action in reversed(state.actions):
        if action.confirm_node_id == node.config.action_from:
            return action.tool
    raise IllegalTransition(f"no hay acción para el confirm {node.config.action_from}")


def handle_write(node: WriteToolNode, state: RunState, ctx: StepContext, resume: Resume) -> NodeResult:
    # Los eventos que devuelve `execute_write` ya los persistió el `EventRecorder` de M3: no se agregan (§6).
    state, result, _persisted = ctx.actions.execute_write(state, node, build_action_context(state, ctx))
    tool = _action_tool(state, node)
    if result == "step_up_required":
        level = ctx.tools.definition(tool).min_auth_level
        return request_step_up(state, ctx, node.id, level, node.config.step_up_max_attempts, [])
    events: list[EngineEvent] = [Events(ctx).access_denied(state, tool)] if result == "denied" else []
    if result != "denied":
        state = clear_attempts(state, node.id)
    return NodeResult(state, result_key=result, events=events)


def handle_verify(node: VerifyNode, state: RunState, ctx: StepContext, resume: Resume) -> NodeResult:
    state, result, events = ctx.actions.verify(state, node, build_action_context(state, ctx))
    return NodeResult(state, result_key=result, events=events)
