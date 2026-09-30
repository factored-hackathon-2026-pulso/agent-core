from decimal import Decimal

import pytest

from agent_core.registry.entities import content_hash, encode_entity, version_ref
from agent_core.registry.errors import RegistryError, RegistryErrorCode
from agent_core.registry.evaluation.report import EvalReport, SuiteMetrics
from agent_core.registry.models import AliasChange, Origin, ProposalState, StoredVersion, VersionRef
from agent_core.registry.suite import EvalSuite
from testing.builders import NOW
from tests.registry.helpers import AGENT, bot, docs, human, prompt_draft, suite_content
from tests.registry.service_world import SUITE, ZERO, World

ANA = human()
# Versiones que agrega la propuesta de `_frozen`: prompt del borrador, cascada (flow y agente) y suite.
NEW_REFS = (VersionRef(kind="prompt", id="p/resumen_radicado", version="1.1.0"),
            VersionRef(kind="flow", id="disputa-cargo", version="1.0.1"),
            VersionRef(kind="agent", id=AGENT, version="1.0.1"),
            VersionRef(kind="eval_suite", id="disputas-suite", version="1.0.0"))


def _frozen(w: World) -> str:
    p = w.service.create_proposal(ANA, AGENT, Origin.manual, "mejorar radicado")
    w.service.put_draft(ANA, p.proposal_id, [prompt_draft(), SUITE], expected_rev=0)
    w.service.freeze(ANA, p.proposal_id)
    return p.proposal_id


def _evaluated(w: World) -> tuple[str, str]:
    pid = _frozen(w)
    w.service.evaluate(ANA, pid, "disputas-suite")
    h = w.service.get_proposal(pid).proposal.candidate_hash
    assert h is not None
    return pid, h


def _report(verdict: str) -> EvalReport:
    m = SuiteMetrics(primary=Decimal(1), guardrails=ZERO, runs=1)
    return EvalReport(verdict=verdict, candidate=m, base=m)  # type: ignore[arg-type]


def _code(info: pytest.ExceptionInfo[RegistryError]) -> RegistryErrorCode:
    return info.value.code


def test_human_completes_cycle_alone() -> None:  # T-REG-14
    w = World()
    pid, h = _evaluated(w)
    w.service.approve(ANA, pid, h)
    detail = w.service.publish(ANA, pid, "key-1")
    assert detail.release_id == "rel-" + h[:16] and detail.status == "active"
    with w.store.transaction() as tx:
        assert tx.get_alias(AGENT, "staging") == detail.release_id
        assert tx.get_alias(AGENT, "prod") == "rel-demo"
        approval = tx.latest_approval(pid, h)
    assert approval is not None and approval.actor == "ana"
    assert w.service.get_proposal(pid).proposal.state is ProposalState.published
    assert {e.ref.id for e in detail.entities if e.changed_vs_base} == {"p/resumen_radicado", "disputa-cargo",
                                                                         AGENT}


def test_evaluation_runs_candidate_against_base() -> None:
    w = World()
    _evaluated(w)
    assert w.evaluator.calls == [("candidate", "rel-demo")]


def test_gate_fail_returns_to_draft_and_raises() -> None:
    w = World()
    w.evaluator.reports.append(_report("fail"))
    pid = _frozen(w)
    with pytest.raises(RegistryError) as info:
        w.service.evaluate(ANA, pid, "disputas-suite")
    assert _code(info) is RegistryErrorCode.gate_failed
    got = w.service.get_proposal(pid).proposal
    assert got.state is ProposalState.draft and got.candidate_hash is None


def test_failed_infra_keeps_candidate_and_blocks_approval() -> None:  # T-REG-11
    w = World()
    w.evaluator.reports.append(_report("failed_infra"))
    pid = _frozen(w)
    assert w.service.evaluate(ANA, pid, "disputas-suite").verdict == "failed_infra"
    p = w.service.get_proposal(pid).proposal
    assert p.state is ProposalState.candidate
    with pytest.raises(RegistryError) as info:
        w.service.approve(ANA, pid, p.candidate_hash or "")
    assert _code(info) is RegistryErrorCode.illegal_transition


def test_retry_after_failed_infra_uses_latest_run() -> None:  # Review Focus 2
    w = World()
    w.evaluator.reports.extend([_report("failed_infra"), _report("pass")])
    pid = _frozen(w)
    w.service.evaluate(ANA, pid, "disputas-suite")
    w.service.evaluate(ANA, pid, "disputas-suite")
    h = w.service.get_proposal(pid).proposal.candidate_hash or ""
    assert w.service.approve(ANA, pid, h).decision == "approved"


def test_approve_with_other_hash_is_candidate_changed() -> None:  # T-REG-12
    w = World()
    pid, _ = _evaluated(w)
    with pytest.raises(RegistryError) as info:
        w.service.approve(ANA, pid, "0" * 64)
    assert _code(info) is RegistryErrorCode.candidate_changed


