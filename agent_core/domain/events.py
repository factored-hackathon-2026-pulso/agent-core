"""Eventos del motor (M0 §2.10). Payloads en vista `audit`. `seq`/`prev_hash`/`hash` los asigna M11."""

from collections.abc import Mapping
from datetime import timedelta
from decimal import Decimal
from enum import StrEnum
from types import MappingProxyType
from typing import Annotated, Literal

from pydantic import Field, NonNegativeInt, PositiveInt

from agent_core.domain.base import Locale, Model, NodeId, Probability, Sha256Hex, UtcDatetime
from agent_core.domain.identity import AuthLevel, PrincipalType
from agent_core.domain.json import JsonValue
from agent_core.domain.outcomes import Awaiting, Command, Mode, Outcome, ReasonCodeStr
from agent_core.domain.refs import EntityRef
from agent_core.domain.shared import Fingerprint, ToolStatus
from agent_core.domain.state import InvalidationReason

Cost = Annotated[Decimal, Field(ge=0, allow_inf_nan=False)]


class EngineEvent(Model):
    """Base de todo evento de la cadena."""

    event_id: str
    run_id: str
    turn_id: str | None = None
    session_id: str | None = None
    release: str
    ts: UtcDatetime
    seq: NonNegativeInt | None = None
    prev_hash: Sha256Hex | None = None
    hash: Sha256Hex | None = None


# --- payloads -----------------------------------------------------------------------------------------------


class RunStartedPayload(Model):
    """Payload del evento `run_started` (vista audit, M0 §2.10)."""
    agent: EntityRef
    mode: Mode
    subject_kind: str | None = None
    principal_type: PrincipalType
    locale: Locale
    reportable_attrs: dict[str, str] = Field(default_factory=dict)


class LangScore(Model):
    """Idioma candidato con su puntuación."""
    lang: str
    score: Probability


class LangGuard(Model):
    """Resultado de la detección de idioma del turno (M0 §2.10)."""
    detector: str
    letters: NonNegativeInt
    top2: list[LangScore]
    decision: Literal["kept", "switched", "short", "undetermined", "unsupported"]
    locale_prior: Locale | None = None
    locale: Locale


class InjectionGuard(Model):
    """Resultado del detector de inyección: señales y versión del ruleset (M0 §2.10)."""
    flagged: bool
    signals: list[str] = Field(default_factory=list)
    ruleset: str


class GuardsOutput(Model):
    """Salida de los guardarraíles de entrada (idioma, inyección, tamaño) de un turno (M0 §2.10)."""
    lang: LangGuard
    injection: InjectionGuard
    size_ok: bool


class TurnStartedPayload(Model):
    """Payload del evento `turn_started` (vista audit, M0 §2.10)."""
    client_turn_id: str | None = None
    guards: GuardsOutput | None = None


class CommandEmittedPayload(Model):
    """Payload del evento `command_emitted` (vista audit, M0 §2.10)."""
    command: Command
    flow: str | None = None
    interrupt: str | None = None
    additional_flows: list[str] = Field(default_factory=list)
    above_threshold: dict[str, bool] = Field(default_factory=dict)
    decision_id: str | None = None
    source: Literal["understand", "button"]


class NodeEnteredPayload(Model):
    """Payload del evento `node_entered` (vista audit, M0 §2.10)."""
    flow: EntityRef
    node_id: NodeId
    node_type: str
    resume_kind: Literal["slot_answer", "confirm_answer", "step_up_retry", "none"]


class LabelScore(Model):
    """Etiqueta con su probabilidad (top-k de una decisión)."""
    label: str
    p: Probability


class DecisionMadePayload(Model):
    """Payload del evento `decision_made` (vista audit, M0 §2.10)."""
    decision_id: str
    model: EntityRef
    provider_used: str
    model_version: str
    fallback_depth: NonNegativeInt
    value: dict[str, JsonValue]
    p_cal: dict[str, Probability | None]
    p_raw: dict[str, Probability | None]
    top_k: dict[str, list[LabelScore]] = Field(default_factory=dict)
    above_threshold: dict[str, bool]
    latency_ms: NonNegativeInt
    tokens: NonNegativeInt
    cost_usd: Cost
    locale: Locale


class RuleEvaluatedPayload(Model):
    """Payload del evento `rule_evaluated` (vista audit, M0 §2.10)."""
    node_id: NodeId
    policy: EntityRef | None = None
    inputs: dict[str, JsonValue]
    result: bool


