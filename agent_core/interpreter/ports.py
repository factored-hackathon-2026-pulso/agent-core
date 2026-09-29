"""Puertos de M2 hacia M5 (`decide`) y M8 (`respond(generate)`) mientras esos módulos no existen (D1).

M5 y M8 los adaptarán a su interfaz real; las firmas reproducen m05 §2 y m08 §2 en lo que M2 necesita."""

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Protocol

from agent_core.domain import (
    Decision,
    EngineEvent,
    EntityRef,
    EscalationRequest,
    GenerateConfig,
    JsonValue,
    Locale,
    Message,
    NodeId,
    RejectedDraft,
    RunState,
)


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
    """Recibe solo la vista `model` (M5 §3.1)."""

    def decide(self, model: EntityRef, inputs_model_view: dict[str, JsonValue],
               locale: Locale) -> DecisionResult: ...


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
