"""Puertos locales de M4 (patrón D1 de M2).

`TurnRecorderPort` y `EventChain` se verificaron contra M11 real (`TurnRecorder.record_turn`,
`AuditLog.append`; `tests/m04/test_real_m11.py`). `UnderstandPort` NO coincide con `UnderstandService.run`
de M5: ver m04 §11 (discrepancias de la Fase B)."""

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Protocol

from agent_core.domain import (
    Agent,
    Command,
    EncryptedBlob,
    EngineEvent,
    JsonValue,
    Locale,
    Message,
    NodeId,
    OnBehalfOf,
    Principal,
    RejectedDraft,
    Release,
    RunState,
    TranscriptRef,
)
from agent_core.guards import GuardResult
from agent_core.interpreter import StepContext
from agent_core.ports import UnitOfWork


@dataclass(frozen=True)
class UnderstandRequest:
    text_model: str  # vista `model` (C1)
    state: RunState
    release: Release
    agent: Agent
    locale: Locale
    awaiting_confirmation: bool  # hay un confirm pendiente (m05 §3.2)
    current_node: NodeId | None


@dataclass(frozen=True)
class UnderstandOutcome:
    command: Command
    flow: str | None = None
    interrupt: str | None = None
    additional_flows: list[str] = field(default_factory=list)
    slots: dict[str, JsonValue] = field(default_factory=dict)
    above_threshold: dict[str, bool] = field(default_factory=dict)  # solo campos calibrados
    decision_id: str | None = None
    events: list[EngineEvent] = field(default_factory=list)  # `decision_made`, lo emite M5
    cost_usd: Decimal = Decimal("0")


class UnderstandPort(Protocol):
    def run(self, request: UnderstandRequest) -> UnderstandOutcome: ...


class TurnRecorderPort(Protocol):
    def record_turn(
        self,
        run_id: str,
        turn_id: str,
        user_msg_model: str,
        final_model: str,
        rejected: list[RejectedDraft],
    ) -> list[TranscriptRef]:
        """Orden (M11): entrada del usuario, borradores rechazados, respuesta final."""
        ...


class EventChain(Protocol):
    def append(self, uow: UnitOfWork, run_id: str, events: list[EngineEvent]) -> object:
        """Encadena (seq, prev_hash, hash) y persiste con `uow.append_events`. El retorno se ignora (M11
        devuelve los eventos encadenados)."""
        ...


class GuardsPort(Protocol):
    """Forma de `guards.GuardService.run` (verificada)."""

    def run(
        self,
        text_model_view: str,
        state: RunState,
        agent: Agent,
        release: Release,
        request_lang: str | None,
        first_turn: bool,
        *,
        turn_id: str | None = None,
    ) -> tuple[GuardResult, list[EngineEvent]]: ...


class TurnRuntime(Protocol):
    step: StepContext  # base: M4 la ajusta por turno con `dataclasses.replace`

    def model_text(self, text: str) -> str:
        """M7: PII del usuario → tokens del run."""
        ...

    def render(self, message: Message) -> Message:
        """M7: tokens → valores para el principal."""
        ...

    def sealed_token_map(self) -> EncryptedBlob | None:
        """M7: `vault.seal()` si cambió."""
        ...


class RuntimeFactory(Protocol):
    def open(self, state: RunState, principal: Principal, on_behalf_of: OnBehalfOf | None) -> TurnRuntime: ...


class TraceIds(Protocol):
    def current(self, turn_id: str) -> str: ...