class ToolCalledPayload(Model):
    """Payload del evento `tool_called` (vista audit, M0 §2.10)."""
    node_id: NodeId
    tool: EntityRef
    call_id: str
    status: ToolStatus
    args: dict[str, JsonValue]
    result: JsonValue = None
    result_fp: Fingerprint | None = None
    error: str | None = None
    attempt: PositiveInt = 1
    action_id: str | None = None
    latency_ms: NonNegativeInt


class StepUpRequestedPayload(Model):
    """Payload del evento `step_up_requested` (vista audit, M0 §2.10)."""
    node_id: NodeId
    required_level: AuthLevel
    attempt: PositiveInt


class ActionConfirmedPayload(Model):
    """Payload del evento `action_confirmed` (vista audit, M0 §2.10)."""
    action_id: str
    source: Literal["understand", "button"]


class ActionCancelledPayload(Model):
    """Payload del evento `action_cancelled` (vista audit, M0 §2.10)."""
    action_id: str
    reason: InvalidationReason


class ActionDispatchedPayload(Model):
    """Payload del evento `action_dispatched` (vista audit, M0 §2.10)."""
    action_id: str
    tool: EntityRef
    args_hash: Sha256Hex


class ActionVerifiedPayload(Model):
    """Payload del evento `action_verified` (vista audit, M0 §2.10)."""
    action_id: str
    result: Literal["verified", "failed"]
    readback_call_id: str


class ExpiryEvaluatedPayload(Model):
    """Payload del evento `expiry_evaluated` (vista audit, M0 §2.10)."""
    now: UtcDatetime
    last_activity_at: UtcDatetime
    ttl: timedelta
    expired: bool


class ValidatorOutcome(Model):
    """Resultado del validador de respuestas (M8): fallas y regeneraciones (M0 §2.10)."""
    ok: bool
    failures: list[str] = Field(default_factory=list)
    regenerations: NonNegativeInt = 0


class LlmUsage(Model):
    """Uso agregado del LLM en una respuesta: llamadas, latencia, tokens y costo (M0 §2.10)."""
    calls: NonNegativeInt
    latency_ms: NonNegativeInt
    tokens_in: NonNegativeInt
    tokens_out: NonNegativeInt
    cost_usd: Cost
    cost_known: bool  # false si alguna llamada no informó uso
    models: list[str] = Field(default_factory=list)


class ResponseEmittedPayload(Model):
    """Payload del evento `response_emitted` (vista audit, M0 §2.10)."""
    node_id: NodeId | None = None
    kind: Literal["template", "generated"]
    validator: ValidatorOutcome
    fallback_used: bool
    claims: list[str] = Field(default_factory=list)
    transcript_fp: Fingerprint | None = None
    llm: LlmUsage | None = None


class ResponseFailedPayload(Model):
    """Payload del evento `response_failed` (vista audit, M0 §2.10): `respond(generate)` terminó en
    `EscalationRequest` y no hubo `response_emitted`. Deja auditado el uso del LLM de esa cadena.
    Nunca lleva el texto de los borradores."""
    node_id: NodeId | None = None
    reason_code: Literal["validation_failed"]
    validator: ValidatorOutcome
    claims: list[str] = Field(default_factory=list)
    llm: LlmUsage | None = None


class TurnStages(Model):
    """Duración por etapa del turno, en milisegundos (M0 §2.10)."""
    guards_ms: NonNegativeInt | None = None
    understand_ms: NonNegativeInt | None = None
    flow_ms: NonNegativeInt | None = None
    response_ms: NonNegativeInt | None = None


class TurnCompletedPayload(Model):
    """Payload del evento `turn_completed` (vista audit, M0 §2.10)."""
    client_turn_id: str | None = None
    entry: Literal["start_run", "turn"]
    duration_ms: NonNegativeInt
    stages: TurnStages
    degraded: bool
    awaiting: Awaiting


class InjectionFlaggedPayload(Model):
    """Payload del evento `injection_flagged` (vista audit, M0 §2.10)."""
    signals: list[str]
    ruleset: str
    scope: Literal["user_text", "untrusted_field"]


class AccessDeniedReason(StrEnum):
    """Motivos por los que se deniega un acceso (M0 §2.10)."""
    principal_expired = "principal_expired"
    delegation_expired = "delegation_expired"
    delegation_mismatch = "delegation_mismatch"
    principal_mismatch = "principal_mismatch"
    subject_forbidden = "subject_forbidden"
    agent_forbidden = "agent_forbidden"
    tool_denied = "tool_denied"


