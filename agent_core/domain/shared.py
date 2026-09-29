"""Tipos que cruzan fronteras entre módulos que no pueden importarse (M0 §2.8, regla de ubicación)."""

from enum import StrEnum
from typing import Literal

from pydantic import Field

from agent_core.domain.base import Model, UtcDatetime
from agent_core.domain.json import JsonValue
from agent_core.domain.outcomes import ReasonCodeStr


class ToolStatus(StrEnum):
    """Estado del resultado de una tool (M0 §2.8)."""
    ok = "ok"
    error = "error"
    timeout = "timeout"
    denied = "denied"
    uncertain = "uncertain"
    step_up_required = "step_up_required"


class EscalationRequest(Model):
    """M2/M4 → M10."""

    reason_code: ReasonCodeStr
    target_queue: str
    priority: str


class RejectedDraft(Model):
    """M8 → M11 (transcript)."""

    text_model: str
    reason: str
    failures: list[str] = Field(default_factory=list)


class Fingerprint(Model):
    """Huella con clave (ADR 0008): `value = HMAC-SHA256(k[kid], JCS(dato))`."""

    alg: Literal["HMAC-SHA256"]
    kid: str
    value: str


class TranscriptEntry(Model):
    """Entrada del transcript de un run, con el texto tal como lo vio el modelo (M0 §2.8)."""
    run_id: str
    turn_id: str
    role: Literal["user", "assistant", "rejected_draft"]
    text_model: str
    reason: str | None = None


class TranscriptRef(Model):
    """Referencia a una entrada del transcript, con su huella (M0 §2.8)."""
    entry_id: str
    fingerprint: Fingerprint


class OutboxMessage(Model):
    """Mensaje de la bandeja de salida transaccional (M0 §2.8)."""
    message_id: str
    type: Literal["handoff_created"]
    run_id: str
    payload: dict[str, JsonValue]
    created_at: UtcDatetime
