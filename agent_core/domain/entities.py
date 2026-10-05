"""Entidades del registro (M0 §2.4). Datos puros: la lógica vive en los módulos que las usan."""

import re
from collections.abc import Mapping
from datetime import date, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from types import MappingProxyType
from typing import Annotated, Any, Literal

from pydantic import (
    Field,
    NonNegativeInt,
    PositiveInt,
    SerializerFunctionWrapHandler,
    StringConstraints,
    model_serializer,
    model_validator,
)

from agent_core.domain.base import EntityId, ExactVersion, Locale, Model, Sha256Hex
from agent_core.domain.errors import InvalidRuntimeRef
from agent_core.domain.identity import AuthInfo, AuthLevel, PrincipalType
from agent_core.domain.json import JsonValue
from agent_core.domain.knowledge import (
    MAX_PAGE_SOURCE_REFS,
    Audience,
    PagePath,
    PageSourceRef,
    PageStatus,
    check_page_path,
)
from agent_core.domain.metrics import MetricDef
from agent_core.domain.nodes import Node, PositiveTimedelta
from agent_core.domain.outcomes import Mode
from agent_core.domain.refs import EntityKind, EntityRef, RefSpec, require_exact_refs
from agent_core.domain.transfer import AcceptedSlot, RoutingCard, TransferContract

# Dinero y tarifas: `Decimal` finito (nunca `float`; NaN e Infinity se rechazan).
PositiveMoney = Annotated[Decimal, Field(gt=0, allow_inf_nan=False)]
NonNegativeMoney = Annotated[Decimal, Field(ge=0, allow_inf_nan=False)]

# `<nombre>@X.Y.Z`: versión exacta y solo ASCII, para que el detector quede fijado (reproducibilidad).
_PINNED_DETECTOR = r"^[a-z][a-z0-9_-]*@(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)$"


class Budgets(Model):
    """Presupuestos duros de un agente por turno y por run (M0 §2.4)."""
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
    """Agente descrito como datos versionados: flow de entrada, límites y plantillas (M0 §2.4)."""
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
    slots_model: RefSpec | None = None  # 2.ª llamada de slots (`llm_structured`); sin él no hay
    templates: EngineTemplates
    max_clarifications: NonNegativeInt
    on_clarify_exhausted: Literal["end", "escalate"]
    default_target_queue: str = Field(min_length=1)
    max_repair_turns_per_run: PositiveInt = 8
    metrics: list[MetricDef] = Field(default_factory=list, max_length=32)  # ADR 0020; the engine ignores them
    routing: RoutingCard | None = None  # without a card the agent is in no directory (ADR 0021)
    accepts: TransferContract | None = None  # without a contract the agent receives no transfers
    # Contract of the `input` of a `task` run. With it, `start_run` validates the input and the slots enter
    # as `validated` (the caller is an authenticated service, not a user's claim); without it: `claimed`.
    input_schema: dict[str, AcceptedSlot] | None = None

    @model_validator(mode="after")
    def _default_locale_supported(self) -> "Agent":
        if self.default_locale not in self.supported_locales:
            raise ValueError("default_locale debe estar en supported_locales")
        if self.input_schema is not None and self.mode != "task":
            raise ValueError("input_schema solo aplica a agentes de modo task")
        return self


class Flow(Model):
    """Flow versionado: lista de nodos, el primero es la entrada (M0 §2.4)."""
    id: EntityId
    version: ExactVersion
    priority: int
    nodes: list[Node] = Field(min_length=1)  # el primer nodo es la entrada


class EscalateAction(Model):
    """Acción de una interrupción que escala a una cola (M0 §2.4)."""
    type: Literal["escalate"]
    target_queue: str
    priority: str


class StartFlowAction(Model):
    """Acción de una interrupción que inicia un flow (M0 §2.4)."""
    type: Literal["start_flow"]
    flow: RefSpec


class Interrupt(Model):
    """Interrupción de una release: prioridad y acción (escalar o iniciar un flow) (M0 §2.4)."""
    id: EntityId
    priority: int
    action: Annotated[EscalateAction | StartFlowAction, Field(discriminator="type")]
    signal_policy: RefSpec | None = None
    # Guardarraíl de plataforma: ninguna propuesta puede quitarla, bajarle la prioridad ni cambiarle la acción
    # (solo se fija al sembrar o con el rol admin). Una candidata que la pierda no pasa la validación.
    locked: bool = False


