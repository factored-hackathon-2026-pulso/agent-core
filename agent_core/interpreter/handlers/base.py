"""Tipos y ayudas comunes de los handlers (M2 §2)."""

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from agent_core.domain import (
    AuthLevel,
    ConfirmationPrompt,
    EngineEvent,
    EscalationRequest,
    JsonValue,
    Message,
    Outcome,
    RejectedDraft,
    RunState,
    StepUpPrompt,
)
from agent_core.interpreter.context import Resume, StepContext, Stop
from agent_core.interpreter.events import Events

DEFAULT_PRIORITY = "normal"


@dataclass
class NodeResult:
    """Salida de un handler: rama que sigue (`result_key`), por qué se detiene (`stop`), o ambas (D16)."""

    state: RunState
    result_key: str | None = None
    stop: Stop | None = None
    messages: list[Message] = field(default_factory=list)
    events: list[EngineEvent] = field(default_factory=list)
    end_outcome: Outcome | None = None
    escalation: EscalationRequest | None = None
    confirmation: ConfirmationPrompt | None = None
    step_up: StepUpPrompt | None = None
    output: dict[str, JsonValue] | None = None
    rejected: list[RejectedDraft] = field(default_factory=list)


NodeHandler = Callable[[Any, RunState, StepContext, Resume], NodeResult]


def rule_data(state: RunState) -> dict[str, JsonValue]:
    """Datos de `rule`/`priority_expr`: hechos `full` y slots `validated`. Nunca `decisions` ni `claimed`."""
    slots: dict[str, JsonValue] = {
        name: s.value for name, s in state.slots.items() if s.status == "validated"}
    facts: dict[str, JsonValue] = {name: {"value": f.value} for name, f in state.facts.items()}
    return {"slots": slots, "facts": facts}


def escalation_request(ctx: StepContext, reason: str, target_queue: str | None = None,
                       priority: str = DEFAULT_PRIORITY) -> EscalationRequest:
    return EscalationRequest(reason_code=reason, target_queue=target_queue or ctx.agent.default_target_queue,
                             priority=priority)


def escalate_now(state: RunState, ctx: StepContext, reason: str,
                 events: list[EngineEvent] | None = None) -> NodeResult:
    """Escalamiento originado por el motor (no un nodo `escalate`): presupuesto, step-up, ruta (D12, D13)."""
    return NodeResult(state, stop=Stop.terminal, escalation=escalation_request(ctx, reason),
                      events=list(events or []))


def clear_attempts(state: RunState, node_id: str) -> RunState:
    if node_id not in state.node_attempts:
        return state
    return state.model_copy(update={"node_attempts": {k: v for k, v in state.node_attempts.items()
                                                      if k != node_id}})


def request_step_up(state: RunState, ctx: StepContext, node_id: str, level: AuthLevel, max_attempts: int,
                    events: list[EngineEvent]) -> NodeResult:
    """M2 §3.4 (ADR 0010, D9): el nodo no avanza; al superar `max_attempts` escala `auth_insufficient`."""
    attempts = state.node_attempts.get(node_id, 0) + 1
    state = state.model_copy(update={"node_attempts": {**state.node_attempts, node_id: attempts}})
    emitted = [*events, Events(ctx).step_up_requested(state, node_id, level, attempts)]
    if attempts > max_attempts:
        return escalate_now(state, ctx, "auth_insufficient", emitted)
    prompt = StepUpPrompt(required_level=level, reason=f"requires_{level.value}")
    return NodeResult(state, stop=Stop.awaiting_step_up, step_up=prompt, events=emitted)