def test_reopen_invalidates_approval() -> None:  # T-REG-12
    w = World()
    pid, h = _evaluated(w)
    w.service.approve(ANA, pid, h)
    w.service.reopen(ANA, pid)
    with pytest.raises(RegistryError) as info:
        w.service.publish(ANA, pid, "k")
    assert _code(info) is RegistryErrorCode.illegal_transition


@pytest.mark.parametrize("who", [bot("constructor", "aprobador"), human("constructor")])
def test_non_human_or_non_approver_cannot_decide(who) -> None:  # type: ignore[no-untyped-def]  # T-REG-13
    w = World()
    pid, h = _evaluated(w)
    for call in (lambda: w.service.approve(who, pid, h), lambda: w.service.reject(who, pid, "no"),
                 lambda: w.service.publish(who, pid, "k"),
                 lambda: w.service.promote(who, AGENT, "prod", "rel-demo"),
                 lambda: w.service.revoke(who, "rel-demo", "x")):
        with pytest.raises(RegistryError) as info:
            call()
        assert _code(info) is RegistryErrorCode.forbidden_role


def test_reject_returns_to_draft_with_reason() -> None:
    w = World()
    pid, h = _evaluated(w)
    p = w.service.reject(ANA, pid, "el tono es muy seco")
    assert p.state is ProposalState.draft
    with w.store.transaction() as tx:
        a = tx.latest_approval(pid, h)
    assert a is not None and (a.decision, a.reason) == ("rejected", "el tono es muy seco")


def test_reject_after_approval_is_illegal() -> None:  # spec §4: reject solo desde evaluated
    w = World()
    pid, h = _evaluated(w)
    w.service.approve(ANA, pid, h)
    with pytest.raises(RegistryError) as info:
        w.service.reject(ANA, pid, "mejor no")
    assert _code(info) is RegistryErrorCode.illegal_transition
    assert w.service.get_proposal(pid).proposal.state is ProposalState.approved


def test_publish_with_moved_staging_is_stale_and_rebases() -> None:  # T-REG-15
    w = World()
    pid, h = _evaluated(w)
    w.service.approve(ANA, pid, h)
    with w.store.transaction() as tx:
        tx.set_alias(AliasChange(agent_id=AGENT, alias="staging", before="rel-demo", after="rel-otra",
                                 actor="x", reason="r", at=NOW))
    with pytest.raises(RegistryError) as info:
        w.service.publish(ANA, pid, "k")
    assert _code(info) is RegistryErrorCode.proposal_stale
    p = w.service.get_proposal(pid).proposal
    assert (p.state, p.base_release_id, p.candidate_hash) == (ProposalState.draft, "rel-otra", None)


def test_publish_failure_mid_way_leaves_nothing() -> None:  # T-REG-02 (memoria)
    w = World()
    pid, h = _evaluated(w)
    w.service.approve(ANA, pid, h)
    with w.store.transaction() as tx:
        events_before = len(tx.events())
    w.store.fail_on = lambda name: name == "set_alias"
    with pytest.raises(RuntimeError):
        w.service.publish(ANA, pid, "k")
    w.store.fail_on = None
    with w.store.transaction() as tx:
        assert tx.get_release("rel-" + h[:16]) is None
        assert all(tx.get_version(ref) is None for ref in NEW_REFS)
        assert tx.get_alias(AGENT, "staging") == "rel-demo"
        assert len(tx.events()) == events_before
        assert tx.get_publish_key("k") is None
    assert w.service.get_proposal(pid).proposal.state is ProposalState.approved
    assert w.service.publish(ANA, pid, "k").release_id == "rel-" + h[:16]
    with w.store.transaction() as tx:  # las mismas refs sí existen tras el reintento: la prueba no es vacía
        assert all(tx.get_version(ref) is not None for ref in NEW_REFS)
        assert tx.get_alias(AGENT, "staging") == "rel-" + h[:16]


def test_publish_retry_with_same_key_is_idempotent_after_success() -> None:  # Review Focus 3
    w = World()
    pid, h = _evaluated(w)
    w.service.approve(ANA, pid, h)
    first = w.service.publish(ANA, pid, "k")
    assert w.service.publish(ANA, pid, "k").release_id == first.release_id


def test_two_proposals_same_agent_second_publish_is_stale() -> None:  # T-REG-16
    w = World()
    a, ha = _evaluated(w)
    p = w.service.create_proposal(ANA, AGENT, Origin.manual, "otra")
    w.service.put_draft(ANA, p.proposal_id, [prompt_draft(version="1.2.0", text="Tercera variante."), SUITE],
                        expected_rev=0)
    w.service.freeze(ANA, p.proposal_id)
    w.service.evaluate(ANA, p.proposal_id, "disputas-suite")
    hb = w.service.get_proposal(p.proposal_id).proposal.candidate_hash or ""
    w.service.approve(ANA, a, ha)
    w.service.approve(ANA, p.proposal_id, hb)
    w.service.publish(ANA, a, "ka")
    with pytest.raises(RegistryError) as info:
        w.service.publish(ANA, p.proposal_id, "kb")
    assert _code(info) is RegistryErrorCode.proposal_stale


