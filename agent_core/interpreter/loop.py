"""Bucle de M2 (§3.1): ejecuta nodos hasta uno que espera al principal o uno terminal."""

from agent_core.domain import (
    ActiveFlow,
    EngineEvent,
    EntityRef,
    Flow,
    IllegalTransition,
    Message,
    Node,
    RejectedDraft,
    RunState,
    node_kind,
)
from agent_core.interpreter.budgets import enter_node
from agent_core.interpreter.context import NO_RESUME, Resume, StepContext, StepOutcome, Stop
from agent_core.interpreter.events import Events
from agent_core.interpreter.handlers import HANDLERS
from agent_core.interpreter.handlers.base import escalation_request


def _node(flow: Flow, node_id: str) -> Node:
    for node in flow.nodes:
        if node.id == node_id:
            return node
    raise IllegalTransition(f"el flow {flow.id}@{flow.version} no tiene el nodo {node_id}")


def _move(state: RunState, node: Node, key: str) -> RunState:
    target = node.next.get(key)
    if target is None:
        raise IllegalTransition(f"el nodo {node.id} no tiene transición para {key!r}")
    assert state.active_flow is not None
    return state.model_copy(update={"active_flow": state.active_flow.model_copy(update={"node_id": target})})


def start_flow(state: RunState, flow: Flow) -> RunState:
    """Fija `active_flow` en el nodo de entrada (`flow.nodes[0]`) y reinicia `node_attempts` (D5)."""
    active = ActiveFlow(flow=EntityRef(id=flow.id, version=flow.version), node_id=flow.nodes[0].id)
    return state.model_copy(update={"active_flow": active, "node_attempts": {}})


def advance(state: RunState, ctx: StepContext, resume: Resume) -> StepOutcome:
    if state.active_flow is None:
        raise IllegalTransition("advance necesita un flow activo")
    events: list[EngineEvent] = []
    messages: list[Message] = []
    rejected: list[RejectedDraft] = []
    factory = Events(ctx)
    while True:
        active = state.active_flow
        assert active is not None
        flow = ctx.registry.get(active.flow, Flow)
        node = _node(flow, active.node_id)
        state, exhausted = enter_node(state, ctx)
        if exhausted:
            request = escalation_request(ctx, "budget_exceeded")
            return StepOutcome(state, Stop.terminal, messages, events, escalation=request,
                               rejected_drafts=rejected)
        kind = node_kind(node)
        events.append(factory.node_entered(state, active.flow, node.id, kind or "", resume.kind))
        handler = HANDLERS.get(kind or "")
        if handler is None:
            raise IllegalTransition(f"no hay handler para el tipo de nodo {kind!r}")
        result = handler(node, state, ctx, resume)
        resume = NO_RESUME
        state = result.state
        events.extend(result.events)
        messages.extend(result.messages)
        rejected.extend(result.rejected)
        if result.stop is not None:
            if result.result_key is not None:
                state = _move(state, node, result.result_key)
            return StepOutcome(state, result.stop, messages, events, result.end_outcome, result.escalation,
                               result.confirmation, result.step_up, result.output, rejected)
        if result.result_key is None:
            raise IllegalTransition(f"el handler de {node.id} no devolvió rama ni detención")
        state = _move(state, node, result.result_key)