class AccessDeniedPayload(Model):
    """Payload del evento `access_denied` (vista audit, M0 §2.10)."""
    reason: AccessDeniedReason
    tool: EntityRef | None = None


class EscalatedPayload(Model):
    """Payload del evento `escalated` (vista audit, M0 §2.10)."""
    reason_code: ReasonCodeStr
    target_queue: str
    priority: str
    handoff_ref: str


class HandoffResolvedPayload(Model):
    """Payload del evento `handoff_resolved` (vista audit, M0 §2.10)."""
    handoff_ref: str
    resolution_code: str
    handoff_quality: Literal["useful", "incomplete", "unnecessary"]
    reader_type: PrincipalType


class RunClosedPayload(Model):
    """Payload del evento `run_closed` (vista audit, M0 §2.10)."""
    outcome: Outcome
    closed_by: Literal["flow", "abandonment", "escalation", "revocation"]


class HandoffCreatedPayload(Model):
    """Evento saliente (outbox, no va a la cadena)."""

    handoff_ref: str
    run_id: str
    target_queue: str
    priority: str
    reason_code: ReasonCodeStr
    language: Locale
    reportable_attrs: dict[str, str] = Field(default_factory=dict)


# --- eventos ---------------------------------------------------------------------------------------------


class RunStarted(EngineEvent):
    """Evento `run_started` de la cadena de auditoría (M0 §2.10)."""
    type: Literal["run_started"] = "run_started"
    payload: RunStartedPayload


class TurnStarted(EngineEvent):
    """Evento `turn_started` de la cadena de auditoría (M0 §2.10)."""
    type: Literal["turn_started"] = "turn_started"
    payload: TurnStartedPayload


class CommandEmitted(EngineEvent):
    """Evento `command_emitted` de la cadena de auditoría (M0 §2.10)."""
    type: Literal["command_emitted"] = "command_emitted"
    payload: CommandEmittedPayload


class NodeEntered(EngineEvent):
    """Evento `node_entered` de la cadena de auditoría (M0 §2.10)."""
    type: Literal["node_entered"] = "node_entered"
    payload: NodeEnteredPayload


class DecisionMade(EngineEvent):
    """Evento `decision_made` de la cadena de auditoría (M0 §2.10)."""
    type: Literal["decision_made"] = "decision_made"
    payload: DecisionMadePayload


class RuleEvaluated(EngineEvent):
    """Evento `rule_evaluated` de la cadena de auditoría (M0 §2.10)."""
    type: Literal["rule_evaluated"] = "rule_evaluated"
    payload: RuleEvaluatedPayload


class ToolCalled(EngineEvent):
    """Evento `tool_called` de la cadena de auditoría (M0 §2.10)."""
    type: Literal["tool_called"] = "tool_called"
    payload: ToolCalledPayload


class StepUpRequested(EngineEvent):
    """Evento `step_up_requested` de la cadena de auditoría (M0 §2.10)."""
    type: Literal["step_up_requested"] = "step_up_requested"
    payload: StepUpRequestedPayload


class ActionConfirmed(EngineEvent):
    """Evento `action_confirmed` de la cadena de auditoría (M0 §2.10)."""
    type: Literal["action_confirmed"] = "action_confirmed"
    payload: ActionConfirmedPayload


class ActionCancelled(EngineEvent):
    """Evento `action_cancelled` de la cadena de auditoría (M0 §2.10)."""
    type: Literal["action_cancelled"] = "action_cancelled"
    payload: ActionCancelledPayload


class ActionDispatched(EngineEvent):
    """Evento `action_dispatched` de la cadena de auditoría (M0 §2.10)."""
    type: Literal["action_dispatched"] = "action_dispatched"
    payload: ActionDispatchedPayload


class ActionVerified(EngineEvent):
    """Evento `action_verified` de la cadena de auditoría (M0 §2.10)."""
    type: Literal["action_verified"] = "action_verified"
    payload: ActionVerifiedPayload


class ExpiryEvaluated(EngineEvent):
    """Evento `expiry_evaluated` de la cadena de auditoría (M0 §2.10)."""
    type: Literal["expiry_evaluated"] = "expiry_evaluated"
    payload: ExpiryEvaluatedPayload


class ResponseEmitted(EngineEvent):
    """Evento `response_emitted` de la cadena de auditoría (M0 §2.10)."""
    type: Literal["response_emitted"] = "response_emitted"
    payload: ResponseEmittedPayload


