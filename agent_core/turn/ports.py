"""Puertos locales de M4 (patrón D1 de M2).

`TurnRecorderPort` y `EventChain` se verificaron contra M11 real (`TurnRecorder.record_turn`,
`AuditLog.append`; `tests/m04/test_real_m11.py`). `UnderstandPort` NO coincide con `UnderstandService.run`
de M5: ver m04 §11 (discrepancias de la Fase B)."""

from collections.abc import Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Literal, Protocol

from agent_core.domain import (
    Agent,
    Command,
    EncryptedBlob,
    EngineEvent,
    EntityRef,
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
    turn_id: str
    step: StepContext  # del `TurnRuntime`: el adaptador de M5 saca de aquí el `TokenVault` del run


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
    model_calls: int = 0  # llamadas `predict` del turno (1.ª y 2.ª llamada de M5)
    tokens: int = 0


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


# --- telemetry of the turn (m04 §3.9) -------------------------------------------------------------------
# M4 never imports OpenTelemetry nor `agent_telemetry` (`.importlinter`): it reports each turn through this
# port. The default is a no-op (`agent_core.turn.telemetry`); the real one lives in composition. Telemetry
# only reads events M4 already built: it never changes an event, a hash or the order of the ids.


@dataclass(frozen=True)
class TurnScope:
    """What a turn's live span is about: correlation ids and closed-list values, never a payload value."""

    run_id: str
    turn_id: str
    session_id: str | None
    release: str
    agent: EntityRef
    entry: Literal["start_run", "turn"]
    principal_type: str
    locale: str


@dataclass(frozen=True)
class TransferOutcome:
    outcome: Literal["transferred", "rejected"]
    to_agent: str | None = None
    to_release_id: str | None = None  # only with `transferred`
    reason_code: str | None = None  # only with `rejected`


class TransferSpan(Protocol):
    link: object | None  # opaque handle for the target turn's `links`; None = nothing to link

    def finish(self, outcome: TransferOutcome) -> None: ...


class TurnSpan(Protocol):
    def record(self, events: Sequence[EngineEvent]) -> None:
        """Events this turn just chained, in chain order; called after every `EventChain.append` of the
        turn."""
        ...

    def transfer(self, transfer_id: str) -> AbstractContextManager[TransferSpan]: ...


class TurnTelemetry(Protocol):
    def turn(self, scope: TurnScope, links: Sequence[object] = ()) -> AbstractContextManager[TurnSpan]: ...
