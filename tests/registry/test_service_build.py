import pytest

from agent_core.registry.errors import RegistryError, RegistryErrorCode
from agent_core.registry.models import Origin, ProposalState
from tests.registry.helpers import AGENT, bot, human, prompt_draft
from tests.registry.service_world import SUITE, World


def _code(exc: pytest.ExceptionInfo[RegistryError]) -> RegistryErrorCode:
    return exc.value.code


def test_create_takes_base_from_staging() -> None:
    w = World()
    p = w.service.create_proposal(bot(), AGENT, Origin.builder_chat, "mejorar radicado")
    assert (p.state, p.base_release_id, p.rev, p.created_by) == (ProposalState.draft, "rel-demo", 0,
                                                                  "constructor-bot")


def test_put_draft_with_stale_rev_fails() -> None:  # T-REG-03
    w = World()
    p = w.service.create_proposal(human(), AGENT, Origin.manual, "t")
    w.service.put_draft(human(), p.proposal_id, [prompt_draft()], expected_rev=0)
    with pytest.raises(RegistryError) as info:
        w.service.put_draft(human(), p.proposal_id, [prompt_draft()], expected_rev=0)
    assert _code(info) is RegistryErrorCode.proposal_stale


def test_freeze_with_violation_keeps_draft() -> None:  # T-REG-04
    w = World()
    p = w.service.create_proposal(human(), AGENT, Origin.manual, "t")
    w.service.put_draft(human(), p.proposal_id, [prompt_draft(version="0.1.0")], expected_rev=0)
    with pytest.raises(RegistryError) as info:
        w.service.freeze(human(), p.proposal_id)
    assert _code(info) is RegistryErrorCode.validation_failed
    assert isinstance(info.value.payload, list) and info.value.payload[0]["rule"] == "REG-VERSION"
    assert w.service.get_proposal(p.proposal_id).proposal.state is ProposalState.draft


def test_freeze_moves_to_candidate_with_hash_and_auto_bumps() -> None:
    w = World()
    p = w.service.create_proposal(human(), AGENT, Origin.manual, "t")
    w.service.put_draft(human(), p.proposal_id, [prompt_draft(), SUITE], expected_rev=0)
    view = w.service.freeze(human(), p.proposal_id)
    got = w.service.get_proposal(p.proposal_id).proposal
    assert got.state is ProposalState.candidate and got.candidate_hash == view.candidate_hash
    assert view.release_id_preview == "rel-" + view.candidate_hash[:16]
    assert {str(r) for r in view.auto_bumped} == {"flow:disputa-cargo@1.0.1", "agent:atencion@1.0.1"}


def test_validate_is_read_only() -> None:
    w = World()
    p = w.service.create_proposal(human(), AGENT, Origin.manual, "t")
    w.service.put_draft(human(), p.proposal_id, [prompt_draft()], expected_rev=0)
    report = w.service.validate(human(), p.proposal_id)
    assert report.violations == [] and report.candidate_hash is not None
    assert w.service.get_proposal(p.proposal_id).proposal.state is ProposalState.draft


def test_illegal_transitions() -> None:  # T-REG-07
    w = World()
    p = w.service.create_proposal(human(), AGENT, Origin.manual, "t")
    with pytest.raises(RegistryError) as info:
        w.service.reopen(human(), p.proposal_id)
    assert _code(info) is RegistryErrorCode.illegal_transition
    w.service.put_draft(human(), p.proposal_id, [prompt_draft()], expected_rev=0)
    w.service.freeze(human(), p.proposal_id)
    with pytest.raises(RegistryError) as info:
        w.service.put_draft(human(), p.proposal_id, [prompt_draft()], expected_rev=1)
    assert _code(info) is RegistryErrorCode.illegal_transition


def test_reopen_clears_candidate() -> None:
    w = World()
    p = w.service.create_proposal(human(), AGENT, Origin.manual, "t")
    w.service.put_draft(human(), p.proposal_id, [prompt_draft()], expected_rev=0)
    w.service.freeze(human(), p.proposal_id)
    again = w.service.reopen(human(), p.proposal_id)
    assert again.state is ProposalState.draft and again.candidate_hash is None


def test_without_constructor_role_is_forbidden() -> None:
    w = World()
    with pytest.raises(RegistryError) as info:
        w.service.create_proposal(human("aprobador"), AGENT, Origin.manual, "t")
    assert _code(info) is RegistryErrorCode.forbidden_role


def test_unknown_proposal_is_not_found() -> None:
    with pytest.raises(RegistryError) as info:
        World().service.freeze(human(), "prop-x")
    assert _code(info) is RegistryErrorCode.not_found


def test_events_are_recorded() -> None:
    w = World()
    p = w.service.create_proposal(human(), AGENT, Origin.manual, "t")
    w.service.put_draft(human(), p.proposal_id, [prompt_draft()], expected_rev=0)
    w.service.freeze(human(), p.proposal_id)
    with w.store.transaction() as tx:
        types = [e.type for e in tx.events()]
    assert types == ["proposal_created", "draft_updated", "frozen"]
