from datetime import timedelta

import pytest

from agent_core.registry.errors import RegistryError, RegistryErrorCode
from agent_core.registry.models import Origin
from agent_core.registry.service import RegistryService
from testing.builders import principal
from tests.registry.helpers import AGENT, bot, human, prompt_draft
from tests.registry.service_world import SUITE, World

ANA = human()


class Runs:
    def __init__(self, mapping: dict[str, str]) -> None:
        self.mapping = mapping

    def release_of(self, run_id: str) -> str | None:
        return self.mapping.get(run_id)


def _published(w: World) -> str:
    p = w.service.create_proposal(ANA, AGENT, Origin.manual, "t")
    w.service.put_draft(ANA, p.proposal_id, [prompt_draft(), SUITE], expected_rev=0)
    w.service.freeze(ANA, p.proposal_id)
    w.service.evaluate(ANA, p.proposal_id, "disputas-suite")
    h = w.service.get_proposal(p.proposal_id).proposal.candidate_hash or ""
    w.service.approve(ANA, p.proposal_id, h)
    return w.service.publish(ANA, p.proposal_id, "k").release_id


def test_diff_matches_release_entities() -> None:  # T-REG-22
    w = World()
    rel = _published(w)
    diff = w.service.diff_releases("rel-demo", rel)
    assert {(c.before.id, c.before.version, c.after.version) for c in diff.changed} == {
        ("p/resumen_radicado", "1.0.0", "1.1.0"),
        ("disputa-cargo", "1.0.0", "1.0.1"),
        (AGENT, "1.0.0", "1.0.1"),
    }
    assert diff.added == [] and diff.removed == []
    assert next(c for c in diff.changed if c.after.id == "p/resumen_radicado").docs.rationale


def test_lineage_matches_release_exactly() -> None:  # T-REG-21
    w = World()
    rel = _published(w)
    service = RegistryService(w.store, w.evaluator, w.clock, w.ids, runs=Runs({"run-9": rel}))
    lineage = service.lineage_for_run(ANA, "run-9")
    detail = service.get_release(rel)
    assert [e.ref for e in lineage.entities] == [e.ref for e in detail.entities]
    assert lineage.approved_by == "ana" and lineage.built_by == "ana" and lineage.eval_verdict == "pass"
    assert any(e.changed_vs_base and e.ref.id == "p/resumen_radicado" for e in lineage.entities)


def test_lineage_unknown_run_is_not_found() -> None:
    w = World()
    service = RegistryService(w.store, w.evaluator, w.clock, w.ids, runs=Runs({}))
    with pytest.raises(RegistryError) as info:
        service.lineage_for_run(ANA, "run-x")
    assert info.value.code is RegistryErrorCode.not_found


def test_entity_and_versions() -> None:
    w = World()
    _published(w)
    latest = w.service.get_entity("prompt", "p/resumen_radicado")
    assert latest.ref.version == "1.1.0" and latest.content["id"] == "p/resumen_radicado"
    versions = w.service.list_versions("prompt", "p/resumen_radicado")
    assert [v.ref.version for v in versions] == ["1.0.0", "1.1.0"]


def test_lineage_requires_the_constructor_role() -> None:
    w = World()
    rel = _published(w)
    service = RegistryService(w.store, w.evaluator, w.clock, w.ids, runs=Runs({"run-9": rel}))
    customer = principal(type="customer", id="cliente-1", roles=[], attrs={})
    approver_only = human("aprobador", pid="beto")
    for who in (customer, approver_only):
        with pytest.raises(RegistryError) as info:
            service.lineage_for_run(who, "run-9")
        assert info.value.code is RegistryErrorCode.forbidden_role
    assert service.lineage_for_run(bot(), "run-9").run_id == "run-9"


def _three_proposals(w: World) -> list[str]:
    ids = []
    for title in ("uno", "dos", "tres"):
        ids.append(w.service.create_proposal(ANA, AGENT, Origin.manual, title).proposal_id)
        w.clock.advance(timedelta(minutes=1))
    return ids


def test_list_proposals_is_newest_first_with_a_total_and_pages() -> None:
    w = World()
    one, two, three = _three_proposals(w)
    page = w.service.list_proposals(ANA)
    assert [p.proposal_id for p in page.items] == [three, two, one] and page.total == 3
    first = w.service.list_proposals(ANA, limit=2)
    rest = w.service.list_proposals(ANA, limit=2, offset=2)
    assert [p.proposal_id for p in first.items + rest.items] == [three, two, one] and first.total == 3


def test_list_proposals_filters_by_agent_state_and_creator() -> None:
    w = World()
    one, *_ = _three_proposals(w)
    w.service.put_draft(ANA, one, [prompt_draft(), SUITE], expected_rev=0)
    w.service.freeze(ANA, one)
    assert [p.proposal_id for p in w.service.list_proposals(ANA, state="candidate").items] == [one]
    assert w.service.list_proposals(ANA, state="draft").total == 2
    assert w.service.list_proposals(ANA, state="published").total == 0
    assert w.service.list_proposals(ANA, agent_id="otro-agente").total == 0
    assert w.service.list_proposals(ANA, created_by="nadie").total == 0
    assert w.service.list_proposals(ANA, created_by="ana").total == 3


def test_list_proposals_bounds_the_page_and_needs_a_builder() -> None:
    w = World()
    _three_proposals(w)
    assert len(w.service.list_proposals(ANA, limit=10_000).items) == 3  # tope 200, no error
    with pytest.raises(RegistryError) as info:
        w.service.list_proposals(ANA, limit=0)
    assert info.value.code is RegistryErrorCode.validation_failed
    with pytest.raises(RegistryError) as denied:
        w.service.list_proposals(principal(type='customer', id='cust-1'))
    assert denied.value.code is RegistryErrorCode.forbidden_role
