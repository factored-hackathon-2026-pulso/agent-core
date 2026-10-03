"""N-07 (§31.12): cambios a nivel release (interrupciones, idioma, ruleset, `max_input_chars`) por propuesta,
con un borrador reservado `release_settings`."""

from typing import Any

import pytest

from agent_core.registry.errors import RegistryError, RegistryErrorCode
from agent_core.registry.models import EntityDraft, Origin
from tests.registry.helpers import AGENT, admin, docs, human, prompt_draft
from tests.registry.service_world import ANA, SUITE, World, publish_cycle

ADMIN = admin()


def settings(**content: Any) -> EntityDraft:
    return EntityDraft(kind="release_settings", content=content, docs=docs("ajuste de release"))


def _freeze(w: World, *drafts: EntityDraft) -> str:
    p = w.service.create_proposal(human(), AGENT, Origin.manual, "t")
    w.service.put_draft(human(), p.proposal_id, [prompt_draft(), *drafts], expected_rev=0)
    return p.proposal_id


def _violations(w: World, pid: str) -> list[str]:
    with pytest.raises(RegistryError) as info:
        w.service.freeze(human(), pid)
    assert info.value.code is RegistryErrorCode.validation_failed
    assert isinstance(info.value.payload, list)
    return [v["rule"] for v in info.value.payload]


def test_max_input_chars_changes_the_published_release_and_nothing_else() -> None:
    w = World()
    before = w.service.get_release("rel-demo")
    rid = publish_cycle(w, [prompt_draft(), SUITE, settings(max_input_chars=8000)])
    after = w.service.get_release(rid)
    assert (before.max_input_chars, after.max_input_chars) == (4000, 8000)
    assert after.interrupts == before.interrupts
    assert after.language_detection == before.language_detection
    assert after.injection_ruleset == before.injection_ruleset


def test_the_settings_are_part_of_the_candidate_hash() -> None:
    w = World()
    plain = w.service.freeze(ANA, _freeze(w)).candidate_hash
    other = World()
    changed = other.service.freeze(ANA, _freeze(other, settings(max_input_chars=8000))).candidate_hash
    assert plain != changed


def test_interrupts_are_replaced_as_a_whole() -> None:
    w = World()
    new = [{"id": "fraude", "priority": 120, "locked": True,
            "action": {"type": "escalate", "target_queue": "fraude", "priority": "critical"}},
           {"id": "queja", "priority": 10,
            "action": {"type": "escalate", "target_queue": "quejas", "priority": "normal"}}]
    rid = publish_cycle(w, [prompt_draft(), SUITE, settings(interrupts=new)], actor=ADMIN)
    got = {i.id: i.priority for i in w.service.get_release(rid).interrupts}
    assert got == {"fraude": 120, "queja": 10}
    cleared = World()  # la `fraude` de la base es de plataforma: no se vacía
    assert _violations(cleared, _freeze_as_admin(cleared, [])) == ["REG-LOCKED"]


def test_an_omitted_field_keeps_the_base_value() -> None:
    w = World()
    rid = publish_cycle(w, [prompt_draft(), SUITE, settings()])
    detail = w.service.get_release(rid)
    assert detail.max_input_chars == 4000 and len(detail.interrupts) == 1


def test_unknown_fields_and_bad_values_are_schema_violations() -> None:
    w = World()
    assert _violations(w, _freeze(w, settings(nope=1))) == ["REG-SCHEMA"]
    w = World()
    assert _violations(w, _freeze(w, settings(max_input_chars=0))) == ["REG-SCHEMA"]


def test_a_second_settings_draft_is_a_duplicate() -> None:
    w = World()
    twice = _freeze(w, settings(max_input_chars=1), settings(max_input_chars=2))
    assert "REG-DUPLICATE" in _violations(w, twice)


def test_a_language_detection_that_does_not_exist_is_a_pin_violation() -> None:
    w = World()
    assert "REG-PIN" in _violations(w, _freeze(w, settings(language_detection="no-existe")))


def test_the_settings_travel_in_the_functional_changes_for_the_approver() -> None:
    w = World()
    pid = _freeze(w, SUITE, settings(max_input_chars=8000))
    w.service.freeze(ANA, pid)
    w.service.evaluate(ANA, pid, "disputas-suite")
    review = w.service.get_proposal(pid).review
    assert review is not None and "release_settings" in {d.kind for d in review.functional_changes}


def test_changing_the_interrupts_needs_the_admin_role() -> None:
    w = World()
    p = w.service.create_proposal(human(), AGENT, Origin.manual, "t")
    w.service.put_draft(human(), p.proposal_id, [prompt_draft(), settings(max_input_chars=8000)],
                        expected_rev=0)
    with pytest.raises(RegistryError) as info:
        w.service.put_draft(human(), p.proposal_id, [prompt_draft(), settings(interrupts=[])], expected_rev=1)
    assert info.value.code is RegistryErrorCode.forbidden_role
    w.service.put_draft(admin(), p.proposal_id, [prompt_draft(), settings(interrupts=[])], expected_rev=1)


def test_max_input_chars_has_a_ceiling() -> None:
    w = World()
    assert _violations(w, _freeze(w, settings(max_input_chars=2_000_000_000))) == ["REG-SCHEMA"]


def test_the_approver_sees_the_settings_diff_against_the_base() -> None:
    w = World()
    pid = _freeze(w, SUITE, settings(max_input_chars=8000))
    w.service.freeze(ANA, pid)
    w.service.evaluate(ANA, pid, "disputas-suite")
    review = w.service.get_proposal(pid).review
    assert review is not None
    assert [(c.field, c.before, c.after) for c in review.release_changes] == [("max_input_chars", 4000, 8000)]


# --- interrupciones de plataforma (locked) ---------------------------------------------------------------

FRAUDE = {"id": "fraude", "priority": 100, "locked": True,
          "action": {"type": "escalate", "target_queue": "fraude", "priority": "critical"}}


def _world_with_a_locked_fraud_interrupt() -> World:
    w = World()
    publish_cycle(w, [prompt_draft(), SUITE, settings(interrupts=[FRAUDE])], actor=ADMIN)
    return w


def test_a_locked_interrupt_cannot_be_removed_lowered_changed_or_unlocked() -> None:
    attempts = {
        "removed": [],
        "lowered": [{**FRAUDE, "priority": 50}],
        "other_queue": [{**FRAUDE, "action": {**FRAUDE["action"], "target_queue": "otra"}}],
        "unlocked": [{**FRAUDE, "locked": False}],
    }
    for name, interrupts in attempts.items():
        w = _world_with_a_locked_fraud_interrupt()
        assert _violations(w, _freeze_as_admin(w, interrupts)) == ["REG-LOCKED"], name


def _freeze_as_admin(w: World, interrupts: list[dict[str, Any]]) -> str:
    p = w.service.create_proposal(human(), AGENT, Origin.manual, "t")
    draft = [settings(interrupts=interrupts)]
    w.service.put_draft(ADMIN, p.proposal_id, draft, expected_rev=0)
    return p.proposal_id


def test_a_locked_interrupt_can_be_raised_and_others_added() -> None:
    w = _world_with_a_locked_fraud_interrupt()
    other = {"id": "queja", "priority": 10,
             "action": {"type": "escalate", "target_queue": "quejas", "priority": "normal"}}
    pid = _freeze_as_admin(w, [{**FRAUDE, "priority": 120}, other])
    assert w.service.freeze(ANA, pid).candidate_hash
