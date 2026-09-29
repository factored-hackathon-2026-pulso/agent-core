"""`respond` con `template_ref` (M2 §3.3). La rama `generate` llega en la Task 12."""

from agent_core.domain import RespondNode, RunState
from agent_core.interpreter.context import Resume, StepContext, Stop
from agent_core.interpreter.handlers.base import NodeResult, escalate_now
from agent_core.interpreter.resolve import MissingPath
from agent_core.interpreter.templates import render_message


def handle_respond(node: RespondNode, state: RunState, ctx: StepContext, resume: Resume) -> NodeResult:
    cfg = node.config
    if cfg.template_ref is None:
        raise NotImplementedError("respond(generate): Task 12")
    try:
        message = render_message(state, ctx, cfg.template_ref)
    except MissingPath:
        return escalate_now(state, ctx, "validation_failed")
    stop = Stop.awaiting_user if cfg.await_ else None
    return NodeResult(state, result_key="next", stop=stop, messages=[message])
