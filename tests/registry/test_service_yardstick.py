"""Double yardstick in the registry service (ADR 0020): the base's yardstick and the recorded suite."""

import json
import shutil
from pathlib import Path

import pytest

from agent_core.registry.errors import RegistryError, RegistryErrorCode
from agent_core.registry.memory import InMemoryRegistryStore
from agent_core.registry.models import EntityDraft, Origin, ProposalState, VersionRef
from agent_core.registry.service import RegistryService
from testing.fakes.clock import FakeClock
from testing.fakes.ids import FakeIds
from tests.registry.eval_support import metric
from tests.registry.helpers import (
    AGENT,
    REGISTRY_DEMO,
    admin,
    bot,
    docs,
    prompt_draft,
    suite_content,
    suite_draft,
)
from tests.registry.service_world import (
    ANA,
    FakeEvaluator,
    World,
    agent_with_metrics,
    loosening_evaluated,
    publish_cycle,
)

SUITE_REF = VersionRef(kind="eval_suite", id="disputas-suite", version="1.0.0")


def test_publish_records_the_suite_used_by_the_gate() -> None:
    w = World()
    release_id = publish_cycle(w, [prompt_draft(), suite_draft()])
    assert w.service.get_release(release_id).eval_suite_refs == [SUITE_REF]
    assert w.service.get_release("rel-demo").eval_suite_refs == []


def test_a_base_without_recorded_suite_gives_a_metrics_only_old_yardstick() -> None:  # decision D3
    w = World()
    publish_cycle(w, [prompt_draft(), suite_draft()])
    first = w.evaluator.requests[0]
    assert first.base is not None and first.base.release.id == "rel-demo"
    assert first.old is not None and first.old.suite is None and first.old.metrics == []
    assert first.new.suite is not None and first.new.suite.version == "1.0.0"


def test_the_next_proposal_is_measured_with_the_suite_of_its_base() -> None:
    w = World()
    publish_cycle(w, [prompt_draft(), suite_draft()])
    publish_cycle(w, [prompt_draft(version="1.2.0", text="Otra variante."), suite_draft("1.1.0")], key="k2")
    second = w.evaluator.requests[-1]
    assert second.old is not None and second.old.suite is not None and second.new.suite is not None
    assert (second.old.suite.version, second.new.suite.version) == ("1.0.0", "1.1.0")


def test_import_records_the_seed_suite(tmp_path: Path) -> None:
    root = tmp_path / "seed"
    shutil.copytree(REGISTRY_DEMO, root)
    (root / "eval_suites").mkdir()
    (root / "eval_suites" / "disputas-suite@1.0.0.yaml").write_text(json.dumps(suite_content()),
                                                                   encoding="utf-8")  # JSON is valid YAML
    service = RegistryService(InMemoryRegistryStore(), FakeEvaluator(), FakeClock(), FakeIds())
    [detail] = service.import_seed(admin(), root)
    assert detail.eval_suite_refs == [SUITE_REF]


def test_validate_reports_problems_of_a_drafted_suite() -> None:  # T-EVAL-11
    w = World()
    p = w.service.create_proposal(ANA, AGENT, Origin.manual, "métrica sin umbral")
    w.service.put_draft(ANA, p.proposal_id, [agent_with_metrics(w, "1.1.0", metric("m_gate")), suite_draft()],
                        expected_rev=0)
    report = w.service.validate(ANA, p.proposal_id)
    found = {(v.rule, v.message.split(":")[0]) for v in report.violations}
    assert ("REG-SUITE", "missing_threshold") in found


def test_evaluate_rejects_a_published_suite_with_problems() -> None:  # T-EVAL-11
    w = World()
    publish_cycle(w, [prompt_draft(), suite_draft()])
    p = w.service.create_proposal(ANA, AGENT, Origin.manual, "métrica nueva sin umbral")
    drafts = [agent_with_metrics(w, "1.1.0", metric("m_gate"))]
    w.service.put_draft(ANA, p.proposal_id, drafts, expected_rev=0)
    w.service.freeze(ANA, p.proposal_id)
    calls = len(w.evaluator.requests)
    with pytest.raises(RegistryError) as info:
        w.service.evaluate(ANA, p.proposal_id, "disputas-suite")
    assert info.value.code is RegistryErrorCode.validation_failed
    assert info.value.payload[0]["message"].startswith("missing_threshold")  # type: ignore[index,call-overload]
    assert len(w.evaluator.requests) == calls  # nothing was evaluated


def test_the_report_carries_the_loosening() -> None:  # T-EVAL-08 in the service
    w = World()
    pid, _ = loosening_evaluated(w)
    last = w.service.get_proposal(pid).last_eval
    assert last is not None and [c.kind for c in last.report.yardstick_changes] == ["repetitions_lowered"]


def test_tightening_carries_no_loosening() -> None:
    w = World()
    publish_cycle(w, [prompt_draft(), suite_draft()])
    p = w.service.create_proposal(ANA, AGENT, Origin.manual, "tighten")
    w.service.put_draft(ANA, p.proposal_id, [prompt_draft(version="1.2.0", text="Otra variante."),
                                              suite_draft("1.1.0", repetitions=3)], expected_rev=0)
    w.service.freeze(ANA, p.proposal_id)
    assert w.service.evaluate(ANA, p.proposal_id, "disputas-suite").yardstick_changes == []


