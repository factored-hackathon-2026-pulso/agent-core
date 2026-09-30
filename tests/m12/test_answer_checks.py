"""Comprobaciones 6 (`page_citations`) y 7 (`page_audience`) del validador de M8 (m12 §3.4) y su cableado.

T-M12-02: una respuesta al cliente que cita una página no `approved` se rechaza (comprobación 7).
T-M12-03: una cita a una página de un `save_as` no listado se rechaza (comprobación 6)."""

from datetime import date, datetime
from typing import Any

from agent_core.domain import GenerateConfig, PageView, page_ref, parse_page_ref
from agent_core.response import CHECKS, Draft, Responder, validate
from agent_core.response.checks import check_page_audience, check_page_citations
from testing.fakes.gateway import gen
from tests.m08.helpers import GOOD, World, make_ctx
from tests.m12.helpers import NOW, SNAPSHOT, meta

PUBLIC = page_ref("faq/cargos.md", SNAPSHOT, "plazos")
DRAFT = page_ref("faq/borrador.md", SNAPSHOT)
INTERNAL = page_ref("proc/reversos.md", SNAPSHOT)
AGENT_ONLY = page_ref("guia/agente.md", SNAPSHOT)
EXPIRED = page_ref("faq/vencida.md", SNAPSHOT)
FUTURE = page_ref("faq/futura.md", SNAPSHOT)
OTHER_NODE = page_ref("faq/otra.md", SNAPSHOT)

METAS = {
    PUBLIC: meta(anchor="plazos"),
    DRAFT: meta("faq/borrador.md", status="draft"),
    INTERNAL: meta("proc/reversos.md", audience="internal"),
    AGENT_ONLY: meta("guia/agente.md", audience="agent_only"),
    EXPIRED: meta("faq/vencida.md", valid_to="2026-06-30"),
    FUTURE: meta("faq/futura.md", valid_from="2027-01-01"),
}
ALL = frozenset(METAS)
CONTENT = {PUBLIC: "Respondemos tu disputa en 15 días hábiles.", DRAFT: "Borrador de 7 días."}


def _ctx(**over: Any) -> Any:
    base: dict[str, Any] = {"page_refs": ALL, "pages_meta": METAS, "pages_model_view": CONTENT,
                            "customer_facing": True, "now": NOW}
    return make_ctx({}, set(), **(base | over))


def _draft(*citations: str, text: str = "Respuesta.") -> Draft:
    return Draft(text=text, citations=list(citations))


# --- T-M12-03 · comprobación 6 ----------------------------------------------------------------------------


def test_t_m12_03_a_page_of_a_save_as_not_in_knowledge_from_is_rejected() -> None:
    ctx = _ctx(page_refs=frozenset({PUBLIC}), pages_meta={**METAS, OTHER_NODE: meta("faq/otra.md")})
    result = validate(_draft(OTHER_NODE), ctx)
    assert not result.ok
    assert [f.check for f in result.failures if f.check == "page_citations"] == ["page_citations"]
    assert "cita 1" in result.failures[0].detail or any("cita 1" in f.detail for f in result.failures)


def test_a_page_of_a_listed_save_as_passes_check_6() -> None:
    assert check_page_citations(_draft(PUBLIC), _ctx(page_refs=frozenset({PUBLIC}))) == []


def test_check_6_ignores_fact_citations_and_non_page_format_strings() -> None:
    assert check_page_citations(_draft("f-pqr", "page:1"), _ctx(page_refs=frozenset())) == []


def test_check_6_reports_one_failure_per_bad_page_in_order_without_echoing_the_citation() -> None:
    ctx = _ctx(page_refs=frozenset({PUBLIC}))
    failures = check_page_citations(_draft(OTHER_NODE, PUBLIC, DRAFT), ctx)
    assert [f.detail for f in failures] == ["cita 1: página_no_listada", "cita 3: página_no_listada"]
    assert all("faq" not in f.detail for f in failures)


