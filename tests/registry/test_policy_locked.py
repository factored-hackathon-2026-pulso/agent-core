"""`Policy.locked` (REG-LOCKED for policies) and `EvalReport.guardrail_changes`."""

from typing import Any

import pytest

from agent_core.domain import Policy
from agent_core.registry.entities import content_hash
from agent_core.registry.errors import RegistryError, RegistryErrorCode
from agent_core.registry.models import EntityDraft, Origin
from tests.registry.helpers import AGENT, admin, bot, docs, human, prompt_draft
from tests.registry.service_world import ANA, SUITE, World, publish_cycle

ADMIN = admin()
POLICY_ID = "escalamiento-disputa-monto"


def policy_draft(version: str, *, limit: int = 500, **over: Any) -> EntityDraft:
    content: dict[str, Any] = {"id": POLICY_ID, "version": version, "owner": "riesgo",
                               "expr": {">": [{"var": "facts.monto_usd.value"}, limit]},
                               "rationale": "Disputas sobre montos altos requieren revisión humana.", **over}
    return EntityDraft(kind="policy", content=content, docs=docs("politica"))


def _violations(w: World, *drafts: EntityDraft, actor: Any = None) -> list[str]:
    p = w.service.create_proposal(human(), AGENT, Origin.manual, "t")
    w.service.put_draft(actor or ADMIN, p.proposal_id, list(drafts), expected_rev=0)
    with pytest.raises(RegistryError) as info:
        w.service.freeze(human(), p.proposal_id)
    assert info.value.code is RegistryErrorCode.validation_failed
    assert isinstance(info.value.payload, list)
    return [v["rule"] for v in info.value.payload]


def _locked_world() -> World:
    w = World()
    publish_cycle(w, [prompt_draft(), SUITE, policy_draft("1.1.0", locked=True)], actor=ADMIN)
    return w


def test_locked_defaults_to_false_and_is_omitted_so_published_hashes_do_not_change() -> None:
    plain = Policy.model_validate(policy_draft("1.0.0").content)
    assert plain.locked is False
    assert "locked" not in plain.model_dump(mode="json")
    assert Policy.model_validate(policy_draft("1.0.0", locked=True).content).model_dump(mode="json")["locked"]
    assert content_hash(plain) == content_hash(Policy.model_validate(policy_draft("1.0.0").content))


def test_only_an_admin_can_set_a_policy_locked() -> None:
    w = World()
    p = w.service.create_proposal(human(), AGENT, Origin.manual, "t")
    for who in (bot(), human()):
        with pytest.raises(RegistryError) as denied:
            w.service.put_draft(who, p.proposal_id, [policy_draft("1.1.0", locked=True)], expected_rev=0)
        assert denied.value.code is RegistryErrorCode.forbidden_role
    w.service.put_draft(ADMIN, p.proposal_id, [policy_draft("1.1.0", locked=True)], expected_rev=0)
    # an unlocked policy draft stays open to a constructor
    q = w.service.create_proposal(human(), AGENT, Origin.manual, "t2")
    w.service.put_draft(bot(), q.proposal_id, [policy_draft("1.1.0", limit=400)], expected_rev=0)


def test_a_locked_policy_cannot_be_changed_unlocked_even_by_admin() -> None:
    assert _violations(_locked_world(), policy_draft("1.2.0", limit=900, locked=True)) == ["REG-LOCKED"]
    assert _violations(_locked_world(), policy_draft("1.2.0", limit=500)) == ["REG-LOCKED"]


def test_a_locked_policy_that_leaves_the_release_closure_is_a_violation() -> None:
    from agent_core.registry.candidate import locked_policy_violations
    locked = Policy.model_validate(policy_draft("1.1.0", locked=True).content)
    assert [v.rule for v in locked_policy_violations([locked], [])] == ["REG-LOCKED"]
    assert locked_policy_violations([locked], [locked]) == []
    assert locked_policy_violations([Policy.model_validate(policy_draft("1.0.0").content)], []) == []


def test_a_change_that_does_not_touch_the_locked_policy_is_fine() -> None:
    w = _locked_world()
    p = w.service.create_proposal(human(), AGENT, Origin.manual, "t")
    w.service.put_draft(human(), p.proposal_id, [prompt_draft(version="1.2.0", text="Otra variante.")],
                        expected_rev=0)
    assert w.service.freeze(ANA, p.proposal_id).candidate_hash


def _evaluated_report(w: World, *drafts: EntityDraft) -> Any:
    p = w.service.create_proposal(human(), AGENT, Origin.manual, "t")
    w.service.put_draft(ADMIN, p.proposal_id, [prompt_draft(), SUITE, *drafts], expected_rev=0)
    w.service.freeze(ANA, p.proposal_id)
    return w.service.evaluate(ANA, p.proposal_id, "disputas-suite")


def test_the_report_lists_the_guardrail_artifacts_the_gate_does_not_evaluate() -> None:
    w = World()
    report = _evaluated_report(w, policy_draft("1.1.0", limit=900))
    found = {(c.kind, c.id, c.change) for c in report.guardrail_changes}
    assert ("policy", POLICY_ID, "changed") in found
    assert ("flow", "disputa-cargo", "changed") not in found  # re-pinned by the cascade, content unchanged
    changed = next(c for c in report.guardrail_changes if c.kind == "policy")
    assert (changed.base_version, changed.candidate_version) == ("1.0.0", "1.1.0")


def test_a_text_only_change_has_no_guardrail_changes() -> None:
    assert _evaluated_report(World()).guardrail_changes == []
