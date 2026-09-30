"""Estado del run (M0 §2.6). Cada parte tiene un solo módulo que la escribe (índice §5)."""

from collections.abc import Mapping
from decimal import Decimal
from enum import StrEnum
from typing import Any, Literal, Self

from pydantic import Field, NonNegativeInt, model_validator

from agent_core.domain.base import Locale, Model, MutableModel, NodeId, Probability, Sha256Hex, UtcDatetime
from agent_core.domain.identity import OnBehalfOf, Principal, SubjectRef
from agent_core.domain.json import JsonValue
from agent_core.domain.outcomes import Awaiting, Mode, Outcome
from agent_core.domain.refs import EntityRef

RunStatus = Literal["open", "closed", "escalated"]


class Slot(Model):
    """Valor de un slot recolectado, `claimed` o `validated`, y el turno de origen (M0 §2.6)."""
    value: JsonValue
    status: Literal["claimed", "validated"]
    source_turn: NonNegativeInt


class FactSource(Model):
    """Origen de un hecho: tipo, referencia y entradas (M0 §2.6)."""
    kind: Literal["tool", "compute", "identity", "knowledge"]
    ref: str
    inputs: list[str] = Field(default_factory=list)


class Fact(Model):
    """Hecho verificado con su origen (tool, compute, identidad o conocimiento); vista `full` (M0 §2.6)."""
    fact_id: str
    value: JsonValue  # vista full
    source: FactSource
    ts: UtcDatetime


class Decision(Model):
    """Resultado guardado de una decisión (`decide`): valor, probabilidades y proveedor (M0 §2.6)."""
    decision_id: str
    value: dict[str, JsonValue]
    p_cal: dict[str, Probability | None]
    provider_used: str
    model_version: str


class ActionState(StrEnum):
    """Estados del ciclo de vida de una `Action` (M0 §2.6)."""
    proposed = "proposed"
    confirmed = "confirmed"
    executing = "executing"
    executed = "executed"
    uncertain = "uncertain"
    denied = "denied"
    verified = "verified"
    failed = "failed"
    cancelled = "cancelled"


class InvalidationReason(StrEnum):
    """Motivos por los que se invalida o cancela una acción (M0 §2.6)."""
    cancel = "cancel"
    abandoned = "abandoned"
    interrupt = "interrupt"
    escalated = "escalated"
    token_expired = "token_expired"
    max_attempts = "max_attempts"
    denied_by_user = "denied_by_user"


class Action(Model):
    """Acción de escritura propuesta al usuario, con su token de confirmación y ciclo de vida (M0 §2.6)."""
    action_id: str
    confirm_node_id: NodeId
    flow: EntityRef
    tool: EntityRef
    args: dict[str, JsonValue]
    args_hash: Sha256Hex
    state: ActionState
    confirmation_token_hash: Sha256Hex
    token_exp: UtcDatetime
    idempotency_key: str
    created_at: UtcDatetime
    cancel_reason: InvalidationReason | None = None


class ActiveFlow(Model):
    """Flow activo del run: nodo actual y slots locales (M0 §2.6)."""
    flow: EntityRef
    node_id: NodeId
    local_slots: dict[str, JsonValue] = Field(default_factory=dict)


class PendingIntent(Model):
    """Intención mencionada por el usuario que queda en cola tras el flow activo (M0 §2.6)."""
    flow: str
    priority: int
    mention_order: NonNegativeInt


class BudgetsUsed(Model):
    """Consumo acumulado de presupuestos del run y del turno (M0 §2.6)."""
    run_tokens: NonNegativeInt = 0
    run_cost: Decimal = Decimal("0")
    turn_nodes: NonNegativeInt = 0
    turn_model_calls: NonNegativeInt = 0
    turn_understand_calls: NonNegativeInt = 0  # llamadas de Understand del turno (M4 las suma; opción 2)
    turn_started_at: UtcDatetime | None = None


