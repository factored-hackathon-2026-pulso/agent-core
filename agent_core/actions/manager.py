"""`ActionManager`: interfaz pública de M3, dueño de `RunState.actions` y de confirm → act → verify."""

from agent_core.actions.confirmation import Confirmations
from agent_core.actions.context import ActionContext
from agent_core.actions.events import EventFactory
from agent_core.domain import ConfirmationPrompt, ConfirmNode, EngineEvent, JsonValue, RunState, ToolDef
from agent_core.ports import Clock, IdSource


def _same_run(state: RunState, ctx: ActionContext) -> None:
    if ctx.call.run_id != state.run_id:
        raise ValueError("el contexto de la llamada es de otro run")


class ActionManager:
    """Dueño de `RunState.actions` y del invariante confirm → act → verify (M3)."""

    def __init__(self, ids: IdSource, clock: Clock) -> None:
        events = EventFactory(ids, clock)
        self._confirmations = Confirmations(ids, clock, events)

    def propose(
        self, state: RunState, confirm_node: ConfirmNode, resolved_args: dict[str, JsonValue],
        tool_def: ToolDef, ctx: ActionContext,
    ) -> tuple[RunState, ConfirmationPrompt, list[EngineEvent]]:
        """Congela la acción o, con una `proposed` vigente, la repite con el token rotado (M3 §3.2)."""
        _same_run(state, ctx)
        return self._confirmations.propose(state, confirm_node, resolved_args, tool_def, ctx)
