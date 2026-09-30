import pytest

from agent_core.registry.candidate import CandidateError, build_candidate
from agent_core.registry.models import EntityDraft
from agent_core.registry.validation import Limits, check_draft_limits, validate_candidate
from tests.registry.helpers import AGENT, demo_pinned, docs, prompt_draft, suite_draft


def _cand(*drafts: EntityDraft):  # type: ignore[no-untyped-def]
    pinned = demo_pinned()
    return pinned, build_candidate(agent_id=AGENT, base=pinned.release, base_entities=pinned.entities,
                                   drafts=list(drafts), published_hash=lambda r: None)


def _base_versions(pinned) -> dict[tuple[str, str], str]:  # type: ignore[no-untyped-def]
    return {(k.value, i): v for k, by in pinned.release.entities.items() for i, v in by.items()}


def test_valid_candidate_has_no_violations() -> None:
    pinned, cand = _cand(prompt_draft(), suite_draft())
    assert validate_candidate(cand, base_versions=_base_versions(pinned),
                              drafted={("prompt", "p/resumen_radicado")}) == []


def test_version_not_greater_than_base_is_violation() -> None:  # T-REG-05
    pinned, cand = _cand(prompt_draft(version="0.9.0"))
    out = validate_candidate(cand, base_versions=_base_versions(pinned),
                             drafted={("prompt", "p/resumen_radicado")})
    assert [v.rule for v in out] == ["REG-VERSION"]
    assert "mayor" in out[0].message


def test_entity_over_size_limit_is_violation() -> None:  # T-REG-06
    pinned, cand = _cand(prompt_draft(text="x" * 5000))
    out = validate_candidate(cand, base_versions=_base_versions(pinned),
                             drafted={("prompt", "p/resumen_radicado")}, limits=Limits(max_entity_bytes=1000))
    assert "REG-LIMIT" in [v.rule for v in out]


def test_too_many_changes_is_violation() -> None:
    drafts = [prompt_draft()] * 3
    assert [v.rule for v in check_draft_limits(drafts, Limits(max_changes=2))] == ["REG-LIMIT"]


def test_suite_of_other_agent_is_violation() -> None:
    pinned, cand = _cand(prompt_draft(), suite_draft(agent_id="otro"))
    out = validate_candidate(cand, base_versions=_base_versions(pinned), drafted=set())
    assert "REG-SUITE" in [v.rule for v in out]


def test_unknown_kind_and_bad_schema_are_violations() -> None:  # Review Focus 4
    bad_kind = EntityDraft(kind="nope", content={"id": "x", "version": "1.0.0"}, docs=docs())
    bad_schema = EntityDraft(kind="template", content={"id": "t/x", "version": "1.0.0", "locales": 3},
                             docs=docs())
    with pytest.raises(CandidateError) as info:
        _cand(bad_kind, bad_schema)
    assert sorted(v.rule for v in info.value.violations) == ["REG-KIND", "REG-SCHEMA"]
    assert all(v.message for v in info.value.violations)
