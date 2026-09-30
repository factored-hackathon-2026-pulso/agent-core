import pytest

from agent_core.domain import EntityKind, Interrupt, StartFlowAction
from agent_core.registry.candidate import CandidateError, build_candidate, parse_semver
from agent_core.registry.models import EntityDraft, VersionRef
from tests.registry.helpers import AGENT, demo_pinned, docs, prompt_draft, suite_draft


def _base():  # type: ignore[no-untyped-def]
    pinned = demo_pinned()
    return pinned.release, pinned.entities


def _none(_: VersionRef) -> None:
    return None


def test_changing_a_prompt_bumps_flow_and_agent_in_cascade() -> None:
    base, entities = _base()
    cand = build_candidate(agent_id=AGENT, base=base, base_entities=entities, drafts=[prompt_draft()],
                           published_hash=_none)
    assert cand.release.entities[EntityKind.prompt]["p/resumen_radicado"] == "1.1.0"
    assert cand.release.entities[EntityKind.flow]["disputa-cargo"] == "1.0.1"
    assert cand.release.entities[EntityKind.agent][AGENT] == "1.0.1"
    assert {str(r) for r in cand.auto_bumped} == {"flow:disputa-cargo@1.0.1", "agent:atencion@1.0.1"}
    assert cand.agent_version == "1.0.1"
    for ref in cand.auto_bumped:
        assert "p/resumen_radicado@1.1.0" in cand.docs[ref].changelog or ref.kind == "agent"


def test_cascade_bumps_every_dependent_once() -> None:
    base, entities = _base()
    tpl = next(e for e in entities if getattr(e, "id", "") == "t/pqr_radicado")
    draft = EntityDraft(kind="template", content={**tpl.model_dump(mode="json"), "version": "1.0.1"},
                        docs=docs())
    cand = build_candidate(agent_id=AGENT, base=base, base_entities=entities,
                           drafts=[draft, prompt_draft()], published_hash=_none)
    bumped = [str(r) for r in cand.auto_bumped]
    assert bumped.count("flow:disputa-cargo@1.0.1") == 1
    assert len(bumped) == len(set(bumped))


def test_auto_bump_skips_versions_already_published() -> None:
    base, entities = _base()
    taken = {"flow:disputa-cargo@1.0.1": "otro-hash"}
    cand = build_candidate(agent_id=AGENT, base=base, base_entities=entities, drafts=[prompt_draft()],
                           published_hash=lambda r: taken.get(str(r)))
    assert cand.release.entities[EntityKind.flow]["disputa-cargo"] == "1.0.2"


def test_hash_is_deterministic_and_changes_with_content() -> None:
    base, entities = _base()
    a = build_candidate(agent_id=AGENT, base=base, base_entities=entities, drafts=[prompt_draft()],
                        published_hash=_none)
    b = build_candidate(agent_id=AGENT, base=base, base_entities=entities, drafts=[prompt_draft()],
                        published_hash=_none)
    c = build_candidate(agent_id=AGENT, base=base, base_entities=entities,
                        drafts=[prompt_draft(text="Otro texto de confirmación.")], published_hash=_none)
    assert a.candidate_hash == b.candidate_hash != c.candidate_hash
    assert len(a.candidate_hash) == 64


def test_suite_travels_with_candidate_but_not_in_release() -> None:
    base, entities = _base()
    cand = build_candidate(agent_id=AGENT, base=base, base_entities=entities,
                           drafts=[prompt_draft(), suite_draft()], published_hash=_none)
    assert [s.id for s in cand.suites] == ["disputas-suite"]
    assert VersionRef(kind="eval_suite", id="disputas-suite", version="1.0.0") in cand.new_versions


def test_no_change_candidate_has_no_new_versions() -> None:
    base, entities = _base()
    cand = build_candidate(agent_id=AGENT, base=base, base_entities=entities, drafts=[],
                           published_hash=lambda r: "x")
    assert cand.new_versions == ()


def test_broken_reference_becomes_violation() -> None:
    base, entities = _base()
    draft = prompt_draft(model_profile="no-existe@1.0.0")
    with pytest.raises(CandidateError) as info:
        build_candidate(agent_id=AGENT, base=base, base_entities=entities, drafts=[draft],
                        published_hash=_none)
    assert info.value.violations and info.value.violations[0].rule == "REG-PIN"


