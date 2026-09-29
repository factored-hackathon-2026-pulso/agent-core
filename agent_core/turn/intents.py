"""Intenciones pendientes y ofertas (m04 §3.3, ADR 0004)."""

from agent_core.domain import Awaiting, PendingIntent, RunState
from agent_core.ports import RegistryPort
from agent_core.turn.frame import TurnFrame
from agent_core.turn.templates import render_engine


def offer_next(frame: TurnFrame, registry: RegistryPort) -> bool:
    """Ofrece la primera intención pendiente: `pending_offer`, `awaiting=input`, sin flow activo.

    Devuelve `False` (sin tocar nada) si no hay pendientes."""
    state = frame.state
    if not state.pending_intents:
        return False
    first, rest = state.pending_intents[0], state.pending_intents[1:]
    message = render_engine(registry, frame.release, frame.agent.templates.pending_offer, state.locale)
    frame.messages.append(message)
    frame.state = state.model_copy(
        update={
            "pending_intents": rest,
            "pending_offer": first.flow,
            "active_flow": None,
            "awaiting": Awaiting.input,
            "awaiting_node_id": None,
        }
    )
    return True


def clear_flow(state: RunState) -> RunState:
    """Sin flow activo ni espera (cancelación, oferta descartada)."""
    return state.model_copy(
        update={
            "active_flow": None,
            "awaiting": Awaiting.none,
            "awaiting_node_id": None,
            "pending_offer": None,
            "node_attempts": {},
        }
    )


def add_pending(state: RunState, intents: list[PendingIntent]) -> RunState:
    """Suma intenciones y ordena por `priority` desc y, en empate, por `mention_order` (ADR 0004).

    Ignora las que ya están pendientes, ofrecidas o activas."""
    known = {p.flow for p in state.pending_intents}
    if state.pending_offer is not None:
        known.add(state.pending_offer)
    if state.active_flow is not None:
        known.add(state.active_flow.flow.id)
    fresh = [i for i in intents if i.flow not in known]
    merged = sorted([*state.pending_intents, *fresh], key=lambda i: (-i.priority, i.mention_order))
    return state.model_copy(update={"pending_intents": merged})
