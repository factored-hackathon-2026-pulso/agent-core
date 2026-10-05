"""Puertos de M2 hacia M5 (`decide`) y M8 (`respond(generate)`) mientras esos módulos no existen (D1).

M5 y M8 los adaptarán a su interfaz real; las firmas reproducen m05 §2 y m08 §2 en lo que M2 necesita."""

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Protocol

from agent_core.domain import (
    AgentNodeConfig,
    Decision,
    EngineEvent,
    EntityRef,
    EscalationRequest,
    GenerateConfig,
    JsonValue,
    LlmUsage,
    Locale,
    Message,
    NodeId,
    RejectedDraft,
    RunState,
    SuggestConfig,
    SuggestEscalation,
    Suggestion,
)
from agent_core.ports import ToolStatus


@dataclass(frozen=True)
class DecisionResult:
    """Salida de `decide`: la decisión guardable, qué campos superan su umbral y su costo."""

    decision: Decision
    above_threshold: dict[str, bool]
    events: list[EngineEvent] = field(default_factory=list)  # `decision_made`, lo emite M5
    model_calls: int = 1
    tokens: int = 0
    cost_usd: Decimal = Decimal("0")


class DecisionPort(Protocol):
    """Recibe la vista `model` (M5 §3.1). `inputs_full` solo se pasa a un modelo con un proveedor `rule` de
    `compare_on: full` (ADR 0027); el puerto la entrega únicamente a ese proveedor."""

    def decide(self, model: EntityRef, inputs_model_view: dict[str, JsonValue],
               locale: Locale, inputs_full: dict[str, JsonValue] | None = None) -> DecisionResult: ...

    def decide_choice(self, model: EntityRef, inputs_model_view: dict[str, JsonValue], choices: list[str],
                      locale: Locale) -> DecisionResult:
        """Picks one of `choices` or `"none"`; the decision value is `{"choice": ...}` (ADR 0021)."""
        ...


@dataclass(frozen=True)
class GenerateRequest:
    """Nodo `respond(generate)` que M8 debe resolver; `claims` sale de `derive_claims` (M1)."""

    node_id: NodeId
    config: GenerateConfig
    claims: frozenset[str]


@dataclass(frozen=True)
class GenerateResult:
    """Un mensaje o una solicitud de escalamiento (`validation_failed`), más el uso del modelo."""

    message: Message | None = None
    escalation: EscalationRequest | None = None
    rejected: list[RejectedDraft] = field(default_factory=list)
    events: list[EngineEvent] = field(default_factory=list)
    model_calls: int = 0
    tokens: int = 0
    cost_usd: Decimal = Decimal("0")


class ResponderPort(Protocol):
    """Generar → validar → regenerar → plantilla de respaldo (M8 §3.2)."""

    def generate(self, request: GenerateRequest, state: RunState) -> GenerateResult: ...


@dataclass(frozen=True)
class AgentObservation:
    """Lo que el modelo ve de un paso anterior: la tool, sus argumentos y su resultado en vista `model`.

    `result` es `None` si la tool no se ejecutó o no devolvió `ok`; `status` distingue el motivo."""

    tool: EntityRef
    args: dict[str, JsonValue]
    status: ToolStatus
    result: JsonValue = None
    error: str | None = None


@dataclass(frozen=True)
class AgentRequest:
    """One step of the `agent` node (m02 §3.7). `feedback` is the reason (without data) why the previous
    output was rejected, if any. `inputs` are the `input_view` paths in the `model` view (slots wrapped),
    the same on every step of the node."""

    node_id: NodeId
    config: AgentNodeConfig
    step: int
    observations: tuple[AgentObservation, ...] = ()
    feedback: str | None = None
    inputs: dict[str, JsonValue] = field(default_factory=dict)


@dataclass(frozen=True)
class AgentToolCall:
    """El modelo pide ejecutar una tool de `tools_allowed`."""

    tool: EntityRef
    args: dict[str, JsonValue] = field(default_factory=dict)


@dataclass(frozen=True)
class AgentFinal:
    """El modelo da su respuesta final, que debe cumplir `output_schema`."""

    output: JsonValue


@dataclass(frozen=True)
class AgentStepResult:
    """Lo que el modelo decidió en un paso y lo que costó."""

    action: AgentToolCall | AgentFinal
    model_calls: int = 1
    tokens: int = 0
    cost_usd: Decimal = Decimal("0")


class AgentPort(Protocol):
    """Un paso del bucle del nodo `agent`. Solo recibe la vista `model` (M7); el adaptador real (gateway,
    prompt) está abierto (m02 §11)."""

    def step(self, request: AgentRequest, state: RunState) -> AgentStepResult: ...


@dataclass(frozen=True)
class SuggestRequest:
    """Nodo `suggest` que M8 debe resolver (m02 §3.8, ADR 0026). `inputs` son las rutas de `reads` en vista
    `model`; `escalation` lo fijó el flow (su evidencia nunca va al modelo)."""

    node_id: NodeId
    config: SuggestConfig
    inputs: dict[str, JsonValue]
    escalation: SuggestEscalation | None = None


@dataclass(frozen=True)
class SuggestResult:
    """La lista validada (posiblemente vacía) o los ids de las comprobaciones que fallaron (sin datos), más el
    uso del modelo. `failures` no vacío es `gave_up`; una lista vacía sin fallas es un resultado válido."""

    suggestions: list[Suggestion] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)
    regenerations: int = 0
    llm: LlmUsage | None = None
    model_calls: int = 0
    tokens: int = 0
    cost_usd: Decimal = Decimal("0")


class SuggesterPort(Protocol):
    """Generar → validar → regenerar → fallar (M8). Solo recibe la vista `model`."""

    def suggest(self, request: SuggestRequest, state: RunState) -> SuggestResult: ...