def test_check_2_does_not_double_report_page_format_citations() -> None:
    result = validate(_draft(OTHER_NODE), _ctx(page_refs=frozenset()))
    assert {f.check for f in result.failures} <= {"page_citations", "page_audience"}


# --- T-M12-02 · comprobación 7 ----------------------------------------------------------------------------


def test_t_m12_02_a_customer_answer_citing_a_non_approved_page_is_rejected() -> None:
    result = validate(_draft(DRAFT), _ctx())
    assert not result.ok
    assert "page_audience" in [f.check for f in result.failures]


def test_check_7_accepts_only_public_approved_and_current_pages_for_customers() -> None:
    assert check_page_audience(_draft(PUBLIC), _ctx()) == []
    for bad in (DRAFT, INTERNAL, AGENT_ONLY, EXPIRED, FUTURE):
        failures = check_page_audience(_draft(bad), _ctx())
        assert [f.check for f in failures] == ["page_audience"], bad


def test_check_7_uses_the_instant_of_the_clock_for_the_vigencia() -> None:
    later = datetime(2027, 2, 1, tzinfo=NOW.tzinfo)
    assert check_page_audience(_draft(FUTURE), _ctx(now=later)) == []
    assert check_page_audience(_draft(PUBLIC), _ctx(now=later)) == []
    earlier = datetime(2026, 3, 1, tzinfo=NOW.tzinfo)
    assert check_page_audience(_draft(EXPIRED), _ctx(now=earlier)) == []
    assert meta("faq/vencida.md", valid_to="2026-06-30").valid_to == date(2026, 6, 30)


def test_check_7_fails_closed_without_clock_or_metadata_for_a_customer() -> None:
    assert check_page_audience(_draft(PUBLIC), _ctx(now=None))[0].check == "page_audience"
    unknown = page_ref("faq/sin-meta.md", SNAPSHOT)
    assert check_page_audience(_draft(unknown), _ctx())[0].check == "page_audience"


def test_check_7_is_not_applied_to_answers_that_are_not_for_the_customer() -> None:
    advisor = _ctx(customer_facing=False)
    assert check_page_audience(_draft(INTERNAL, DRAFT), advisor) == []


def test_check_7_reports_one_failure_per_page_with_no_echo_of_the_citation() -> None:
    failures = check_page_audience(_draft(DRAFT, PUBLIC, INTERNAL), _ctx())
    assert [f.detail for f in failures] == ["cita 1: página_no_aprobada", "cita 3: audiencia_no_pública"]
    assert all("faq" not in f.detail and "proc" not in f.detail for f in failures)


def test_the_two_new_checks_are_registered_in_order_after_the_existing_five() -> None:
    assert [check for check, _ in CHECKS] == [
        "format", "citations", "numbers", "tokens_pii", "language", "page_citations", "page_audience"]


# --- comprobación 3: las cifras de páginas citadas cuentan como las de los hechos --------------------------


def test_figures_of_a_cited_page_support_the_numbers_of_the_text() -> None:
    ctx = _ctx()
    ok = validate(_draft(PUBLIC, text="Respondemos tu disputa en 15 días hábiles."), ctx)
    assert ok.ok, ok.failures
    bad = validate(_draft(PUBLIC, text="Respondemos tu disputa en 30 días hábiles."), ctx)
    assert [f.check for f in bad.failures] == ["numbers"]


def test_figures_of_a_page_that_is_not_cited_do_not_count() -> None:
    result = validate(_draft(text="Respondemos en 15 días hábiles."), _ctx())
    assert [f.check for f in result.failures] == ["numbers"]


# --- cableado: Responder.generate arma las páginas permitidas desde RunState.pages -------------------------

PAGE_CONFIG = GenerateConfig.model_validate({
    "prompt_ref": "resumen@1.0.0", "allowed_facts": ["facts.pqr.value.id"],
    "fallback_template_ref": "respaldo@1.0.0", "knowledge_from": ["kb"]})


