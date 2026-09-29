"""Eventos del motor (M0 §2.10). Payloads en vista `audit`. `seq`/`prev_hash`/`hash` los asigna M11."""

from collections.abc import Mapping
from datetime import timedelta
from decimal import Decimal
from enum import StrEnum
from types import MappingProxyType
from typing import Annotated, Literal

from pydantic import Field, NonNegativeInt, PositiveInt, StringConstraints

from agent_core.domain.base import Locale, Model, NodeId, Probability, UtcDatetime
from agent_core.domain.identity import AuthLevel, PrincipalType
from agent_core.domain.json import JsonValue
from agent_core.domain.outcomes import Awaiting, Command, Mode, Outcome, ReasonCodeStr
from agent_core.domain.refs import EntityRef
from agent_core.domain.shared import Fingerprint, ToolStatus
from agent_core.domain.state import InvalidationReason

# sha256 en hex minúscula (ASCII, fullmatch implícito de pydantic con ^…$ sin salto final)
Sha256Hex = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
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
    agent: EntityRef
    mode: Mode
    subject_kind: str | None = None
    principal_type: PrincipalType
    locale: Locale
    reportable_attrs: dict[str, str] = Field(default_factory=dict)


class LangScore(Model):
    lang: str
    score: Probability


class LangGuard(Model):
    detector: str
    letters: NonNegativeInt
    top2: list[LangScore]
    decision: Literal["kept", "switched", "short", "undetermined", "unsupported"]
    locale_prior: Locale | None = None
    locale: Locale


class InjectionGuard(Model):
    flagged: bool
    signals: list[str] = Field(default_factory=list)
    ruleset: str


class GuardsOutput(Model):
    lang: LangGuard
    injection: InjectionGuard
    size_ok: bool


class TurnStartedPayload(Model):
    client_turn_id: str | None = None
    guards: GuardsOutput | None = None


class CommandEmittedPayload(Model):
    command: Command
    flow: str | None = None
    interrupt: str | None = None
    additional_flows: list[str] = Field(default_factory=list)
    above_threshold: dict[str, bool] = Field(default_factory=dict)
    decision_id: str | None = None
    source: Literal["understand", "button"]


class NodeEnteredPayload(Model):
    flow: EntityRef
    node_id: NodeId
    node_type: str
    resume_kind: Literal["slot_answer", "confirm_answer", "step_up_retry", "none"]


class LabelScore(Model):
    label: str
    p: Probability


class DecisionMadePayload(Model):
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
    node_id: NodeId
    policy: EntityRef | None = None
    inputs: dict[str, JsonValue]
    result: bool


class ToolCalledPayload(Model):
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
    node_id: NodeId
    required_level: AuthLevel
    attempt: PositiveInt


class ActionConfirmedPayload(Model):
    action_id: str
    source: Literal["understand", "button"]


class ActionCancelledPayload(Model):
    action_id: str
    reason: InvalidationReason


class ActionDispatchedPayload(Model):
    action_id: str
    tool: EntityRef
    args_hash: Sha256Hex


class ActionVerifiedPayload(Model):
    action_id: str
    result: Literal["verified", "failed"]
    readback_call_id: str


class ExpiryEvaluatedPayload(Model):
    now: UtcDatetime
    last_activity_at: UtcDatetime
    ttl: timedelta
    expired: bool


class ValidatorOutcome(Model):
    ok: bool
    failures: list[str] = Field(default_factory=list)
    regenerations: NonNegativeInt = 0


class LlmUsage(Model):
    calls: NonNegativeInt
    latency_ms: NonNegativeInt
    tokens_in: NonNegativeInt
    tokens_out: NonNegativeInt
    cost_usd: Cost
    cost_known: bool  # false si alguna llamada no informó uso
    models: list[str] = Field(default_factory=list)


class ResponseEmittedPayload(Model):
    node_id: NodeId | None = None
    kind: Literal["template", "generated"]
    validator: ValidatorOutcome
    fallback_used: bool
    claims: list[str] = Field(default_factory=list)
    transcript_fp: Fingerprint | None = None
    llm: LlmUsage | None = None


