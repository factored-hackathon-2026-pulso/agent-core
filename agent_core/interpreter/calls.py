"""Contexto de llamada a tools (M0 §2.9). `build_action_context` llega en la Task 11."""

from agent_core.domain import RunState
from agent_core.interpreter.context import StepContext
from agent_core.ports import ToolCallContext


def tool_call_context(state: RunState, ctx: StepContext) -> ToolCallContext:
    return ToolCallContext(run_id=state.run_id, release=state.release, principal=state.principal,
                           on_behalf_of=state.on_behalf_of, subject=state.subject, turn_id=ctx.turn_id)
