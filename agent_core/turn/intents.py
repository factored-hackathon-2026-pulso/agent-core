"""Intenciones pendientes y ofertas (m04 §3.3, ADR 0004)."""

from agent_core.domain import (
    Awaiting,
    Command,
    EntityKind,
    EntityRef,
    Flow,
    PendingIntent,
    Release,
    RunState,
    Slot,
)
from agent_core.ports import RegistryPort
from agent_core.turn.frame import TurnFrame
from agent_core.turn.ports import UnderstandOutcome
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


def mentioned_flows(
    registry: RegistryPort, release: Release, outcome: UnderstandOutcome
) -> list[tuple[Flow, int]]:
    """Flows nombrados por Understand con su orden de mención: `flow` (solo con `start_flow`) y
    `additional_flows`. Sin repetidos; los que la release no fija se ignoran. Orden: `priority` desc y,
    en empate, `mention_order` asc (ADR 0004)."""
    names = [outcome.flow] if outcome.command is Command.start_flow and outcome.flow else []
    names += outcome.additional_flows
    seen: list[str] = []
    for name in names:
        if name not in seen:
            seen.append(name)
    pins = release.entities.get(EntityKind.flow, {})
    found = [
        (registry.get(EntityRef(id=name, version=pins[name]), Flow), order)
        for order, name in enumerate(seen)
        if name in pins
    ]
    return sorted(found, key=lambda item: (-item[0].priority, item[1]))


def queue_mentioned(
    registry: RegistryPort, frame: TurnFrame, outcome: UnderstandOutcome, *, skip: str | None = None
) -> int:
    """Manda a `pending_intents` los flows mencionados (salvo `skip`); devuelve cuántos quedaron nuevos."""
    intents = [
        PendingIntent(flow=flow.id, priority=flow.priority, mention_order=order)
        for flow, order in mentioned_flows(registry, frame.release, outcome)
        if flow.id != skip
    ]
    before = len(frame.state.pending_intents)
    frame.state = add_pending(frame.state, intents)
    return len(frame.state.pending_intents) - before


def claim_slots(frame: TurnFrame, outcome: UnderstandOutcome) -> None:
    """Los slots que trae Understand entran `claimed` (nunca hechos; M2 los lee como ausentes hasta que un
    `collect` los valide). Un slot ya validado no se pisa."""
    if not outcome.slots:
        return
    state = frame.state
    slots = dict(state.slots)
    for name, value in outcome.slots.items():
        existing = slots.get(name)
        if existing is not None and existing.status == "validated":
            continue
        slots[name] = Slot(value=value, status="claimed", source_turn=state.turn_count)
    frame.state = state.model_copy(update={"slots": slots})


def flow_by_id(registry: RegistryPort, release: Release, flow_id: str) -> Flow:
    return registry.get(EntityRef(id=flow_id, version=release.entities[EntityKind.flow][flow_id]), Flow)


def answer_offer(frame: TurnFrame, registry: RegistryPort, outcome: UnderstandOutcome) -> Flow | None:
    """Respuesta a la oferta de una intención pendiente. Solo `affirm` sobre umbral la arranca."""
    offered = frame.state.pending_offer
    assert offered is not None
    affirmed = outcome.command is Command.affirm and outcome.above_threshold.get("command", False)
    if affirmed:
        return flow_by_id(registry, frame.release, offered)
    raise NotImplementedError("deny y respuestas ajenas a la oferta: Task 14b (P1)")
