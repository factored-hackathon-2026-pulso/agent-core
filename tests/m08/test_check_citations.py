from typing import Any

from agent_core.response.checks import check_citations
from agent_core.response.types import Draft, Failure
from tests.m08.helpers import make_ctx, make_fact

FACTS = {"f1": make_fact("f1", {"a": 1}), "f2": make_fact("f2", {"b": 2})}


def _check(citations: list[str], allowed: set[str], **over: Any) -> list[Failure]:
    return check_citations(Draft(text="ok", citations=citations), make_ctx(FACTS, allowed, **over))


def test_existing_and_allowed_citation_passes() -> None:
    assert _check(["f1"], {"f1", "f2"}) == []


def test_t_m8_05_citation_to_a_fact_not_allowed_by_the_node_fails() -> None:
    failures = _check(["f2"], {"f1"})
    assert [f.check for f in failures] == ["citations"] and "cita_no_permitida" in failures[0].detail


def test_citation_to_a_missing_fact_fails() -> None:
    failures = _check(["f9"], {"f9"})
    assert [f.check for f in failures] == ["citations"] and "cita_inexistente" in failures[0].detail


def test_any_page_ref_fails_while_pages_are_empty() -> None:
    failures = _check(["page:1"], {"page:1"})
    assert "cita_inexistente" in failures[0].detail


def test_page_ref_present_and_allowed_passes() -> None:
    assert _check(["page:1"], {"page:1"}, pages_model_view={"page:1": "texto"}) == []


def test_one_failure_per_bad_citation_in_order() -> None:
    failures = _check(["f9", "f1", "f2"], {"f1"})
    assert [f.detail for f in failures] == ["cita 1: cita_inexistente", "cita 3: cita_no_permitida"]


def test_detail_never_repeats_the_model_written_citation() -> None:
    (failure,) = _check(["SECRETO-XYZ"], set())
    assert "SECRETO" not in failure.detail


def test_no_citations_is_fine() -> None:
    assert _check([], set()) == []
