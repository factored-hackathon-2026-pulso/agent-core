"""`TurnFrame`: lo que un turno va acumulando entre pasos (m04 §3.1). Mutable y de un solo turno."""

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Literal

from agent_core.domain import (
    Agent,
    ConfirmationPrompt,
    JsonValue,
    Message,
    RejectedDraft,
    Release,
    RunState,
    StepUpPrompt,
    TurnInput,
)
from agent_core.interpreter import NO_RESUME, Resume, Stop
from agent_core.ports import UnitOfWork
from agent_core.turn.buffer import EventBuffer
from agent_core.turn.events import TurnEvents
from agent_core.turn.metering import StageMeter
from agent_core.turn.ports import TurnRuntime


@dataclass
class TurnFrame:
    uow: UnitOfWork
    state: RunState
    turn_id: str
    entry: Literal["start_run", "turn"]
    client_turn_id: str | None
    agent: Agent
    release: Release
    runtime: TurnRuntime
    meter: StageMeter
    buffer: EventBuffer
    events: TurnEvents
    turn: TurnInput | None = None
    text_model: str = ""
    messages: list[Message] = field(default_factory=list)
    rejected: list[RejectedDraft] = field(default_factory=list)
    degraded: bool = False
    resume: Resume = NO_RESUME
    confirmation: ConfirmationPrompt | None = None
    step_up: StepUpPrompt | None = None
    stop: Stop | None = None
    cost_usd: Decimal = Decimal("0")
    initial_run_cost: Decimal = Decimal("0")
    closed: bool = False  # el run se cerró o escaló en este turno (ya emitió `run_closed`)
    advanced: bool = False  # `advance` corrió en este turno
    output: dict[str, JsonValue] | None = None  # `end.output_map` (modo task)
