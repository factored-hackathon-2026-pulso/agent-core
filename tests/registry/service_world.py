"""Servicio sobre el almacén en memoria con la release de demo sembrada como base de `staging`."""

from dataclasses import dataclass, field

from agent_core.domain import EntityKind
from agent_core.registry.candidate import release_hash
from agent_core.registry.entities import content_hash, encode_entity, version_ref
from agent_core.registry.evaluation.ports import EvalRequest
from agent_core.registry.evaluation.report import EvalReport
from agent_core.registry.memory import InMemoryRegistryStore
from agent_core.registry.models import (
    AliasChange,
    EntityDraft,
    Origin,
    StoredRelease,
    StoredVersion,
    VersionDocs,
)
from agent_core.registry.service import RegistryService
from testing.builders import NOW
from testing.fakes.clock import FakeClock
from testing.fakes.ids import FakeIds
from tests.registry.helpers import AGENT, demo_pinned, human, suite_draft


@dataclass
class FakeEvaluator:
    """Scripted `EvalPort`: returns the reports in order (default: `pass`) and records each request."""
    reports: list[EvalReport] = field(default_factory=list)
    calls: list[tuple[str, str | None]] = field(default_factory=list)
    requests: list[EvalRequest] = field(default_factory=list)

    def run(self, request: EvalRequest) -> EvalReport:
        self.requests.append(request)
        self.calls.append((request.candidate.release.id, request.base.release.id if request.base else None))
        if self.reports:
            return self.reports.pop(0)
        return EvalReport(verdict="pass")


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


ANA = human()


def publish_cycle(w: World, drafts: list[EntityDraft], *, key: str = "k") -> str:
    """One person's full cycle: create, draft, freeze, evaluate with `disputas-suite`, approve and publish.
    Returns the published release id."""
    p = w.service.create_proposal(ANA, AGENT, Origin.manual, "change")
    w.service.put_draft(ANA, p.proposal_id, drafts, expected_rev=0)
    w.service.freeze(ANA, p.proposal_id)
    w.service.evaluate(ANA, p.proposal_id, "disputas-suite")
    h = w.service.get_proposal(p.proposal_id).proposal.candidate_hash or ""
    w.service.approve(ANA, p.proposal_id, h)
    return w.service.publish(ANA, p.proposal_id, key).release_id