def test_loosening_needs_its_own_approval() -> None:  # T-EVAL-17, decision D7
    w = World()
    pid, h = loosening_evaluated(w)
    with pytest.raises(RegistryError) as info:
        w.service.approve(ANA, pid, h)
    assert info.value.code is RegistryErrorCode.loosening_not_accepted
    assert [c["kind"] for c in info.value.payload] == ["repetitions_lowered"]  # type: ignore[union-attr,index]
    assert w.service.get_proposal(pid).proposal.state is ProposalState.evaluated
    approval = w.service.approve(ANA, pid, h, accept_yardstick_loosened=True)
    assert [c.kind for c in approval.yardstick_loosened] == ["repetitions_lowered"]


def test_review_shows_change_suite_and_loosening_apart() -> None:  # T-EVAL-17
    w = World()
    pid, _ = loosening_evaluated(w)
    review = w.service.get_proposal(pid).review
    assert review is not None
    assert [d.kind for d in review.functional_changes] == ["prompt"]
    assert [d.version for d in review.suite_changes] == ["1.1.0"]
    assert str(review.suite) == "eval_suite:disputas-suite@1.1.0"
    assert [c.kind for c in review.yardstick_loosened] == ["repetitions_lowered"]
    assert review.gate == []  # the fake evaluator does not measure; ScenarioEvaluator brings the items


@pytest.mark.parametrize("draft", [
    EntityDraft(kind="agent", docs=docs(),
                content={"id": AGENT, "version": "1.1.0", "metrics": [{"id": "platform_pii_leak"}]}),
    suite_draft("1.1.0", thresholds={"platform_pii_leak": {"noise_margin": "1"}}),
])
def test_a_proposal_cannot_edit_platform_guardrails(draft: EntityDraft) -> None:  # T-EVAL-10
    w = World()
    p = w.service.create_proposal(ANA, AGENT, Origin.manual, "tries to touch the platform")
    with pytest.raises(RegistryError) as info:
        w.service.put_draft(ANA, p.proposal_id, [draft], expected_rev=0)
    assert info.value.code is RegistryErrorCode.forbidden_role
    assert w.service.get_proposal(p.proposal_id).changes == []


def test_published_loosening_only_affects_later_proposals() -> None:  # T-EVAL-09
    w = World()
    pid, h = loosening_evaluated(w)
    own = w.evaluator.requests[-1].old
    assert own is not None and own.suite is not None and own.suite.version == "1.0.0"  # old yardstick
    w.service.approve(ANA, pid, h, accept_yardstick_loosened=True)
    loosened = w.service.publish(ANA, pid, "k-loosen").release_id
    assert [str(r) for r in w.service.get_release(loosened).eval_suite_refs] == [
        "eval_suite:disputas-suite@1.1.0"]
    later = w.service.create_proposal(ANA, AGENT, Origin.manual, "later")
    w.service.put_draft(ANA, later.proposal_id, [prompt_draft(version="1.3.0", text="Una más.")],
                        expected_rev=0)
    w.service.freeze(ANA, later.proposal_id)
    report = w.service.evaluate(ANA, later.proposal_id, "disputas-suite")
    old = w.evaluator.requests[-1].old
    assert old is not None and old.suite is not None and old.suite.version == "1.1.0"
    assert report.yardstick_changes == []


def test_builder_cannot_decide_even_with_its_own_suite_and_metrics() -> None:  # T-EVAL-16
    w = World()
    builder = bot()
    p = w.service.create_proposal(builder, AGENT, Origin.builder_chat, "builder proposal")
    drafts = [agent_with_metrics(w, "1.1.0", metric("m_gate")),
              suite_draft(thresholds={"m_gate": {"noise_margin": "0", "floor": "0"}})]
    w.service.put_draft(builder, p.proposal_id, drafts, expected_rev=0)
    view = w.service.freeze(builder, p.proposal_id)
    assert w.service.evaluate(builder, p.proposal_id, "disputas-suite").verdict == "pass"
    calls = (
        lambda: w.service.approve(builder, p.proposal_id, view.candidate_hash,
                                  accept_yardstick_loosened=True),
        lambda: w.service.publish(builder, p.proposal_id, "k"),
        lambda: w.service.promote(builder, AGENT, "prod", "rel-demo"),
        lambda: w.service.revoke(builder, "rel-demo", "x"),
    )
    before = _observable_state(w, p.proposal_id, view.candidate_hash)
    assert before["state"] is ProposalState.evaluated
    assert before["approval"] is None and before["events"]
    for call in calls:
        with pytest.raises(RegistryError) as info:
            call()
        assert info.value.code is RegistryErrorCode.forbidden_role
        assert _observable_state(w, p.proposal_id, view.candidate_hash) == before  # nothing was written


def _observable_state(w: World, proposal_id: str, candidate_hash: str) -> dict[str, object]:
    """Everything a refused decision must leave untouched: proposal, approval, aliases, release, events."""
    with w.store.transaction() as tx:
        return {
            "state": w.service.get_proposal(proposal_id).proposal.state,
            "approval": tx.latest_approval(proposal_id, candidate_hash),
            "prod": tx.get_alias(AGENT, "prod"),
            "staging": tx.get_alias(AGENT, "staging"),
            "release_status": tx.release_status("rel-demo"),
            "events": len(tx.events()),
            "publish_key": tx.get_publish_key("k"),
        }
