"""Servicio sobre el almacén en memoria con la release de demo sembrada como base de `staging`."""

from dataclasses import dataclass, field

from agent_core.domain import EntityKind
from agent_core.registry.candidate import release_hash
from agent_core.registry.entities import content_hash, encode_entity, version_ref
from agent_core.registry.evaluation.ports import EvalTarget
from agent_core.registry.evaluation.report import EvalReport, SuiteMetrics
from agent_core.registry.memory import InMemoryRegistryStore
from agent_core.registry.models import AliasChange, StoredRelease, StoredVersion, VersionDocs
from agent_core.registry.service import RegistryService
from agent_core.registry.suite import EvalSuite
from testing.builders import NOW
from testing.fakes.clock import FakeClock
from testing.fakes.ids import FakeIds
from tests.registry.helpers import AGENT, demo_pinned, suite_draft

ZERO = {"unverified_writes": 0, "unsupported_success": 0, "sensitive_leaks": 0}


@dataclass
class FakeEvaluator:
    """`EvalPort` guionado: devuelve los reportes en orden; por defecto, `pass`."""
    reports: list[EvalReport] = field(default_factory=list)
    calls: list[tuple[str, str | None]] = field(default_factory=list)

    def run(self, suite: EvalSuite, candidate: EvalTarget, base: EvalTarget | None) -> EvalReport:
        self.calls.append((candidate.release.id, base.release.id if base else None))
        if self.reports:
            return self.reports.pop(0)
        m = SuiteMetrics(primary=1, guardrails=ZERO, runs=1)  # type: ignore[arg-type]
        return EvalReport(verdict="pass", candidate=m, base=m)


def seed_demo(store: InMemoryRegistryStore, *, aliases: tuple[str, ...] = ("staging", "prod")) -> str:
    pinned = demo_pinned()
    docs = VersionDocs(description="semilla", rationale="", changelog="")
    with store.transaction() as tx:
        refs = []
        for entity in pinned.entities:
            tx.blobs.put(encode_entity(entity))
            ref = version_ref(entity)
            refs.append(ref)
            tx.insert_version(StoredVersion(ref=ref, content_hash=content_hash(entity), docs=docs,
                                            proposal_id=None, created_by="seed", created_at=NOW))
        release = pinned.release.model_copy(update={"id": "rel-demo"})
        tx.insert_release(StoredRelease(release=release, release_hash=release_hash(release), agent_id=AGENT,
                                        agent_version=release.entities[EntityKind.agent][AGENT],
                                        base_release_id=None, proposal_id=None, published_by="seed",
                                        published_at=NOW), refs)
        for alias in aliases:
            tx.set_alias(AliasChange(agent_id=AGENT, alias=alias, before=None, after="rel-demo", actor="seed",
                                     reason="semilla", at=NOW))
    return "rel-demo"


@dataclass
class World:
    store: InMemoryRegistryStore = field(default_factory=InMemoryRegistryStore)
    evaluator: FakeEvaluator = field(default_factory=FakeEvaluator)
    clock: FakeClock = field(default_factory=FakeClock)
    ids: FakeIds = field(default_factory=FakeIds)

    def __post_init__(self) -> None:
        seed_demo(self.store)
        self.service = RegistryService(self.store, self.evaluator, self.clock, self.ids)


SUITE = suite_draft()
