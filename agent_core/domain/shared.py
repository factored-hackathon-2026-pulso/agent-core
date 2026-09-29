"""Tipos que cruzan fronteras entre módulos que no pueden importarse (M0 §2.8, regla de ubicación)."""

from enum import StrEnum
from typing import Literal

from pydantic import Field

from agent_core.domain.base import Model, UtcDatetime
from agent_core.domain.json import JsonValue
from agent_core.domain.outcomes import ReasonCodeStr


class ToolStatus(StrEnum):
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
    run_id: str
    turn_id: str
    role: Literal["user", "assistant", "rejected_draft"]
    text_model: str
    reason: str | None = None


class TranscriptRef(Model):
    entry_id: str
    fingerprint: Fingerprint


class OutboxMessage(Model):
    message_id: str
    type: Literal["handoff_created"]
    run_id: str
    payload: dict[str, JsonValue]
    created_at: UtcDatetime