class ResponseFailed(EngineEvent):
    """Evento `response_failed` de la cadena de auditoría (M0 §2.10)."""
    type: Literal["response_failed"] = "response_failed"
    payload: ResponseFailedPayload


class TurnCompleted(EngineEvent):
    """Evento `turn_completed` de la cadena de auditoría (M0 §2.10)."""
    type: Literal["turn_completed"] = "turn_completed"
    payload: TurnCompletedPayload


class InjectionFlagged(EngineEvent):
    """Evento `injection_flagged` de la cadena de auditoría (M0 §2.10)."""
    type: Literal["injection_flagged"] = "injection_flagged"
    payload: InjectionFlaggedPayload


class AccessDenied(EngineEvent):
    """Evento `access_denied` de la cadena de auditoría (M0 §2.10)."""
    type: Literal["access_denied"] = "access_denied"
    payload: AccessDeniedPayload


class Escalated(EngineEvent):
    """Evento `escalated` de la cadena de auditoría (M0 §2.10)."""
    type: Literal["escalated"] = "escalated"
    payload: EscalatedPayload


class HandoffResolved(EngineEvent):
    """Evento `handoff_resolved` de la cadena de auditoría (M0 §2.10)."""
    type: Literal["handoff_resolved"] = "handoff_resolved"
    payload: HandoffResolvedPayload


class RunClosed(EngineEvent):
    """Evento `run_closed` de la cadena de auditoría (M0 §2.10)."""
    type: Literal["run_closed"] = "run_closed"
    payload: RunClosedPayload


AnyEvent = Annotated[
    RunStarted
    | TurnStarted
    | CommandEmitted
    | NodeEntered
    | DecisionMade
    | RuleEvaluated
    | ToolCalled
    | StepUpRequested
    | ActionConfirmed
    | ActionCancelled
    | ActionDispatched
    | ActionVerified
    | ExpiryEvaluated
    | ResponseEmitted
    | ResponseFailed
    | TurnCompleted
    | InjectionFlagged
    | AccessDenied
    | Escalated
    | HandoffResolved
    | RunClosed,
    Field(discriminator="type"),
]

_EVENT_CLASSES: tuple[type[EngineEvent], ...] = (
    RunStarted, TurnStarted, CommandEmitted, NodeEntered, DecisionMade, RuleEvaluated, ToolCalled,
    StepUpRequested, ActionConfirmed, ActionCancelled, ActionDispatched, ActionVerified, ExpiryEvaluated,
    ResponseEmitted, ResponseFailed, TurnCompleted, InjectionFlagged, AccessDenied, Escalated,
    HandoffResolved, RunClosed,
)

EVENT_TYPES: Mapping[str, type[EngineEvent]] = MappingProxyType(
    {cls.model_fields["type"].default: cls for cls in _EVENT_CLASSES}
)

EVENT_EMITTERS: Mapping[str, frozenset[str]] = MappingProxyType(
    {
        "run_started": frozenset({"M4"}),
        "turn_started": frozenset({"M4"}),
        "command_emitted": frozenset({"M4"}),
        "expiry_evaluated": frozenset({"M4"}),
        "turn_completed": frozenset({"M4"}),
        "run_closed": frozenset({"M4"}),
        "node_entered": frozenset({"M2"}),
        "rule_evaluated": frozenset({"M2"}),
        "step_up_requested": frozenset({"M2"}),
        "tool_called": frozenset({"M2", "M3"}),
        "decision_made": frozenset({"M5"}),
        "action_confirmed": frozenset({"M3"}),
        "action_cancelled": frozenset({"M3"}),
        "action_dispatched": frozenset({"M3"}),
        "action_verified": frozenset({"M3"}),
        "response_emitted": frozenset({"M8"}),
        "response_failed": frozenset({"M8"}),
        "injection_flagged": frozenset({"M6"}),
        "access_denied": frozenset({"M9", "M2"}),
        "escalated": frozenset({"M10"}),
        "handoff_resolved": frozenset({"M10"}),
    }
)

MEASURED_FIELDS: Mapping[str, frozenset[str]] = MappingProxyType(
    {
        "decision_made": frozenset({"latency_ms"}),
        "tool_called": frozenset({"latency_ms"}),
        "response_emitted": frozenset({"llm"}),
        "response_failed": frozenset({"llm"}),
        "turn_completed": frozenset({"duration_ms", "stages"}),
    }
)
