"""`RegistryService` (spec §4, §5, §7.2): máquina de estados de propuestas sobre un `RegistryStore`."""

from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, ConfigDict, ValidationError

from agent_core.domain import EntityKind, Principal, RegistryEntity, Release, loads
from agent_core.flows import Violation
from agent_core.ports import Clock, IdKind, IdSource
from agent_core.registry.candidate import (
    Candidate,
    CandidateError,
    build_candidate,
    parse_semver,
    release_hash,
)
from agent_core.registry.entities import AnyEntity, content_hash, decode_entity, encode_entity, version_ref
from agent_core.registry.errors import RegistryError, RegistryErrorCode
from agent_core.registry.evaluation.ports import EvalPort, EvalTarget
from agent_core.registry.evaluation.report import EvalReport
from agent_core.registry.models import (
    AliasChange,
    Approval,
    ChangedRef,
    EntityDraft,
    EntityInRelease,
    EntityVersion,
    EvalRun,
    Origin,
    Proposal,
    ProposalState,
    RegistryEvent,
    ReleaseDetail,
    ReleaseDiff,
    RunLineage,
    StoredRelease,
    StoredVersion,
    VersionDocs,
    VersionRef,
    VersionSummary,
)
from agent_core.registry.roles import actor_id, require_approver, require_constructor
from agent_core.registry.snapshot import SnapshotRegistry
from agent_core.registry.store import RegistryStore, RegistryTx
from agent_core.registry.suite import EvalSuite
from agent_core.registry.validation import DEFAULT_LIMITS, Limits, check_draft_limits, validate_candidate
from agent_core.registry.yaml_io import dump_entities, dump_release, load_seed


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

    def _rebuild(self, tx: RegistryTx, p: Proposal, detail: str) -> Candidate:
        """Rearma la candidata congelada; si ya no es válida (p. ej. otra publicación ocupó una versión del
        borrador) es `candidate_changed` (spec §5.4.2), con las violaciones (sin valores de entrada)."""
        try:
            return self._candidate(tx, p)
        except CandidateError as exc:
            raise RegistryError(RegistryErrorCode.candidate_changed,
                                f"{detail}: {len(exc.violations)} violaciones",
                                payload=_violations_payload(exc.violations)) from exc  # type: ignore[arg-type]

    # --- construcción (rol constructor) ------------------------------------------------------------------

    def create_proposal(self, actor: Principal, agent_id: str, origin: Origin, title: str) -> Proposal:
        require_constructor(actor)
        with self._store.transaction() as tx:
            base = tx.get_alias(agent_id, "staging")
            try:
                p = Proposal(proposal_id=self._ids.new_id(IdKind.proposal), agent_id=agent_id, origin=origin,
                             state=ProposalState.draft, base_release_id=base, title=title,
                             created_by=actor_id(actor), updated_at=self._clock.now())
            except ValidationError as exc:  # sin `input`: el título o el id pueden traer texto libre
                errors = exc.errors(include_input=False)
                fields = sorted({str(e["loc"][0]) for e in errors if e["loc"]})
                raise RegistryError(RegistryErrorCode.validation_failed, "la propuesta no es válida",
                                    payload=[{"rule": "REG-PROPOSAL", "path": f, "flow": None,
                                              "node_id": None, "message": f"`{f}` no es válido"}
                                             for f in fields]) from None
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

    # --- evaluación --------------------------------------------------------------------------------------

    def _suite(self, tx: RegistryTx, cand: Candidate, suite_id: str, version: str | None) -> EvalSuite:
        for suite in cand.suites:
            if suite.id == suite_id and version in (None, suite.version):
                return suite
        versions = sorted(tx.list_versions("eval_suite", suite_id),
                          key=lambda v: tuple(int(x) for x in v.ref.version.split(".")))
        chosen = [v for v in versions if version in (None, v.ref.version)]
        if not chosen:
            raise RegistryError(RegistryErrorCode.not_found, f"no existe la suite {suite_id}")
        entity = self._load(tx, chosen[-1].ref)
        assert isinstance(entity, EvalSuite)
        if entity.agent_id != cand.agent_id:  # spec §5.2.6; las del borrador ya las valida la candidata
            raise RegistryError(RegistryErrorCode.validation_failed,
                                f"la suite {suite_id} no es del agente {cand.agent_id}")
        return entity

    def evaluate(self, actor: Principal, proposal_id: str, suite_id: str,
                 suite_version: str | None = None) -> EvalReport:
        require_constructor(actor)
        with self._store.transaction() as tx:
            p = self._proposal(tx, proposal_id, for_update=False)
            self._expect(p, ProposalState.candidate)
            cand = self._rebuild(tx, p, "la candidata cambió desde freeze")
            if cand.candidate_hash != p.candidate_hash:
                raise RegistryError(RegistryErrorCode.candidate_changed, "la candidata cambió desde freeze")
            suite = self._suite(tx, cand, suite_id, suite_version)
            base_release, base_entities = self._base(tx, p.base_release_id)
        candidate_target = EvalTarget("candidate", cand.release,
                                      SnapshotRegistry(cand.release, cand.entities))
        base_target = (EvalTarget("base", base_release, SnapshotRegistry(base_release, base_entities))
                       if base_release is not None else None)
        report = self._evaluator.run(suite, candidate_target, base_target)  # fuera de la transacción

        with self._store.transaction() as tx:
            p = self._proposal(tx, proposal_id)
            if p.state is not ProposalState.candidate or p.candidate_hash != cand.candidate_hash:
                raise RegistryError(RegistryErrorCode.candidate_changed,
                                    "la propuesta cambió durante la evaluación")
            tx.insert_eval_run(EvalRun(
                eval_run_id=self._ids.new_id(IdKind.eval_run), proposal_id=proposal_id,
                candidate_hash=cand.candidate_hash, base_release_id=p.base_release_id,
                suite=version_ref(suite), verdict=report.verdict, report=report, at=self._clock.now()))
            if report.verdict == "pass":
                p = self._save(tx, p, state=ProposalState.evaluated)
            elif report.verdict == "fail":
                p = self._save(tx, p, state=ProposalState.draft, candidate_hash=None, rev=p.rev + 1)
            self._event(tx, "evaluated", actor, p)
        if report.verdict == "fail":
            raise RegistryError(RegistryErrorCode.gate_failed, "la candidata no pasa el gate",
                                payload=report.model_dump(mode="json"))
        return report

    # --- decisiones humanas ------------------------------------------------------------------------------

    def approve(self, actor: Principal, proposal_id: str, candidate_hash: str) -> Approval:
        require_approver(actor)
        with self._store.transaction() as tx:
            p = self._proposal(tx, proposal_id)
            self._expect(p, ProposalState.evaluated)
            if candidate_hash != p.candidate_hash:
                raise RegistryError(RegistryErrorCode.candidate_changed,
                                    "la candidata aprobada no es la vigente")
            run = tx.latest_eval_run(proposal_id, candidate_hash)
            if run is None or run.verdict != "pass":
                raise RegistryError(RegistryErrorCode.gate_failed, "no hay una evaluación aprobada vigente")
            approval = Approval(proposal_id=proposal_id, candidate_hash=candidate_hash, actor=actor_id(actor),
                                decision="approved", at=self._clock.now())
            tx.insert_approval(approval)
            p = self._save(tx, p, state=ProposalState.approved)
            self._event(tx, "approved", actor, p)
            return approval

    def reject(self, actor: Principal, proposal_id: str, reason: str) -> Proposal:
        require_approver(actor)
        with self._store.transaction() as tx:
            p = self._proposal(tx, proposal_id)
            self._expect(p, ProposalState.evaluated)  # spec §4; para deshacer una aprobación, `reopen`
            tx.insert_approval(Approval(proposal_id=proposal_id, candidate_hash=p.candidate_hash or "",
                                        actor=actor_id(actor), decision="rejected", reason=reason[:2000],
                                        at=self._clock.now()))
            self._event(tx, "rejected", actor, p)
            return self._save(tx, p, state=ProposalState.draft, candidate_hash=None, rev=p.rev + 1)

    def publish(self, actor: Principal, proposal_id: str, idempotency_key: str) -> ReleaseDetail:
        require_approver(actor)
        stale = False
        with self._store.transaction() as tx:
            prior = tx.get_publish_key(idempotency_key)
            if prior is not None:
                if prior[0] != proposal_id:
                    raise RegistryError(RegistryErrorCode.illegal_transition,
                                        "la Idempotency-Key ya se usó con otra propuesta")
                return self._detail(tx, prior[1])
            p = self._proposal(tx, proposal_id)
            self._expect(p, ProposalState.approved)
            tx.lock_agent(p.agent_id)  # la primera publicación no tiene alias que bloquear con FOR UPDATE
            current = tx.get_alias(p.agent_id, "staging", for_update=True)
            if current != p.base_release_id:
                self._save(tx, p, state=ProposalState.draft, candidate_hash=None, base_release_id=current,
                           rev=p.rev + 1)
                self._event(tx, "proposal_staled", actor, p)
                stale = True
            else:
                release_id = self._publish_in_tx(tx, actor, p)
                tx.put_publish_key(idempotency_key, proposal_id, release_id)
                return self._detail(tx, release_id)
        assert stale
        raise RegistryError(RegistryErrorCode.proposal_stale,
                            "staging cambió desde que se creó la propuesta; congela y evalúa de nuevo")

    def _publish_in_tx(self, tx: RegistryTx, actor: Principal, p: Proposal) -> str:
        cand = self._rebuild(tx, p, "la candidata cambió desde la aprobación")
        if cand.candidate_hash != p.candidate_hash:
            raise RegistryError(RegistryErrorCode.candidate_changed,
                                "la candidata cambió desde la aprobación")
        approval = tx.latest_approval(p.proposal_id, cand.candidate_hash)
        if approval is None or approval.decision != "approved":
            raise RegistryError(RegistryErrorCode.gate_failed, "falta una aprobación vigente")
        release_id = release_id_for(cand.candidate_hash)
        if tx.get_release(release_id) is not None:
            raise RegistryError(RegistryErrorCode.illegal_transition, "esa release ya existe")
        now, who = self._clock.now(), actor_id(actor)
        by_ref = {version_ref(e): e for e in [*cand.entities, *cand.suites]}
        for ref in cand.new_versions:
            entity = by_ref[ref]
            tx.blobs.put(encode_entity(entity))
            tx.insert_version(StoredVersion(ref=ref, content_hash=content_hash(entity),
                                            docs=cand.docs[ref], proposal_id=p.proposal_id,
                                            created_by=p.created_by, created_at=now))
        release = cand.release.model_copy(update={"id": release_id})
        # Las suites se guardan como versiones, pero no entran en la release (spec §3.1).
        refs = sorted((version_ref(e) for e in cand.entities), key=str)
        tx.insert_release(StoredRelease(release=release, release_hash=cand.release_hash, agent_id=p.agent_id,
                                        agent_version=cand.agent_version, base_release_id=p.base_release_id,
                                        proposal_id=p.proposal_id, published_by=who, published_at=now), refs)
        tx.set_alias(AliasChange(agent_id=p.agent_id, alias="staging", before=p.base_release_id,
                                 after=release_id, actor=who, reason=f"publica {p.proposal_id}", at=now))
        self._save(tx, p, state=ProposalState.published)
        self._event(tx, "published", actor, p, release_id)
        return release_id

    def promote(self, actor: Principal, agent_id: str, alias: str, release_id: str,
                reason: str = "") -> AliasChange:
        require_approver(actor)
        with self._store.transaction() as tx:
            stored = tx.get_release(release_id)
            if stored is None:
                raise RegistryError(RegistryErrorCode.not_found, "la release no existe")
            if tx.release_status(release_id) != "active":
                raise RegistryError(RegistryErrorCode.illegal_transition,
                                    "no se promueve una release revocada")
            if agent_id not in stored.release.entities.get(EntityKind.agent, {}):
                raise RegistryError(RegistryErrorCode.illegal_transition, "la release no contiene al agente")
            change = AliasChange(agent_id=agent_id, alias=alias,
                                 before=tx.get_alias(agent_id, alias, for_update=True), after=release_id,
                                 actor=actor_id(actor), reason=reason[:500], at=self._clock.now())
            tx.set_alias(change)
            self._event(tx, "promoted", actor, release_id=release_id)
            return change

    def revoke(self, actor: Principal, release_id: str, reason: str) -> ReleaseDetail:
        require_approver(actor)
        with self._store.transaction() as tx:
            if tx.get_release(release_id) is None:
                raise RegistryError(RegistryErrorCode.not_found, "la release no existe")
            if tx.release_status(release_id) != "active":
                raise RegistryError(RegistryErrorCode.illegal_transition, "la release ya está revocada")
            if any(alias == "prod" for _, alias in tx.aliases_to(release_id)):
                raise RegistryError(RegistryErrorCode.illegal_transition,
                                    "prod apunta a esta release: promueve otra antes de revocarla")
            tx.set_release_status(release_id, "revoked")
            self._event(tx, "revoked", actor, release_id=release_id)
            return self._detail(tx, release_id)

    # --- lecturas usadas por las decisiones ---------------------------------------------------------------

    def _detail(self, tx: RegistryTx, release_id: str) -> ReleaseDetail:
        stored = tx.get_release(release_id)
        if stored is None:
            raise RegistryError(RegistryErrorCode.not_found, "la release no existe")
        base_refs = set(tx.release_refs(stored.base_release_id)) if stored.base_release_id else set()
        has_base = stored.base_release_id is not None
        entities = []
        for ref in tx.release_refs(release_id):
            version = tx.get_version(ref)
            assert version is not None
            entities.append(EntityInRelease(ref=ref, content_hash=version.content_hash, docs=version.docs,
                                            changed_vs_base=has_base and ref not in base_refs))
        status = tx.release_status(release_id) or "active"
        ks = stored.release.knowledge_snapshot
        return ReleaseDetail(release_id=release_id, status=status, agent_id=stored.agent_id,
                             entities=entities, knowledge_snapshot=str(ks) if ks else None,
                             proposal_id=stored.proposal_id,
                             base_release_id=stored.base_release_id, published_by=stored.published_by,
                             published_at=stored.published_at)

    def get_release(self, release_id: str) -> ReleaseDetail:
        with self._store.transaction() as tx:
            return self._detail(tx, release_id)

    # --- lecturas, diff y linaje ---------------------------------------------------------------------------

    def _sorted_versions(self, tx: RegistryTx, kind: str, entity_id: str) -> list[StoredVersion]:
        found = tx.list_versions(kind, entity_id)
        return sorted(found, key=lambda v: parse_semver(v.ref.version) or (0, 0, 0))

    def get_entity(self, kind: str, entity_id: str, version: str | None = None) -> EntityVersion:
        with self._store.transaction() as tx:
            ordered = self._sorted_versions(tx, kind, entity_id)
            versions = [v for v in ordered if version in (None, v.ref.version)]
            if not versions:
                raise RegistryError(RegistryErrorCode.not_found, "la entidad o su versión no existe")
            v = versions[-1]
            content = loads(tx.blobs.get(v.content_hash))
            assert isinstance(content, dict)
            return EntityVersion(ref=v.ref, content=content, content_hash=v.content_hash, docs=v.docs,
                                 created_by=v.created_by, created_at=v.created_at)

    def list_versions(self, kind: str, entity_id: str) -> list[VersionSummary]:
        with self._store.transaction() as tx:
            return [
                VersionSummary(ref=v.ref, content_hash=v.content_hash, docs=v.docs, created_by=v.created_by,
                               created_at=v.created_at)
                for v in self._sorted_versions(tx, kind, entity_id)]

    def diff_releases(self, a: str, b: str) -> ReleaseDiff:
        with self._store.transaction() as tx:
            for rid in (a, b):
                if tx.get_release(rid) is None:
                    raise RegistryError(RegistryErrorCode.not_found, "la release no existe")
            left = {(r.kind, r.id): r for r in tx.release_refs(a)}
            right = {(r.kind, r.id): r for r in tx.release_refs(b)}
            changed = []
            for key in sorted(left.keys() & right.keys()):
                if left[key] != right[key]:
                    stored = tx.get_version(right[key])
                    assert stored is not None
                    changed.append(ChangedRef(before=left[key], after=right[key], docs=stored.docs))
            added = [right[k] for k in sorted(right.keys() - left.keys())]
            removed = [left[k] for k in sorted(left.keys() - right.keys())]
            return ReleaseDiff(a=a, b=b, added=added, removed=removed, changed=changed)

    def lineage_for_run(self, actor: Principal, run_id: str) -> RunLineage:
        actor_id(actor)
        release_id = self._runs.release_of(run_id) if self._runs is not None else None
        if release_id is None:
            raise RegistryError(RegistryErrorCode.not_found, "el run no existe")
        with self._store.transaction() as tx:
            detail = self._detail(tx, release_id)
            stored = tx.get_release(release_id)
            assert stored is not None
            verdict = built_by = approved_by = None
            if stored.proposal_id is not None:
                p = tx.get_proposal(stored.proposal_id)
                built_by = p.created_by if p else None
                h = p.candidate_hash if p else None
                if h is not None:
                    run = tx.latest_eval_run(stored.proposal_id, h)
                    approval = tx.latest_approval(stored.proposal_id, h)
                    verdict = run.verdict if run else None
                    approved_by = approval.actor if approval and approval.decision == "approved" else None
            return RunLineage(run_id=run_id, release_id=release_id, entities=detail.entities,
                              knowledge_snapshot=detail.knowledge_snapshot, proposal_id=stored.proposal_id,
                              eval_verdict=verdict, built_by=built_by, approved_by=approved_by,
                              published_at=stored.published_at)

    # --- importación y exportación YAML ----------------------------------------------------------------

    def import_seed(self, actor: Principal, root: Path) -> list[ReleaseDetail]:
        require_approver(actor)
        pinned_list, suites = load_seed(root)
        details: list[ReleaseDetail] = []
        with self._store.transaction() as tx:
            for agent_id in sorted({a for pinned in pinned_list for a in pinned.aliases}):
                tx.lock_agent(agent_id)  # orden fijo: dos importaciones a la vez no se bloquean entre sí
            now, who = self._clock.now(), actor_id(actor)
            seed_docs = VersionDocs(description="Importado desde YAML", rationale="semilla", changelog="")
            # Las suites se guardan como versiones, pero no entran en la release (spec §3.1).
            for suite in suites:
                self._insert_if_new(tx, suite, seed_docs, None, who, now)
            for pinned in pinned_list:
                if len(pinned.aliases) != 1:
                    raise RegistryError(RegistryErrorCode.validation_failed,
                                        "una release por agente en esta entrega")
                [agent_id] = list(pinned.aliases)
                if tx.get_alias(agent_id, "staging") is not None:
                    raise RegistryError(RegistryErrorCode.illegal_transition,
                                        f"el agente {agent_id} ya tiene releases; usa una propuesta")
                refs = [self._insert_if_new(tx, e, seed_docs, None, who, now) for e in pinned.entities]
                digest = release_hash(pinned.release)
                release_id = release_id_for(digest)
                release = pinned.release.model_copy(update={"id": release_id})
                tx.insert_release(StoredRelease(
                    release=release, release_hash=digest, agent_id=agent_id,
                    agent_version=release.entities[EntityKind.agent][agent_id], base_release_id=None,
                    proposal_id=None, published_by=who, published_at=now), sorted(refs, key=str))
                for alias in ("staging", "prod"):
                    tx.set_alias(AliasChange(agent_id=agent_id, alias=alias, before=None, after=release_id,
                                             actor=who, reason="importación inicial", at=now))
                self._event(tx, "imported", actor, release_id=release_id)
                details.append(self._detail(tx, release_id))
        return details

    def _insert_if_new(self, tx: RegistryTx, entity: AnyEntity, docs: VersionDocs, proposal_id: str | None,
                       who: str, now: datetime) -> VersionRef:
        ref, digest = version_ref(entity), content_hash(entity)
        stored = tx.get_version(ref)
        if stored is None:
            tx.blobs.put(encode_entity(entity))
            tx.insert_version(StoredVersion(ref=ref, content_hash=digest, docs=docs, proposal_id=proposal_id,
                                            created_by=who, created_at=now))
        elif stored.content_hash != digest:
            raise RegistryError(RegistryErrorCode.validation_failed, f"{ref} ya existe con otro contenido")
        return ref

    def export(self, release_id: str) -> dict[str, bytes]:
        with self._store.transaction() as tx:
            stored = tx.get_release(release_id)
            if stored is None:
                raise RegistryError(RegistryErrorCode.not_found, "la release no existe")
            entities = [self._load(tx, ref) for ref in tx.release_refs(release_id)]
        return {**dump_entities(entities), **dump_release(stored.release, stored.agent_id)}
