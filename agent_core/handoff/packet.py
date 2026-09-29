"""Tipos del handoff (M10 §2). El paquete persistido lleva vista `audit`; nunca incrusta el transcript."""

from typing import Literal

from pydantic import Field, NonNegativeInt

from agent_core.domain import (
    ActionState,
    EntityRef,
    FactSource,
    InvalidationReason,
    JsonValue,
    Locale,
    Model,
    PrincipalType,
    ReasonCodeStr,
    SubjectRef,
    UtcDatetime,
)

HandoffQuality = Literal["useful", "incomplete", "unnecessary"]


class RequestSummary(Model):
    """Qué pidió el usuario y cómo se llegó aquí. `citations` son los nombres de los hechos citados."""

    text: str
    citations: list[str] = Field(default_factory=list)


class FactView(Model):
    """Hecho verificado con su procedencia. `value` en vista `audit` (sin valor en un paquete degradado)."""

    fact_id: str
    name: str  # clave en `RunState.facts`; también la fuente de la proyección
    value: JsonValue = None
    source: FactSource
    ts: UtcDatetime


class SlotView(Model):
    """Slot `claimed`: lo dijo el usuario y nadie lo verificó. `value` en vista `audit`."""

    name: str
    value: JsonValue
    source_turn: NonNegativeInt


class ActionView(Model):
    """Acción con su estado de verificación (`verified`, `failed`, `uncertain`, `cancelled`, …)."""

    action_id: str
    tool: EntityRef
    state: ActionState
    args: dict[str, JsonValue] = Field(default_factory=dict)  # vista `audit`
    cancel_reason: InvalidationReason | None = None


class HandoffPacket(Model):
    """Paquete estructurado del traspaso (spec general §9). Referencia el transcript, no lo incrusta."""

    handoff_ref: str
    run_id: str
    release: str
    agent: EntityRef
    principal_type: PrincipalType
    subject: SubjectRef | None  # ref enmascarado
    target_queue: str
    priority: str
    reason_code: ReasonCodeStr
    language: Locale
    request_summary: RequestSummary
    verified_facts: list[FactView]
    claimed_not_verified: list[SlotView]
    actions_taken: list[ActionView]
    open_questions: list[str]
    evidence_refs: list[str]
    transcript_ref: str
    degraded_packet: bool = False


class Resolution(Model):
    """Resolución del receptor: etiqueta para la unidad 6 y señal para la auto-mejora."""

    resolution_code: str
    handoff_quality: HandoffQuality
    notes: str | None = None  # solo aquí; nunca en el evento
    resolved_at: UtcDatetime
    reader_type: PrincipalType
    reader_id: str | None


class HandoffRecord(Model):
    """Lo que guarda `UnitOfWork.put_handoff`: el paquete y, una vez, su resolución."""

    packet: HandoffPacket
    resolution: Resolution | None = None