class EncryptedBlob(Model):
    """`token_map` cifrado (lo produce M7). Base64."""

    kid: str
    nonce: str
    ciphertext: str


class RunState(MutableModel):
    """Estado completo de un run; se actualiza con `model_copy(update=...)` y se revalida (M0 §2.6)."""
    run_id: str
    session_id: str | None = None
    state_version: NonNegativeInt = 0
    release: str
    agent: EntityRef
    principal: Principal
    on_behalf_of: OnBehalfOf | None = None
    subject: SubjectRef | None = None
    mode: Mode
    locale: Locale
    status: RunStatus = "open"
    outcome: Outcome | None = None
    created_at: UtcDatetime
    last_activity_at: UtcDatetime
    closed_at: UtcDatetime | None = None
    inactive_after: UtcDatetime | None = None
    awaiting: Awaiting = Awaiting.none
    awaiting_node_id: NodeId | None = None
    active_flow: ActiveFlow | None = None
    pending_intents: list[PendingIntent] = Field(default_factory=list)
    pending_offer: str | None = None
    slots: dict[str, Slot] = Field(default_factory=dict)
    facts: dict[str, Fact] = Field(default_factory=dict)
    decisions: dict[str, Decision] = Field(default_factory=dict)
    actions: list[Action] = Field(default_factory=list)
    token_map: EncryptedBlob | None = None
    open_questions: list[str] = Field(default_factory=list)
    budgets_used: BudgetsUsed = Field(default_factory=BudgetsUsed)
    turn_count: NonNegativeInt = 0
    clarifications_used: NonNegativeInt = 0
    node_attempts: dict[str, NonNegativeInt] = Field(default_factory=dict)
    repair_turns_used: NonNegativeInt = 0
    degraded_turns: list[NonNegativeInt] = Field(default_factory=list)
    handoff_ref: str | None = None

    @model_validator(mode="after")
    def _coherence(self) -> "RunState":
        """Detecta bugs; no reemplaza la lógica de los módulos dueños (M0 §2.6)."""
        if self.status == "open":
            if self.outcome is not None or self.closed_at is not None:
                raise ValueError("un run abierto no tiene outcome ni closed_at")
        elif self.inactive_after is not None:
            raise ValueError("un run cerrado no tiene inactive_after")
        if self.status == "escalated" and (self.outcome is not Outcome.escalated or self.handoff_ref is None):
            raise ValueError("un run escalado tiene outcome escalated y handoff_ref")
        if self.status == "closed" and (self.outcome is None or self.outcome is Outcome.escalated):
            raise ValueError("un run cerrado tiene un outcome distinto de escalated")
        if self.awaiting is not Awaiting.none and self.awaiting_node_id is None:
            offer = (
                self.awaiting is Awaiting.input
                and self.pending_offer is not None
                and self.active_flow is None
            )
            if not offer:
                raise ValueError("awaiting requiere awaiting_node_id, salvo la oferta de intención")
        proposed = [a.confirm_node_id for a in self.actions if a.state is ActionState.proposed]
        if len(proposed) != len(set(proposed)):
            raise ValueError("a lo sumo una acción proposed por confirm")
        if self.mode == "task" and self.session_id is not None:
            raise ValueError("un run task no tiene session_id")
        return self

    def model_copy(self, *, update: Mapping[str, Any] | None = None, deep: bool = False) -> Self:
        """Vía documentada de actualización (índice §5): revalida el estado resultante, falla cerrado.

        `model_copy` de pydantic no valida; aquí el estado fusionado pasa de nuevo por todos los validadores.
        Se reconstruye desde un volcado, así que el resultado nunca comparte objetos mutables (`deep` es
        siempre efectivo) y no se emiten avisos del serializador con dicts o enums en `str`.
        """
        if update:
            unknown = set(update) - set(type(self).model_fields)
            if unknown:
                raise ValueError(f"campos desconocidos en update: {sorted(unknown)}")
        data = self.model_dump(mode="python")
        if update:
            data.update(update)
        return type(self).model_validate(data)