class LanguageDetection(Model):
    """Configuración versionada de la detección de idioma, con detector fijado (M0 §2.4)."""
    id: EntityId
    version: ExactVersion
    detector: Annotated[str, StringConstraints(pattern=_PINNED_DETECTOR)]  # "lingua@<versión exacta>"
    candidates: list[Locale]
    unsupported: list[str] = Field(default_factory=list)
    min_letters: NonNegativeInt
    min_letters_unsupported: NonNegativeInt
    thresholds_from: str | None = None


class InjectionRule(Model):
    """Regla de detección de inyección: regex o frase literal (M0 §2.4)."""
    id: str = Field(min_length=1)
    pattern: str = Field(min_length=1, max_length=2048)  # acota el costo de compilar/evaluar
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
    """Conjunto versionado de reglas de detección de inyección (M0 §2.4)."""
    id: EntityId
    version: ExactVersion
    rules: list[InjectionRule]


_MAX_SNAPSHOT_PAGES = 10_000


class KnowledgePage(Model):
    """Entrada de un snapshot de conocimiento: metadatos y hash del contenido, no el texto (ADR 0015, 0017).

    El texto vive en el `BlobStore` del registry, direccionado por `hash`."""
    path: PagePath
    hash: Sha256Hex
    audience: Audience
    status: PageStatus
    approved_by: str | None = Field(default=None, min_length=1)
    lang: Locale
    translation_of: PagePath | None = None
    valid_from: date | None = None
    valid_to: date | None = None
    source_refs: list[PageSourceRef] = Field(default_factory=list, max_length=MAX_PAGE_SOURCE_REFS)

    @model_validator(mode="after")
    def _coherent(self) -> "KnowledgePage":
        check_page_path(self.path)
        if self.translation_of is not None:
            check_page_path(self.translation_of)
        if (self.status == "approved") != (self.approved_by is not None):
            raise ValueError("una página está aprobada si y solo si tiene approved_by")
        if self.valid_from is not None and self.valid_to is not None and self.valid_from > self.valid_to:
            raise ValueError("valid_from no puede ser posterior a valid_to")
        return self


class KnowledgeSnapshot(Model):
    """Conjunto inmutable de páginas de conocimiento, fijado por una release (ADR 0015, M12)."""
    id: EntityId
    version: ExactVersion
    pages: list[KnowledgePage] = Field(default_factory=list, max_length=_MAX_SNAPSHOT_PAGES)

    @model_validator(mode="after")
    def _paths_are_unique_and_translations_resolve(self) -> "KnowledgeSnapshot":
        paths = [page.path for page in self.pages]
        if len(set(paths)) != len(paths):
            raise ValueError("ruta de página duplicada en el snapshot")
        known = set(paths)
        for page in self.pages:
            if page.translation_of is not None and (page.translation_of == page.path
                                                    or page.translation_of not in known):
                raise ValueError("translation_of debe ser otra página del mismo snapshot")
        return self


