"""`Stop` → `Awaiting` y construcción del `TurnResult` (m04 §3.5)."""

from agent_core.domain import Awaiting, NodeId, RunState, TurnResult
from agent_core.interpreter import Stop
from agent_core.turn.frame import TurnFrame

_BY_STOP = {
    Stop.awaiting_slot: Awaiting.slot,
    Stop.awaiting_confirmation: Awaiting.confirmation,
    Stop.awaiting_step_up: Awaiting.step_up,
    Stop.awaiting_user: Awaiting.input,
}


def awaiting_for(stop: Stop | None, state: RunState) -> tuple[Awaiting, NodeId | None]:
    """Lo que el run espera al terminar el turno.

    Con `stop` (M2 corrió): `awaiting_user → input`, `terminal → none`. Sin `stop` (el turno no avanzó el
    flow): un run cerrado no espera nada, una oferta pendiente es `input` sin nodo, y el resto conserva
    lo que ya esperaba."""
    if stop is not None:
        awaiting = _BY_STOP.get(stop, Awaiting.none)
        if awaiting is Awaiting.none or state.active_flow is None:
            return Awaiting.none, None
        return awaiting, state.active_flow.node_id
    if state.status != "open":
        return Awaiting.none, None
    if state.pending_offer is not None and state.active_flow is None:
        return Awaiting.input, None
    return state.awaiting, state.awaiting_node_id


def build_turn_result(frame: TurnFrame, state: RunState, trace_id: str) -> TurnResult:
    """Los mensajes y el `summary` de la confirmación salen de `runtime.render` (tokens → valores)."""
    confirmation = frame.confirmation
    if confirmation is not None:
        rendered = frame.runtime.render(confirmation.summary)
        confirmation = confirmation.model_copy(update={"summary": rendered})
    return TurnResult(
        run_id=state.run_id,
        turn_id=frame.turn_id,
        messages=[frame.runtime.render(m) for m in frame.messages],
        locale=state.locale,
        awaiting=state.awaiting,
        confirmation=confirmation,
        step_up=frame.step_up,
        status=state.status,
        outcome=state.outcome,
        handoff_ref=state.handoff_ref,
        trace_id=trace_id,
    )
