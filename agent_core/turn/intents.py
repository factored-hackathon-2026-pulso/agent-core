"""Intenciones pendientes y ofertas (m04 §3.3, ADR 0004)."""

from agent_core.domain import Awaiting
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