def _view(ref: str = PUBLIC, content: str = "Respondemos tu disputa en 15 días hábiles.",
          **over: Any) -> PageView:
    parsed = parse_page_ref(ref)
    assert parsed is not None
    return PageView(ref=ref, meta=meta(parsed.path, anchor=parsed.anchor, **over), content_model=content)


def _world(script: Any, pages: dict[str, list[PageView]], **kwargs: Any) -> World:
    w = World(script, **kwargs)
    w.state = w.state.model_copy(update={"pages": pages})
    return w


def test_generate_lets_the_model_cite_a_page_of_a_listed_save_as() -> None:
    cited = gen("Respondemos tu disputa en 15 días hábiles.", [PUBLIC])
    w = _world([cited], {"kb": [_view()]})
    message, rejected, _ = Responder(w.registry).generate(PAGE_CONFIG, w.state, w.ctx)
    assert message.kind == "generated" and rejected == []  # type: ignore[union-attr]
    (call,) = w.gateway.calls
    content = "Respondemos tu disputa en 15 días hábiles."
    assert call.inputs["pages"] == {"kb": [{"ref": PUBLIC, "content": content}]}


def test_generate_rejects_a_page_of_a_save_as_not_listed_and_falls_back() -> None:
    other = _view(page_ref("faq/otra.md", SNAPSHOT), "Otra página de 15 días.")
    text = gen("Respondemos en 15 días.", [other.ref])
    w = _world([text, text], {"kb": [_view()], "otro": [other]})
    message, rejected, _ = Responder(w.registry).generate(PAGE_CONFIG, w.state, w.ctx)
    assert message.kind == "template"  # type: ignore[union-attr]
    assert "page_citations" in rejected[0].failures


def test_generate_rejects_a_non_approved_page_cited_in_a_customer_answer() -> None:
    draft = _view(DRAFT, "Borrador de 7 días.", status="draft")
    text = gen("Borrador de 7 días.", [draft.ref])
    w = _world([text, text], {"kb": [draft]})
    message, rejected, _ = Responder(w.registry).generate(PAGE_CONFIG, w.state, w.ctx)
    assert message.kind == "template" and "page_audience" in rejected[0].failures  # type: ignore[union-attr]


def test_generate_judges_the_vigencia_with_the_clock_of_its_context() -> None:
    expired = _view(EXPIRED, "Política de 9 días.", valid_to="2026-06-30")
    text = gen("Política de 9 días.", [expired.ref])
    w = _world([text, text], {"kb": [expired]})
    message, rejected, _ = Responder(w.registry).generate(PAGE_CONFIG, w.state, w.ctx)
    assert message.kind == "template" and "page_audience" in rejected[0].failures  # type: ignore[union-attr]


def test_generate_applies_the_customer_rule_to_every_answer_of_a_customer_run() -> None:
    """Un `respond` que se declara `advisor_view` en un run de cliente sigue sin poder citar páginas
    internas."""
    internal = _view(INTERNAL, "Reverso de 4 pasos.", audience="internal")
    config = PAGE_CONFIG.model_copy(update={"purpose": "advisor_view"})
    text = gen("Reverso de 4 pasos.", [internal.ref])
    w = _world([text, text], {"kb": [internal]})
    message, rejected, _ = Responder(w.registry).generate(config, w.state, w.ctx)
    assert message.kind == "template" and "page_audience" in rejected[0].failures  # type: ignore[union-attr]


def test_generate_without_knowledge_from_behaves_as_before() -> None:
    w = World([gen(GOOD, ["f-pqr"])])
    message, rejected, _ = w.run()
    assert message.kind == "generated" and rejected == []  # type: ignore[union-attr]
    (call,) = w.gateway.calls
    assert "pages" not in call.inputs
