"""Entidades del registro (M0 §2.4). Datos puros: la lógica vive en los módulos que las usan."""

import re
from collections.abc import Mapping
from datetime import date, timedelta
from decimal import Decimal
from enum import StrEnum
from types import MappingProxyType
from typing import Annotated, Literal

from pydantic import Field, NonNegativeInt, PositiveInt, StringConstraints, model_validator

from agent_core.domain.base import EntityId, ExactVersion, Locale, Model
from agent_core.domain.errors import InvalidRuntimeRef
from agent_core.domain.identity import AuthLevel, PrincipalType
from agent_core.domain.json import JsonValue
from agent_core.domain.nodes import Node, PositiveTimedelta
from agent_core.domain.outcomes import Mode
from agent_core.domain.refs import EntityKind, EntityRef, RefSpec, require_exact_refs

# Dinero y tarifas: `Decimal` finito (nunca `float`; NaN e Infinity se rechazan).
PositiveMoney = Annotated[Decimal, Field(gt=0, allow_inf_nan=False)]
NonNegativeMoney = Annotated[Decimal, Field(ge=0, allow_inf_nan=False)]

# `<nombre>@X.Y.Z`: versión exacta y solo ASCII, para que el detector quede fijado (reproducibilidad).
_PINNED_DETECTOR = r"^[a-z][a-z0-9_-]*@(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)$"


class Budgets(Model):
    max_nodes_per_turn: PositiveInt
    max_model_calls_per_turn: PositiveInt
    max_tokens_per_run: PositiveInt
    max_cost_per_run: PositiveMoney
    max_wall_ms_per_turn: PositiveInt


class EngineTemplates(Model):
    """Plantillas que usa el motor fuera de los flows (M4, M6, M10)."""

    clarify: RefSpec
    abstain: RefSpec
    handoff: RefSpec
    pending_ack: RefSpec
    pending_offer: RefSpec
    unsupported_language: RefSpec
    input_too_large: RefSpec


class Agent(Model):
    id: EntityId
    version: ExactVersion
    mode: Mode
    entry_flow: RefSpec
    invocable_by: list[PrincipalType] = Field(min_length=1)
    min_auth_level: AuthLevel
    subject_kinds: list[str]
    supported_locales: list[Locale] = Field(min_length=1)
    default_locale: Locale
    tools_allowed: list[RefSpec] = Field(default_factory=list)
    budgets: Budgets
    inactivity_ttl: PositiveTimedelta = timedelta(minutes=30)
    understand: RefSpec | None = None
    templates: EngineTemplates
    max_clarifications: NonNegativeInt
    on_clarify_exhausted: Literal["end", "escalate"]
    default_target_queue: str = Field(min_length=1)
    max_repair_turns_per_run: PositiveInt = 8

    @model_validator(mode="after")
    def _default_locale_supported(self) -> "Agent":
        if self.default_locale not in self.supported_locales:
            raise ValueError("default_locale debe estar en supported_locales")
        return self


class Flow(Model):
    id: EntityId
    version: ExactVersion
    priority: int
    nodes: list[Node] = Field(min_length=1)  # el primer nodo es la entrada


class EscalateAction(Model):
    type: Literal["escalate"]
    target_queue: str
    priority: str


class StartFlowAction(Model):
    type: Literal["start_flow"]
    flow: RefSpec


class Interrupt(Model):
    id: EntityId
    priority: int
    action: Annotated[EscalateAction | StartFlowAction, Field(discriminator="type")]
    signal_policy: RefSpec | None = None


class LanguageDetection(Model):
    id: EntityId
    version: ExactVersion
    detector: Annotated[str, StringConstraints(pattern=_PINNED_DETECTOR)]  # "lingua@<versión exacta>"
    candidates: list[Locale]
    unsupported: list[str] = Field(default_factory=list)
    min_letters: NonNegativeInt
    min_letters_unsupported: NonNegativeInt
    thresholds_from: str | None = None


class InjectionRule(Model):
    id: str = Field(min_length=1)
    pattern: str = Field(min_length=1)
    kind: Literal["regex", "phrase"]

    @model_validator(mode="after")
    def _regex_compiles(self) -> "InjectionRule":
        # Falla al cargar, no en el primer turno; una frase es literal y no se compila.
        if self.kind == "regex":
            try:
                re.compile(self.pattern)
            except re.error as exc:
                raise ValueError(f"regex inválida en la regla {self.id!r}: {exc}") from exc
        return self