def test_promote_and_revoke() -> None:  # T-REG-20 (registro)
    w = World()
    pid, h = _evaluated(w)
    w.service.approve(ANA, pid, h)
    rel = w.service.publish(ANA, pid, "k").release_id
    with pytest.raises(RegistryError) as info:
        w.service.revoke(ANA, "rel-demo", "apunta prod")
    assert _code(info) is RegistryErrorCode.illegal_transition
    change = w.service.promote(ANA, AGENT, "prod", rel, "sale a prod")
    assert (change.before, change.after) == ("rel-demo", rel)
    assert w.service.revoke(ANA, "rel-demo", "reemplazada").status == "revoked"
    with pytest.raises(RegistryError) as info:
        w.service.promote(ANA, AGENT, "prod", "rel-demo")
    assert _code(info) is RegistryErrorCode.illegal_transition


def test_evaluate_falls_back_to_latest_published_suite() -> None:
    w = World()
    first, h = _evaluated(w)
    w.service.approve(ANA, first, h)
    w.service.publish(ANA, first, "k")
    p = w.service.create_proposal(ANA, AGENT, Origin.manual, "sin suite en el borrador")
    w.service.put_draft(ANA, p.proposal_id, [prompt_draft(version="1.2.0", text="Otra variante.")],
                        expected_rev=0)
    w.service.freeze(ANA, p.proposal_id)
    assert w.service.evaluate(ANA, p.proposal_id, "disputas-suite").verdict == "pass"
    last = w.service.get_proposal(p.proposal_id).last_eval
    assert last is not None and str(last.suite) == "eval_suite:disputas-suite@1.0.0"


def test_published_suite_of_other_agent_is_rejected() -> None:  # spec §5.2.6
    w = World()
    suite = EvalSuite.model_validate(suite_content(id="otra-suite", agent_id="otro"))
    with w.store.transaction() as tx:
        tx.blobs.put(encode_entity(suite))
        tx.insert_version(StoredVersion(ref=version_ref(suite), content_hash=content_hash(suite),
                                        docs=docs("suite ajena"), proposal_id=None, created_by="seed",
                                        created_at=NOW))
    pid = _frozen(w)
    with pytest.raises(RegistryError) as info:
        w.service.evaluate(ANA, pid, "otra-suite")
    assert _code(info) is RegistryErrorCode.validation_failed
    assert w.evaluator.calls == []


def _occupy_prompt_version(w: World) -> None:
    """Otra publicación ocupa p/resumen_radicado@1.1.0 distinto (REG-VERSION-TAKEN al rearmar)."""
    from agent_core.domain import Prompt
    other = Prompt.model_validate({**prompt_draft(text="Otro contenido.").content})
    with w.store.transaction() as tx:
        tx.blobs.put(encode_entity(other))
        tx.insert_version(StoredVersion(ref=version_ref(other), content_hash=content_hash(other), docs=docs(),
                                        proposal_id=None, created_by="x", created_at=NOW))


def _assert_candidate_changed(info: pytest.ExceptionInfo[RegistryError]) -> None:
    assert _code(info) is RegistryErrorCode.candidate_changed
    assert isinstance(info.value.payload, list)
    assert info.value.payload[0]["rule"] == "REG-VERSION-TAKEN"  # type: ignore[call-overload]


def test_publish_with_version_taken_is_candidate_changed_not_500() -> None:  # revisión final I1
    w = World()
    pid, h = _evaluated(w)
    w.service.approve(ANA, pid, h)
    _occupy_prompt_version(w)
    with pytest.raises(RegistryError) as info:
        w.service.publish(ANA, pid, "k")
    _assert_candidate_changed(info)
    assert w.service.get_proposal(pid).proposal.state is ProposalState.approved
    w.service.reopen(ANA, pid)  # no queda atascada


def test_evaluate_with_version_taken_is_candidate_changed_not_500() -> None:  # revisión final I1
    w = World()
    pid = _frozen(w)
    _occupy_prompt_version(w)
    with pytest.raises(RegistryError) as info:
        w.service.evaluate(ANA, pid, "disputas-suite")
    _assert_candidate_changed(info)


def test_promote_only_accepts_staging_and_prod() -> None:  # menor de la revisión final
    w = World()
    with pytest.raises(RegistryError) as info:
        w.service.promote(ANA, AGENT, "cualquier-cosa", "rel-demo")
    assert _code(info) is RegistryErrorCode.validation_failed
    assert w.service.promote(ANA, AGENT, "staging", "rel-demo").after == "rel-demo"


def test_promote_checks_the_release_belongs_to_the_agent() -> None:
    w = World()
    with pytest.raises(RegistryError) as info:
        w.service.promote(ANA, "otro-agente", "prod", "rel-demo")
    assert _code(info) is RegistryErrorCode.illegal_transition
    assert w.service.get_release("rel-demo").agent_id == AGENT  # y no movió nada


def test_release_without_status_row_is_integrity_error_not_active() -> None:
    from agent_core.registry.errors import IntegrityError
    w = World()
    del w.store._state.status["rel-demo"]
    with pytest.raises(IntegrityError):
        w.service.get_release("rel-demo")
