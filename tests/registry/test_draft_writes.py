import pytest

from agent_core.registry.errors import RegistryError, RegistryErrorCode
from agent_core.registry.models import AuditContext, Origin, ProposalState
from tests.registry.helpers import AGENT, bot, prompt_draft
from tests.registry.service_world import SUITE, World


def _events(w: World, type_: str) -> int:
    with w.store.transaction() as tx:
        return sum(1 for e in tx.events() if e.type == type_)


def test_create_proposal_with_the_same_key_returns_the_same_proposal() -> None:
    w = World()
    first = w.service.create_proposal(bot(), AGENT, Origin.builder_chat, "t", idempotency_key="k1")
    second = w.service.create_proposal(bot(), AGENT, Origin.builder_chat, "t", idempotency_key="k1")
    assert second.proposal_id == first.proposal_id
    assert _events(w, "proposal_created") == 1


def test_the_same_key_with_other_content_is_a_conflict() -> None:  # Review Focus 2
    w = World()
    w.service.create_proposal(bot(), AGENT, Origin.builder_chat, "t", idempotency_key="k1")
    with pytest.raises(RegistryError) as info:
        w.service.create_proposal(bot(), AGENT, Origin.builder_chat, "otro titulo", idempotency_key="k1")
    assert info.value.code is RegistryErrorCode.idempotency_conflict


def test_put_draft_replay_does_not_bump_rev_nor_go_stale() -> None:
    w = World()
    p = w.service.create_proposal(bot(), AGENT, Origin.builder_chat, "t")
    first = w.service.put_draft(bot(), p.proposal_id, [prompt_draft()], expected_rev=0, idempotency_key="k2")
    again = w.service.put_draft(bot(), p.proposal_id, [prompt_draft()], expected_rev=0, idempotency_key="k2")
    assert (first.rev, again.rev) == (1, 1)
    assert w.service.get_proposal(p.proposal_id).proposal.rev == 1


def test_put_draft_replay_after_the_proposal_moved_on_returns_the_stored_write() -> None:  # Review Focus 1
    w = World()
    p = w.service.create_proposal(bot(), AGENT, Origin.builder_chat, "t")
    w.service.put_draft(bot(), p.proposal_id, [prompt_draft(), SUITE], expected_rev=0, idempotency_key="k3")
    w.service.freeze(bot(), p.proposal_id)
    again = w.service.put_draft(bot(), p.proposal_id, [prompt_draft(), SUITE], expected_rev=0,
                                idempotency_key="k3")
    assert again.state is ProposalState.candidate  # no `illegal_transition` ni `proposal_stale`


def test_put_draft_same_key_other_changes_is_a_conflict() -> None:
    w = World()
    p = w.service.create_proposal(bot(), AGENT, Origin.builder_chat, "t")
    w.service.put_draft(bot(), p.proposal_id, [prompt_draft()], expected_rev=0, idempotency_key="k4")
    with pytest.raises(RegistryError) as info:
        w.service.put_draft(bot(), p.proposal_id, [prompt_draft(text="Otro texto.")], expected_rev=0,
                            idempotency_key="k4")
    assert info.value.code is RegistryErrorCode.idempotency_conflict


def test_put_draft_without_key_still_goes_stale() -> None:  # no cambia el comportamiento previo
    w = World()
    p = w.service.create_proposal(bot(), AGENT, Origin.builder_chat, "t")
    w.service.put_draft(bot(), p.proposal_id, [prompt_draft()], expected_rev=0)
    with pytest.raises(RegistryError) as info:
        w.service.put_draft(bot(), p.proposal_id, [prompt_draft()], expected_rev=0)
    assert info.value.code is RegistryErrorCode.proposal_stale


def test_get_write_reads_back_the_write_with_its_audit_context() -> None:
    w = World()
    audit = AuditContext(run_id="run-1", on_behalf_of="builder:ana")
    p = w.service.create_proposal(bot(), AGENT, Origin.builder_chat, "t")
    assert w.service.get_write("k5") is None
    w.service.put_draft(bot(), p.proposal_id, [prompt_draft()], expected_rev=0, idempotency_key="k5",
                        audit=audit)
    record = w.service.get_write("k5")
    assert record is not None
    assert (record.op, record.proposal_id, record.rev_after, record.verdict) == ("put_draft", p.proposal_id,
                                                                                 1, None)
    assert (record.run_id, record.on_behalf_of) == ("run-1", "builder:ana")