class Release(Model):
    """Conjunto inmutable de versiones exactas de entidades que forman un despliegue (M0 §2.4)."""
    id: str = Field(min_length=1)
    status: Literal["active", "revoked"]
    entities: dict[EntityKind, dict[EntityId, ExactVersion]] = Field(default_factory=dict)
    interrupts: list[Interrupt] = Field(default_factory=list)
    language_detection: EntityRef
    injection_ruleset: EntityRef | None = None
    knowledge_snapshot: EntityRef | None = None
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
    """Política versionada: expresión, responsable y justificación (M0 §2.4)."""
    id: EntityId
    version: ExactVersion
    owner: str
    expr: JsonValue
    rationale: str
    # Platform guardrail (mirrors `Interrupt.locked`): a later proposal cannot remove it, unlock it or change
    # it (REG-LOCKED). Only an admin can set it. Omitted from the serialisation when false, so the content
    # hash of already published policies does not change.
    locked: bool = False

    @model_serializer(mode="wrap")
    def _omit_default_locked(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        data: dict[str, Any] = handler(self)
        if not self.locked:
            data.pop("locked", None)
        return data


class Template(Model):
    """Plantilla versionada de respuesta por locale (M0 §2.4)."""
    id: EntityId
    version: ExactVersion
    locales: dict[Locale, str] = Field(min_length=1)
    reads: frozenset[str] = frozenset()  # variables de hecho que lee (derive_claims de M1)


class Prompt(Model):
    """Prompt versionado por locale, con su perfil de modelo (M0 §2.4)."""
    id: EntityId
    version: ExactVersion
    locales: dict[Locale, str] = Field(min_length=1)
    reads: frozenset[str] = frozenset()
    model_profile: RefSpec  # rev. 5 (ADR 0016)


class StructuredMode(StrEnum):
    """Modo de salida estructurada de un modelo: nativo o por prompt (M0 §2.4)."""
    native = "native"
    prompted = "prompted"


class ModelPrice(Model):
    """Tarifa de un modelo por millón de tokens, con fuente y fecha (M0 §2.4)."""
    input_per_mtok: NonNegativeMoney
    output_per_mtok: NonNegativeMoney
    source: str = Field(min_length=1)
    as_of: date


class ModelProfile(Model):
    """Perfil de un modelo de lenguaje: alias de endpoint, parámetros y tarifa (ADR 0016)."""
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
    """Clase de riesgo de una tool; determina si escribe y qué confirmación exige (M0 §2.4).

    `write_draft` es la escritura confinada a un borrador del registry: va sin `confirm` (ADR 0019)."""
    read = "read"
    compute = "compute"
    write_draft = "write_draft"
    write_reversible = "write_reversible"
    write_irreversible = "write_irreversible"
    money_movement = "money_movement"


class ToolDef(Model):
    """Definición versionada de una tool: riesgo, nivel de autenticación e idempotencia (M0 §2.4)."""
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
    description: str | None = None  # qué hace la tool, para el modelo del nodo `agent` (unidad 5)
    args_schema: dict[str, JsonValue] | None = None  # subconjunto cerrado de JSON Schema (domain.schema)

    @property
    def is_write(self) -> bool:
        return self.risk_class not in (RiskClass.read, RiskClass.compute)

    def accepts(self, auth: AuthInfo, at: datetime | None) -> bool:
        """Whether `auth` is enough for this tool at instant `at` (ADR 0010): the level and, when the tool
        declares `max_auth_age`, how old the authentication is. Without an instant the age cannot be checked,
        so a tool with `max_auth_age` is refused (fails closed)."""
        if auth.level < self.min_auth_level:
            return False
        if self.max_auth_age is None:
            return True
        return at is not None and at - auth.at <= self.max_auth_age

    @model_validator(mode="after")
    def _write_needs_readback(self) -> "ToolDef":
        if self.is_write and self.readback_by is None:
            raise ValueError("una tool de escritura declara readback_by (ADR 0007)")
        return self


class ProviderSpec(Model):
    """Proveedor de una decisión (jev, classifier, llm_structured o rule) y su configuración."""
    provider: Literal["jev", "classifier", "llm_structured", "rule"]
    config: dict[str, JsonValue] = Field(default_factory=dict)

    @property
    def compares_on_full_view(self) -> bool:
        """`rule` con `compare_on: full` (ADR 0027): compara en la vista `full` pero solo devuelve valores
        de la vista `model`. Ningún otro proveedor lo admite."""
        return self.provider == "rule" and self.config.get("compare_on") == "full"


class CalibrationRef(Model):
    """Método de calibración de un modelo de decisión y la corrida que lo produjo (M0 §2.4)."""
    method: Literal["none", "isotonic", "platt", "temperature"]
    run: str | None = None


class DecisionModelDef(Model):
    """Definición de un modelo de decisión: esquema de salida, proveedores y calibración (M0 §2.4)."""
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
    | InjectionRuleset | ModelProfile | KnowledgeSnapshot
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
        KnowledgeSnapshot: EntityKind.knowledge_snapshot,
    }
)
