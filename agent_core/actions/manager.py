"""`ActionManager`: interfaz pública de M3, dueño de `RunState.actions` y de confirm → act → verify."""

from agent_core.actions.confirmation import Confirmations
from agent_core.actions.context import ActionContext
from agent_core.actions.events import EventFactory
from agent_core.actions.results import Answer, AnswerResult
from agent_core.domain import (
    ConfirmationPrompt,
    ConfirmNode,
    EngineEvent,
    InvalidationReason,
    JsonValue,
    RunState,
    ToolDef,
)
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

    def answer(self, state: RunState, confirm_node: ConfirmNode, answer: Answer, token: str | None,
               ctx: ActionContext) -> tuple[RunState, AnswerResult, list[EngineEvent]]:
        """`yes` exige token vigente (el del request, por botón; el de la acción, por texto). `no` cancela.
        `unclear` suma intento; al tope, `max_attempts` (M3 §3.3)."""
        _same_run(state, ctx)
        return self._confirmations.answer(state, confirm_node, answer, token, ctx)

    def expire_tokens(self, state: RunState, *,
                      turn_id: str | None = None) -> tuple[RunState, list[EngineEvent]]:
        """Cancela (`token_expired`) toda acción `proposed` con el token vencido."""
        return self._confirmations.expire_tokens(state, turn_id)

    def invalidate(self, state: RunState, reason: InvalidationReason, *,
                   turn_id: str | None = None) -> tuple[RunState, list[EngineEvent]]:
        """Cancela toda acción `proposed` o `confirmed`; `executing` y posteriores no se tocan (M3 §3.6)."""
        return self._confirmations.invalidate(state, reason, turn_id)
