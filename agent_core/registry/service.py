"""`RegistryService` (spec §4, §5, §7.2): máquina de estados de propuestas sobre un `RegistryStore`."""

from collections.abc import Sequence
from typing import Protocol

from pydantic import BaseModel, ConfigDict

from agent_core.domain import Principal, RegistryEntity, Release
from agent_core.flows import Violation
from agent_core.ports import Clock, IdKind, IdSource
from agent_core.registry.candidate import Candidate, CandidateError, build_candidate
from agent_core.registry.entities import AnyEntity, decode_entity
from agent_core.registry.errors import RegistryError, RegistryErrorCode
from agent_core.registry.evaluation.ports import EvalPort
from agent_core.registry.models import (
    EntityDraft,
    EvalRun,
    Origin,
    Proposal,
    ProposalState,
    RegistryEvent,
    VersionRef,
)
from agent_core.registry.roles import actor_id, require_constructor
from agent_core.registry.store import RegistryStore, RegistryTx
from agent_core.registry.suite import EvalSuite
from agent_core.registry.validation import DEFAULT_LIMITS, Limits, check_draft_limits, validate_candidate


class RunReleaseReader(Protocol):
    def release_of(self, run_id: str) -> str | None: ...


class _V(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ValidationReport(_V):
    violations: list[Violation]
    candidate_hash: str | None
    auto_bumped: list[VersionRef]


class CandidateView(_V):
    proposal_id: str
    candidate_hash: str
    release_id_preview: str
    new_versions: list[VersionRef]
    auto_bumped: list[VersionRef]


class ProposalDetail(_V):
    proposal: Proposal
    changes: list[EntityDraft]
    last_eval: EvalRun | None


def release_id_for(candidate_hash: str) -> str:
    return "rel-" + candidate_hash[:16]


def _violations_payload(violations: Sequence[Violation]) -> list[dict[str, str | None]]:
    return [{"rule": v.rule, "path": v.path, "flow": v.flow, "node_id": v.node_id, "message": v.message}
            for v in violations]


class RegistryService:
    def __init__(self, store: RegistryStore, evaluator: EvalPort, clock: Clock, ids: IdSource,
                 runs: RunReleaseReader | None = None, limits: Limits = DEFAULT_LIMITS) -> None:
        self._store, self._evaluator, self._clock, self._ids = store, evaluator, clock, ids
        self._runs, self._limits = runs, limits

    # --- helpers ---------------------------------------------------------------------------------------

    def _proposal(self, tx: RegistryTx, proposal_id: str, *, for_update: bool = True) -> Proposal:
        p = tx.get_proposal(proposal_id, for_update=for_update)
        if p is None:
            raise RegistryError(RegistryErrorCode.not_found, "la propuesta no existe")
        return p

    @staticmethod
    def _expect(p: Proposal, *states: ProposalState) -> None:
        if p.state not in states:
            allowed = ", ".join(s.value for s in states)
            raise RegistryError(RegistryErrorCode.illegal_transition,
                                f"la propuesta está en {p.state.value}; se necesita {allowed}")

    def _save(self, tx: RegistryTx, p: Proposal, **update: object) -> Proposal:
        new = p.model_copy(update={**update, "updated_at": self._clock.now()})
        tx.save_proposal(new)
        return new

    def _event(self, tx: RegistryTx, type_: str, actor: Principal, p: Proposal | None = None,
               release_id: str | None = None) -> None:
        tx.append_event(RegistryEvent(
            type=type_, actor=actor.id or "", principal_type=actor.type.value,
            origin=p.origin.value if p else None, proposal_id=p.proposal_id if p else None,
            candidate_hash=p.candidate_hash if p else None, release_id=release_id, at=self._clock.now()))

    def _load(self, tx: RegistryTx, ref: VersionRef) -> AnyEntity:
        stored = tx.get_version(ref)
        if stored is None:
            raise RegistryError(RegistryErrorCode.not_found, f"no existe {ref}")
        return decode_entity(ref.kind, tx.blobs.get(stored.content_hash))

    def _base(self, tx: RegistryTx, release_id: str | None) -> tuple[Release | None, list[RegistryEntity]]:
        if release_id is None:
            return None, []
        stored = tx.get_release(release_id)
        if stored is None:
            raise RegistryError(RegistryErrorCode.not_found, "la release base no existe")
        entities = [self._load(tx, ref) for ref in tx.release_refs(release_id)]
        return stored.release, [e for e in entities if not isinstance(e, EvalSuite)]

    def _candidate(self, tx: RegistryTx, p: Proposal) -> Candidate:
        base, entities = self._base(tx, p.base_release_id)
        drafts = tx.get_changes(p.proposal_id)

        def published(ref: VersionRef) -> str | None:
            stored = tx.get_version(ref)
            return stored.content_hash if stored else None

        cand = build_candidate(agent_id=p.agent_id, base=base, base_entities=entities, drafts=drafts,
                               published_hash=published)
        base_versions = ({(k.value, i): v for k, by in base.entities.items() for i, v in by.items()}
                         if base else {})
        problems = validate_candidate(cand, base_versions=base_versions,
                                      drafted={(d.kind, d.id) for d in drafts}, limits=self._limits)
        if problems:
            raise CandidateError(problems)
        return cand

    # --- construcción (rol constructor) ------------------------------------------------------------------

    def create_proposal(self, actor: Principal, agent_id: str, origin: Origin, title: str) -> Proposal:
        require_constructor(actor)
        with self._store.transaction() as tx:
            p = Proposal(proposal_id=self._ids.new_id(IdKind.proposal), agent_id=agent_id, origin=origin,
                         state=ProposalState.draft, base_release_id=tx.get_alias(agent_id, "staging"),
                         title=title, created_by=actor_id(actor), updated_at=self._clock.now())
            tx.save_proposal(p)
            self._event(tx, "proposal_created", actor, p)
            return p

    def put_draft(self, actor: Principal, proposal_id: str, changes: Sequence[EntityDraft],
                  expected_rev: int) -> Proposal:
        require_constructor(actor)
        problems = check_draft_limits(changes, self._limits)
        if problems:
            raise RegistryError(RegistryErrorCode.validation_failed, "el borrador excede los límites",
                                payload=_violations_payload(problems))  # type: ignore[arg-type]
        with self._store.transaction() as tx:
            p = self._proposal(tx, proposal_id)
            self._expect(p, ProposalState.draft)
            if p.rev != expected_rev:
                raise RegistryError(RegistryErrorCode.proposal_stale,
                                    f"la propuesta va en la revisión {p.rev}, no en {expected_rev}")
            tx.replace_changes(proposal_id, changes)
            p = self._save(tx, p, rev=p.rev + 1)
            self._event(tx, "draft_updated", actor, p)
            return p

    def validate(self, actor: Principal, proposal_id: str) -> ValidationReport:
        require_constructor(actor)
        with self._store.transaction() as tx:
            p = self._proposal(tx, proposal_id, for_update=False)
            try:
                cand = self._candidate(tx, p)
            except CandidateError as exc:
                return ValidationReport(violations=exc.violations, candidate_hash=None, auto_bumped=[])
            return ValidationReport(violations=[], candidate_hash=cand.candidate_hash,
                                    auto_bumped=list(cand.auto_bumped))

    def freeze(self, actor: Principal, proposal_id: str) -> CandidateView:
        require_constructor(actor)
        with self._store.transaction() as tx:
            p = self._proposal(tx, proposal_id)
            self._expect(p, ProposalState.draft)
            try:
                cand = self._candidate(tx, p)
            except CandidateError as exc:
                failure = RegistryError(RegistryErrorCode.validation_failed,
                                        f"la candidata tiene {len(exc.violations)} violaciones",
                                        payload=_violations_payload(exc.violations))  # type: ignore[arg-type]
            else:
                p = self._save(tx, p, state=ProposalState.candidate, candidate_hash=cand.candidate_hash)
                self._event(tx, "frozen", actor, p)
                return CandidateView(proposal_id=proposal_id, candidate_hash=cand.candidate_hash,
                                     release_id_preview=release_id_for(cand.candidate_hash),
                                     new_versions=list(cand.new_versions), auto_bumped=list(cand.auto_bumped))
        raise failure

    def reopen(self, actor: Principal, proposal_id: str) -> Proposal:
        require_constructor(actor)
        with self._store.transaction() as tx:
            p = self._proposal(tx, proposal_id)
            self._expect(p, ProposalState.candidate, ProposalState.evaluated, ProposalState.approved)
            p = self._save(tx, p, state=ProposalState.draft, candidate_hash=None, rev=p.rev + 1)
            self._event(tx, "reopened", actor, p)
            return p

    def get_proposal(self, proposal_id: str) -> ProposalDetail:
        with self._store.transaction() as tx:
            p = self._proposal(tx, proposal_id, for_update=False)
            last = tx.latest_eval_run(p.proposal_id, p.candidate_hash) if p.candidate_hash else None
            return ProposalDetail(proposal=p, changes=tx.get_changes(proposal_id), last_eval=last)