def test_knowledge_snapshot_drafts_are_rejected() -> None:
    base, entities = _base()
    draft = EntityDraft(kind="knowledge_snapshot", content={"id": "kb", "version": "1.0.0", "pages": []},
                        docs=docs())
    with pytest.raises(CandidateError) as info:
        build_candidate(agent_id=AGENT, base=base, base_entities=entities, drafts=[draft],
                        published_hash=_none)
    assert info.value.violations[0].rule == "REG-KNOWLEDGE"


def test_parse_semver() -> None:
    assert parse_semver("1.2.3") == (1, 2, 3)
    assert parse_semver("1.2") is None


def test_interrupt_that_starts_a_flow_follows_the_bumped_flow() -> None:
    base, entities = _base()
    interrupt = Interrupt.model_validate({"id": "reabrir", "priority": 50,
                                          "action": {"type": "start_flow", "flow": "disputa-cargo@1.0.0"}})
    base = base.model_copy(update={"interrupts": [*base.interrupts, interrupt]})
    cand = build_candidate(agent_id=AGENT, base=base, base_entities=entities, drafts=[prompt_draft()],
                           published_hash=_none)
    started = [i.action.flow for i in cand.release.interrupts if isinstance(i.action, StartFlowAction)]
    assert [str(ref) for ref in started] == ["disputa-cargo@1.0.1"]


def test_drafted_version_published_with_other_content_is_a_violation() -> None:
    base, entities = _base()
    taken = {"prompt:p/resumen_radicado@1.1.0": "otro-hash"}
    with pytest.raises(CandidateError) as info:
        build_candidate(agent_id=AGENT, base=base, base_entities=entities, drafts=[prompt_draft()],
                        published_hash=lambda r: taken.get(str(r)))
    assert [v.rule for v in info.value.violations] == ["REG-VERSION-TAKEN"]


def test_drafted_version_published_with_same_content_is_not_new() -> None:
    base, entities = _base()
    first = build_candidate(agent_id=AGENT, base=base, base_entities=entities, drafts=[prompt_draft()],
                            published_hash=_none)
    prompt_ref = VersionRef(kind="prompt", id="p/resumen_radicado", version="1.1.0")
    stored = {prompt_ref: first.content_hashes[prompt_ref]}
    again = build_candidate(agent_id=AGENT, base=base, base_entities=entities, drafts=[prompt_draft()],
                            published_hash=stored.get)
    assert prompt_ref not in again.new_versions
    assert again.candidate_hash == first.candidate_hash


def test_drafted_entity_keeps_its_docs() -> None:
    base, entities = _base()
    cand = build_candidate(agent_id=AGENT, base=base, base_entities=entities, drafts=[prompt_draft()],
                           published_hash=_none)
    assert cand.docs[VersionRef(kind="prompt", id="p/resumen_radicado", version="1.1.0")] == docs()


def test_unreferenced_draft_is_a_violation() -> None:
    base, entities = _base()
    draft = EntityDraft(kind="template", content={"id": "t/huerfana", "version": "1.0.0",
                                                  "locales": {"es": "Hola.", "pt": "Olá."}}, docs=docs())
    with pytest.raises(CandidateError) as info:
        build_candidate(agent_id=AGENT, base=base, base_entities=entities, drafts=[draft],
                        published_hash=_none)
    assert [v.rule for v in info.value.violations] == ["REG-UNREFERENCED"]


def test_schema_violation_does_not_echo_input_values() -> None:
    base, entities = _base()
    draft = prompt_draft(model_profile={"dato": "cust-secreto-123"})
    with pytest.raises(CandidateError) as info:
        build_candidate(agent_id=AGENT, base=base, base_entities=entities, drafts=[draft],
                        published_hash=_none)
    assert info.value.violations[0].rule == "REG-SCHEMA"
    assert "cust-secreto-123" not in info.value.violations[0].message


def test_candidate_without_the_agent_is_a_violation() -> None:
    with pytest.raises(CandidateError) as info:
        build_candidate(agent_id=AGENT, base=None, base_entities=[], drafts=[prompt_draft()],
                        published_hash=_none)
    assert info.value.violations[0].rule == "REG-AGENT"
