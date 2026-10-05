"""Sobre y datos de los eventos salientes v1 (docs/specs/2026-10-02-eventos-salientes-design.md).

Cada `data` es un modelo cerrado y se construye campo a campo: nunca un volcado del payload de la cadena."""

from typing import Annotated, Literal

from pydantic import Field

from agent_core.domain import EntityRef, Locale, Mode, Outcome, PrincipalType, ReasonCodeStr
from agent_core.domain.base import Model, UtcDatetime

SPEC_VERSION = 1
PUBLIC_TYPES = (
    "run.started", "run.closed", "run.transferred", "handoff.created", "handoff.resolved",
    "release.published", "release.promoted", "release.revoked")


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


class ReleaseData(Model):
    """Sin actor ni motivo (personas, texto libre). `agent_id`, `alias` y `before` son opcionales y aditivos
    (2026-10-05): el agente y el alias que toca el evento (`staging` al publicar, el promovido, ninguno al
    revocar) y la release a la que apuntaba el alias antes. `None` en un evento guardado antes de ellos."""
    release_id: str
    proposal_id: str | None = None
    agent_id: str | None = None
    alias: str | None = None
    before: str | None = None


class _Envelope(Model):
    spec_version: Literal[1] = 1
    event_id: str
    occurred_at: UtcDatetime
    run_id: str | None = None
    session_id: str | None = None
    turn_id: str | None = None
    release_id: str | None = None


class _EngineEnvelope(_Envelope):
    source: Literal["engine"] = "engine"


class _RegistryEnvelope(_Envelope):
    source: Literal["registry"] = "registry"


class RunStartedEvent(_EngineEnvelope):
    type: Literal["run.started"] = "run.started"
    data: RunStartedData


class RunClosedEvent(_EngineEnvelope):
    type: Literal["run.closed"] = "run.closed"
    data: RunClosedData


class RunTransferredEvent(_EngineEnvelope):
    type: Literal["run.transferred"] = "run.transferred"
    data: RunTransferredData


class HandoffCreatedEvent(_EngineEnvelope):
    type: Literal["handoff.created"] = "handoff.created"
    data: HandoffCreatedData


class HandoffResolvedEvent(_EngineEnvelope):
    type: Literal["handoff.resolved"] = "handoff.resolved"
    data: HandoffResolvedData


class ReleasePublishedEvent(_RegistryEnvelope):
    type: Literal["release.published"] = "release.published"
    data: ReleaseData


class ReleasePromotedEvent(_RegistryEnvelope):
    type: Literal["release.promoted"] = "release.promoted"
    data: ReleaseData


class ReleaseRevokedEvent(_RegistryEnvelope):
    type: Literal["release.revoked"] = "release.revoked"
    data: ReleaseData


OutboundEvent = Annotated[
    RunStartedEvent | RunClosedEvent | RunTransferredEvent | HandoffCreatedEvent | HandoffResolvedEvent
    | ReleasePublishedEvent | ReleasePromotedEvent | ReleaseRevokedEvent,
    Field(discriminator="type"),
]
