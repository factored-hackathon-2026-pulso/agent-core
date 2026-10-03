"""Registry (unidad 2): entidades, versionado y publicación.

Spec: docs/specs/2026-09-29-registry-design.md (rev. 2). ADR 0017 y 0018.
Otros módulos importan solo de aquí."""

from agent_core.registry.blobs import BlobStore, InMemoryBlobStore
from agent_core.registry.directory import RegistryDirectory
from agent_core.registry.errors import HTTP_STATUS, IntegrityError, RegistryError, RegistryErrorCode
from agent_core.registry.evaluation import (
    PLATFORM_GUARDRAILS,
    EvalPort,
    EvalReport,
    EvalRequest,
    EvalTarget,
    GateItem,
    HarnessUnavailable,
    Judge,
    LocalSandbox,
    SandboxHandle,
    SandboxPort,
    ScenarioEvaluator,
    ScenarioHarness,
    Yardstick,
    YardstickChange,
)
from agent_core.registry.memory import InMemoryRegistryStore
from agent_core.registry.models import (
    AuditContext,
    DraftWrite,
    EntityDraft,
    Origin,
    Proposal,
    ProposalState,
    VersionDocs,
    VersionRef,
    WriteRecord,
)
from agent_core.registry.postgres.runtime import PostgresRegistry
from agent_core.registry.postgres.store import PgRegistryStore, apply_registry_schema
from agent_core.registry.quotas import DEFAULT_QUOTAS, Quotas
from agent_core.registry.s3_blobs import ReadThroughBlobStore, S3BlobStore
from agent_core.registry.service import RegistryService, RunReleaseReader
from agent_core.registry.snapshot import SnapshotRegistry
from agent_core.registry.store import RegistryStore
from agent_core.registry.suite import (
    Assertion,
    DatasetScenario,
    EvalSuite,
    MetricThreshold,
    SandboxSeed,
    Scenario,
    Step,
    SuiteProblem,
    SuiteProblemCode,
    suite_problems,
)

__all__ = [
    "DEFAULT_QUOTAS",
    "HTTP_STATUS",
    "PLATFORM_GUARDRAILS",
    "Assertion",
    "AuditContext",
    "BlobStore",
    "DatasetScenario",
    "DraftWrite",
    "EntityDraft",
    "EvalPort",
    "EvalReport",
    "EvalRequest",
    "EvalSuite",
    "EvalTarget",
    "GateItem",
    "HarnessUnavailable",
    "InMemoryBlobStore",
    "InMemoryRegistryStore",
    "IntegrityError",
    "Judge",
    "LocalSandbox",
    "MetricThreshold",
    "Origin",
    "PgRegistryStore",
    "PostgresRegistry",
    "Proposal",
    "ProposalState",
    "Quotas",
    "ReadThroughBlobStore",
    "RegistryDirectory",
    "RegistryError",
    "RegistryErrorCode",
    "RegistryService",
    "RegistryStore",
    "RunReleaseReader",
    "S3BlobStore",
    "SandboxHandle",
    "SandboxPort",
    "SandboxSeed",
    "Scenario",
    "ScenarioEvaluator",
    "ScenarioHarness",
    "SnapshotRegistry",
    "Step",
    "SuiteProblem",
    "SuiteProblemCode",
    "VersionDocs",
    "VersionRef",
    "WriteRecord",
    "Yardstick",
    "YardstickChange",
    "apply_registry_schema",
    "suite_problems",
]