class InjectionRuleset(Model):
    id: EntityId
    version: ExactVersion
    rules: list[InjectionRule]


class Release(Model):
    id: str = Field(min_length=1)
    status: Literal["active", "revoked"]
    entities: dict[EntityKind, dict[EntityId, ExactVersion]] = Field(default_factory=dict)
    interrupts: list[Interrupt] = Field(default_factory=list)
    language_detection: EntityRef
    injection_ruleset: EntityRef | None = None
    max_input_chars: PositiveInt = 4000

    @model_validator(mode="after")
    def _refs_are_exact(self) -> "Release":
        # Una release es reproducible: toda referencia que contiene (interrupciones, señales) es exacta.
        try:
            require_exact_refs(self)
        except InvalidRuntimeRef as exc:
            raise ValueError(str(exc)) from exc
        return self


class Policy(Model):
    id: EntityId
    version: ExactVersion
    owner: str
    expr: JsonValue
    rationale: str


class Template(Model):
    id: EntityId
    version: ExactVersion
    locales: dict[Locale, str] = Field(min_length=1)
    reads: frozenset[str] = frozenset()  # variables de hecho que lee (derive_claims de M1)


class Prompt(Model):
    id: EntityId
    version: ExactVersion
    locales: dict[Locale, str] = Field(min_length=1)
    reads: frozenset[str] = frozenset()
    model_profile: RefSpec  # rev. 5 (ADR 0016)


class StructuredMode(StrEnum):
    native = "native"
    prompted = "prompted"


class ModelPrice(Model):
    input_per_mtok: NonNegativeMoney
    output_per_mtok: NonNegativeMoney
    source: str = Field(min_length=1)
    as_of: date


class ModelProfile(Model):
    id: EntityId
    version: ExactVersion
    endpoint_alias: str = Field(min_length=1)  # alias del endpoint, nunca credenciales ni URL
    model: str = Field(min_length=1)
    temperature: Annotated[Decimal, Field(ge=0, allow_inf_nan=False)]
    max_tokens: PositiveInt
    timeout_s: PositiveInt = 8
    structured: StructuredMode = StructuredMode.native
    price: ModelPrice


class RiskClass(StrEnum):
    read = "read"
    compute = "compute"
    write_reversible = "write_reversible"
    write_irreversible = "write_irreversible"
    money_movement = "money_movement"


class ToolDef(Model):
    id: EntityId
    version: ExactVersion
    risk_class: RiskClass
    min_auth_level: AuthLevel
    max_auth_age: PositiveTimedelta | None = None
    idempotent: bool
    readback_by: Literal["idempotency_key"] | None = None
    untrusted_fields: list[str] = Field(default_factory=list)
    source: str | None = None  # tabla de origen para M7
    confirmation_ttl: PositiveTimedelta = timedelta(minutes=5)

    @property
    def is_write(self) -> bool:
        return self.risk_class not in (RiskClass.read, RiskClass.compute)

    @model_validator(mode="after")
    def _write_needs_readback(self) -> "ToolDef":
        if self.is_write and self.readback_by is None:
            raise ValueError("una tool de escritura declara readback_by (ADR 0007)")
        return self


class ProviderSpec(Model):
    provider: Literal["jev", "classifier", "llm_structured", "rule"]
    config: dict[str, JsonValue] = Field(default_factory=dict)


class CalibrationRef(Model):
    method: Literal["none", "isotonic", "platt", "temperature"]
    run: str | None = None


class DecisionModelDef(Model):
    id: EntityId
    version: ExactVersion
    output_schema: dict[str, JsonValue]
    calibrated_fields: list[str]
    input_view: list[str] = Field(default_factory=list)
    providers: list[ProviderSpec] = Field(min_length=1)
    calibration: CalibrationRef
    thresholds_from: str | None = None


RegistryEntity = (
    Agent | Flow | Policy | Template | Prompt | ToolDef | DecisionModelDef | LanguageDetection
    | InjectionRuleset | ModelProfile
)

ENTITY_KIND: Mapping[type, EntityKind] = MappingProxyType(
    {
        Agent: EntityKind.agent,
        Flow: EntityKind.flow,
        Policy: EntityKind.policy,
        Template: EntityKind.template,
        Prompt: EntityKind.prompt,
        ToolDef: EntityKind.tool,
        DecisionModelDef: EntityKind.decision_model,
        LanguageDetection: EntityKind.language_detection,
        InjectionRuleset: EntityKind.injection_ruleset,
        ModelProfile: EntityKind.model_profile,
    }
)
