"""Modelos del registry (spec §3, §7, §9). Solo datos."""

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from agent_core.domain import EntityId, EntityRef, Interrupt, JsonValue, Release
from agent_core.registry.evaluation.report import EvalReport, Verdict
from agent_core.registry.evaluation.yardstick import YardstickChange


class RegModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class VersionRef(RegModel):
    kind: str
    id: str
    version: str

    def __str__(self) -> str:
        return f"{self.kind}:{self.id}@{self.version}"


class VersionDocs(RegModel):
    description: str = Field(min_length=1, max_length=4000)
    rationale: str = Field(max_length=4000)
    changelog: str = Field(max_length=8000)


class Origin(StrEnum):
    manual = "manual"
    builder_chat = "builder_chat"
    auto_detect = "auto_detect"
    import_ = "import"


class ProposalState(StrEnum):
    draft = "draft"
    candidate = "candidate"
    evaluated = "evaluated"
    approved = "approved"
    published = "published"


class EntityDraft(RegModel):
    kind: str
    content: dict[str, JsonValue]
    docs: VersionDocs

    @model_validator(mode="after")
    def _has_identity(self) -> "EntityDraft":
        if not isinstance(self.content.get("id"), str) or not isinstance(self.content.get("version"), str):
            raise ValueError("el contenido necesita `id` y `version` como texto")
        return self

    @property
    def id(self) -> str:
        return str(self.content["id"])

    @property
    def version(self) -> str:
        return str(self.content["version"])


class Proposal(RegModel):
    proposal_id: str
    agent_id: EntityId
    origin: Origin
    state: ProposalState
    rev: int = 0
    base_release_id: str | None
    title: str = Field(min_length=1, max_length=200)
    created_by: str
    candidate_hash: str | None = None
    updated_at: datetime


class StoredVersion(RegModel):
    ref: VersionRef
    content_hash: str
    docs: VersionDocs
    proposal_id: str | None
    created_by: str
    created_at: datetime


class StoredRelease(RegModel):
    release: Release
    release_hash: str
    agent_id: str
    agent_version: str
    base_release_id: str | None
    proposal_id: str | None
    published_by: str
    published_at: datetime
    eval_suite_refs: list[VersionRef] = Field(default_factory=list)  # ADR 0020: suites used by the gate


class Approval(RegModel):
    proposal_id: str
    candidate_hash: str
    actor: str
    decision: Literal["approved", "rejected"]
    reason: str | None = None
    yardstick_loosened: list[YardstickChange] = Field(default_factory=list)  # ADR 0020 §6.2: approved apart
    at: datetime


class EvalRun(RegModel):
    eval_run_id: str
    proposal_id: str
    candidate_hash: str
    base_release_id: str | None
    suite: VersionRef
    verdict: Verdict
    report: EvalReport
    at: datetime


class AliasChange(RegModel):
    agent_id: str
    alias: str
    before: str | None
    after: str
    actor: str
    reason: str
    at: datetime


class RegistryEvent(RegModel):
    type: str
    actor: str
    principal_type: str
    origin: str | None = None
    proposal_id: str | None = None
    candidate_hash: str | None = None
    release_id: str | None = None
    at: datetime


DraftOp = Literal["create_proposal", "put_draft", "freeze", "reopen", "evaluate"]


class AuditContext(RegModel):
    """Quién pidió la escritura (ADR 0019 §4): el run y su principal, solo para auditoría, nunca permisos."""

    run_id: str = Field(min_length=1, max_length=200)
    on_behalf_of: str = Field(min_length=1, max_length=200)  # `tipo:id` del principal del run


class DraftWrite(RegModel):
    """Una escritura del constructor con clave de idempotencia (ADR 0007 §5). Una sola vez por clave."""

    idempotency_key: str = Field(min_length=1, max_length=255)
    op: DraftOp
    proposal_id: str
    rev_after: int
    request_hash: str
    result_ref: str | None = None  # `evaluate`: el id de la corrida de evaluación
    audit: AuditContext | None = None
    created_at: datetime


class WriteRecord(RegModel):
    """Lo que devuelve `get_write` (el readback de las tools `write_draft` del constructor)."""

    op: DraftOp
    proposal_id: str
    rev_after: int
    request_hash: str
    verdict: Verdict | None = None  # solo `evaluate`
    run_id: str | None = None
    on_behalf_of: str | None = None


class EntityInRelease(RegModel):
    ref: VersionRef
    content_hash: str
    docs: VersionDocs
    changed_vs_base: bool


class ReleaseDetail(RegModel):
    release_id: str
    status: Literal["active", "revoked"]
    agent_id: str
    entities: list[EntityInRelease]
    knowledge_snapshot: str | None
    proposal_id: str | None
    base_release_id: str | None
    published_by: str
    published_at: datetime
    eval_suite_refs: list[VersionRef] = Field(default_factory=list)  # the next proposal's old yardstick
    # Campos de nivel release que entran en `release_hash` (N-03): con ellos se reconstruye sin dry-run.
    interrupts: list[Interrupt] = Field(default_factory=list)
    language_detection: EntityRef
    injection_ruleset: EntityRef | None = None
    max_input_chars: int


class ChangedRef(RegModel):
    before: VersionRef
    after: VersionRef
    docs: VersionDocs


class ReleaseDiff(RegModel):
    a: str
    b: str
    added: list[VersionRef]
    removed: list[VersionRef]
    changed: list[ChangedRef]


class RunLineage(RegModel):
    run_id: str
    release_id: str
    entities: list[EntityInRelease]
    knowledge_snapshot: str | None
    proposal_id: str | None
    eval_verdict: Verdict | None
    built_by: str | None
    approved_by: str | None
    published_at: datetime


class VersionSummary(RegModel):
    ref: VersionRef
    content_hash: str
    docs: VersionDocs
    created_by: str
    created_at: datetime


class EntityVersion(RegModel):
    ref: VersionRef
    content: dict[str, JsonValue]
    content_hash: str
    docs: VersionDocs
    created_by: str
    created_at: datetime
