"""Sobre y datos de los eventos salientes v1 (docs/specs/2026-10-02-eventos-salientes-design.md).

Cada `data` es un modelo cerrado y se construye campo a campo: nunca un volcado del payload de la cadena."""

from typing import Annotated, Literal

from pydantic import Field

from agent_core.domain import EntityRef, Locale, Mode, Outcome, PrincipalType, ReasonCodeStr
from agent_core.domain.base import Model, UtcDatetime

SPEC_VERSION = 1
PUBLIC_TYPES = ("run.started", "run.closed", "run.transferred", "handoff.created", "handoff.resolved")


class RunStartedData(Model):
    agent: EntityRef
    mode: Mode
    principal_type: PrincipalType
    locale: Locale
    subject_kind: str | None = None


class RunClosedData(Model):
    outcome: Outcome
    closed_by: Literal["flow", "abandonment", "escalation", "revocation", "transfer"]


class RunTransferredData(Model):
    transfer_id: str
    to_agent: EntityRef
    to_release_id: str
    to_run_id: str


class HandoffCreatedData(Model):
    handoff_ref: str
    target_queue: str
    priority: str
    reason_code: ReasonCodeStr
    language: Locale


class HandoffResolvedData(Model):
    handoff_ref: str
    handoff_quality: Literal["useful", "incomplete", "unnecessary"]
    reader_type: PrincipalType


class _Envelope(Model):
    spec_version: Literal[1] = 1
    event_id: str
    occurred_at: UtcDatetime
    source: Literal["engine"] = "engine"
    run_id: str | None = None
    session_id: str | None = None
    turn_id: str | None = None
    release_id: str | None = None


class RunStartedEvent(_Envelope):
    type: Literal["run.started"] = "run.started"
    data: RunStartedData


class RunClosedEvent(_Envelope):
    type: Literal["run.closed"] = "run.closed"
    data: RunClosedData


class RunTransferredEvent(_Envelope):
    type: Literal["run.transferred"] = "run.transferred"
    data: RunTransferredData


class HandoffCreatedEvent(_Envelope):
    type: Literal["handoff.created"] = "handoff.created"
    data: HandoffCreatedData


class HandoffResolvedEvent(_Envelope):
    type: Literal["handoff.resolved"] = "handoff.resolved"
    data: HandoffResolvedData


OutboundEvent = Annotated[
    RunStartedEvent | RunClosedEvent | RunTransferredEvent | HandoffCreatedEvent | HandoffResolvedEvent,
    Field(discriminator="type"),
]