class TurnStages(Model):
    guards_ms: NonNegativeInt | None = None
    understand_ms: NonNegativeInt | None = None
    flow_ms: NonNegativeInt | None = None
    response_ms: NonNegativeInt | None = None


class TurnCompletedPayload(Model):
    client_turn_id: str | None = None
    entry: Literal["start_run", "turn"]
    duration_ms: NonNegativeInt
    stages: TurnStages
    degraded: bool
    awaiting: Awaiting


class InjectionFlaggedPayload(Model):
    signals: list[str]
    ruleset: str
    scope: Literal["user_text", "untrusted_field"]


class AccessDeniedReason(StrEnum):
    principal_expired = "principal_expired"
    delegation_expired = "delegation_expired"
    delegation_mismatch = "delegation_mismatch"
    principal_mismatch = "principal_mismatch"
    subject_forbidden = "subject_forbidden"
    agent_forbidden = "agent_forbidden"
    tool_denied = "tool_denied"


class AccessDeniedPayload(Model):
    reason: AccessDeniedReason
    tool: EntityRef | None = None


class EscalatedPayload(Model):
    reason_code: ReasonCodeStr
    target_queue: str
    priority: str
    handoff_ref: str


class HandoffResolvedPayload(Model):
    handoff_ref: str
    resolution_code: str
    handoff_quality: Literal["useful", "incomplete", "unnecessary"]
    reader_type: PrincipalType


class RunClosedPayload(Model):
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
    type: Literal["run_started"] = "run_started"
    payload: RunStartedPayload


class TurnStarted(EngineEvent):
    type: Literal["turn_started"] = "turn_started"
    payload: TurnStartedPayload


class CommandEmitted(EngineEvent):
    type: Literal["command_emitted"] = "command_emitted"
    payload: CommandEmittedPayload


class NodeEntered(EngineEvent):
    type: Literal["node_entered"] = "node_entered"
    payload: NodeEnteredPayload


class DecisionMade(EngineEvent):
    type: Literal["decision_made"] = "decision_made"
    payload: DecisionMadePayload


class RuleEvaluated(EngineEvent):
    type: Literal["rule_evaluated"] = "rule_evaluated"
    payload: RuleEvaluatedPayload


class ToolCalled(EngineEvent):
    type: Literal["tool_called"] = "tool_called"
    payload: ToolCalledPayload


class StepUpRequested(EngineEvent):
    type: Literal["step_up_requested"] = "step_up_requested"
    payload: StepUpRequestedPayload


class ActionConfirmed(EngineEvent):
    type: Literal["action_confirmed"] = "action_confirmed"
    payload: ActionConfirmedPayload


class ActionCancelled(EngineEvent):
    type: Literal["action_cancelled"] = "action_cancelled"
    payload: ActionCancelledPayload


class ActionDispatched(EngineEvent):
    type: Literal["action_dispatched"] = "action_dispatched"
    payload: ActionDispatchedPayload


class ActionVerified(EngineEvent):
    type: Literal["action_verified"] = "action_verified"
    payload: ActionVerifiedPayload


class ExpiryEvaluated(EngineEvent):
    type: Literal["expiry_evaluated"] = "expiry_evaluated"
    payload: ExpiryEvaluatedPayload


class ResponseEmitted(EngineEvent):
    type: Literal["response_emitted"] = "response_emitted"
    payload: ResponseEmittedPayload


class TurnCompleted(EngineEvent):
    type: Literal["turn_completed"] = "turn_completed"
    payload: TurnCompletedPayload


class InjectionFlagged(EngineEvent):
    type: Literal["injection_flagged"] = "injection_flagged"
    payload: InjectionFlaggedPayload


class AccessDenied(EngineEvent):
    type: Literal["access_denied"] = "access_denied"
    payload: AccessDeniedPayload


class Escalated(EngineEvent):
    type: Literal["escalated"] = "escalated"
    payload: EscalatedPayload


class HandoffResolved(EngineEvent):
    type: Literal["handoff_resolved"] = "handoff_resolved"
    payload: HandoffResolvedPayload


class RunClosed(EngineEvent):
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
    ResponseEmitted, TurnCompleted, InjectionFlagged, AccessDenied, Escalated, HandoffResolved, RunClosed,
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
        "turn_completed": frozenset({"duration_ms", "stages"}),
    }
)
