"""Interfaz pública de M2: contexto, reanudación y resultado de `advance` (M2 §2, D2, D3)."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Literal

from agent_core.actions import ActionManager, EventRecorder, append_events
from agent_core.domain import (
    Agent,
    ConfirmationPrompt,
    EngineEvent,
    EscalationRequest,
    JsonValue,
    Locale,
    Message,
    Outcome,
    RejectedDraft,
    Release,
    RunState,
    StepUpPrompt,
)
from agent_core.interpreter.breaker import CircuitBreaker
from agent_core.interpreter.ports import AgentPort, DecisionPort, ResponderPort
from agent_core.ports import Clock, IdSource, RegistryPort, ToolExecutor, UnitOfWorkFactory
from agent_core.views import TokenVault, ViewService

ResumeKind = Literal["slot_answer", "confirm_answer", "step_up_retry", "none"]


class Stop(StrEnum):
    """Por qué se detiene `advance`. M4 lo traduce a `RunState.awaiting`."""

    awaiting_slot = "awaiting_slot"
    awaiting_confirmation = "awaiting_confirmation"
    awaiting_step_up = "awaiting_step_up"
    awaiting_user = "awaiting_user"
    terminal = "terminal"


@dataclass(frozen=True)
class Resume:
    """Por qué se reanuda el nodo actual. `value`: texto del slot, o "yes"|"no"|"unclear" para confirm."""

    kind: ResumeKind = "none"
    value: JsonValue = None
    token: str | None = None  # token de confirmación del botón (M3 `answer`)


NO_RESUME = Resume()


@dataclass(frozen=True)
class StepContext:
    """Todo lo que M2 necesita del exterior: sin esto, `advance` no hace I/O (M2 §4)."""

    release: Release
    agent: Agent
    locale: Locale
    clock: Clock
    degraded: bool
    registry: RegistryPort
    tools: ToolExecutor
    decisions: DecisionPort
    actions: ActionManager
    responder: ResponderPort
    views: ViewService
    vault: TokenVault
    ids: IdSource
    uow_factory: UnitOfWorkFactory
    bound_params: Mapping[str, str] = field(default_factory=dict)
    agents: AgentPort | None = None  # nodo `agent` (ADR 0019); sin él, un nodo `agent` es error de cableado
    record: EventRecorder = append_events
    turn_id: str | None = None
    breaker: CircuitBreaker = field(default_factory=CircuitBreaker)


@dataclass(frozen=True)
class StepOutcome:
    """Resultado de `advance`. Los eventos de `execute_write` ya están persistidos y no vuelven aquí."""

    state: RunState
    stop: Stop
    messages: list[Message] = field(default_factory=list)
    events: list[EngineEvent] = field(default_factory=list)
    end_outcome: Outcome | None = None
    escalation: EscalationRequest | None = None
    confirmation: ConfirmationPrompt | None = None
    step_up: StepUpPrompt | None = None
    output: dict[str, JsonValue] | None = None  # `end.output_map` en modo task
    rejected_drafts: list[RejectedDraft] = field(default_factory=list)
