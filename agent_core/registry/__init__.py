"""Registry (unidad 2): entidades, versionado y publicación.

Spec: docs/specs/2026-09-29-registry-design.md (rev. 2). ADR 0017 y 0018.
Otros módulos importan solo de aquí."""

from agent_core.registry.blobs import BlobStore, InMemoryBlobStore
from agent_core.registry.errors import HTTP_STATUS, IntegrityError, RegistryError, RegistryErrorCode
from agent_core.registry.evaluation import (
    EvalPort,
    EvalReport,
    EvalTarget,
    HarnessUnavailable,
    Judge,
    LocalSandbox,
    SandboxHandle,
    SandboxPort,
    ScenarioEvaluator,
    ScenarioHarness,
)
from agent_core.registry.memory import InMemoryRegistryStore
from agent_core.registry.models import EntityDraft, Origin, ProposalState, VersionDocs, VersionRef
from agent_core.registry.postgres.runtime import PostgresRegistry
from agent_core.registry.postgres.store import PgRegistryStore, apply_registry_schema
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
    "HTTP_STATUS",
    "Assertion",
    "BlobStore",
    "DatasetScenario",
    "EntityDraft",
    "EvalPort",
    "EvalReport",
    "EvalSuite",
    "EvalTarget",
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
    "ProposalState",
    "RegistryError",
    "RegistryErrorCode",
    "RegistryService",
    "RegistryStore",
    "RunReleaseReader",
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
    "apply_registry_schema",
    "suite_problems",
]
