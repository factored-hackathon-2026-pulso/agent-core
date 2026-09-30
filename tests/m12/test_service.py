"""`KnowledgeService.read` (m12 §3.1): doble filtro por `purpose`, proyección M7, resultados y eventos."""

from datetime import timedelta
from typing import Any

import pytest

from agent_core.domain import KnowledgeRead, RunState, page_ref
from agent_core.knowledge import KnowledgeService
from tests.m12.helpers import (
    SNAPSHOT,
    CountingSource,
    DownSource,
    LeakySource,
    World,
    advisor_state,
    node,
    principal,
    record,
    run_state,
    standard_records,
)

REF_PLAZOS = page_ref("faq/cargos.md", SNAPSHOT, "plazos")


def _event(events: list[Any]) -> KnowledgeRead:
    (event,) = events
    assert isinstance(event, KnowledgeRead)
    return event


def test_t_m12_01_customer_answer_drops_internal_and_draft_even_if_the_service_returns_them() -> None:
    world = World(LeakySource(standard_records()))  # el servicio ignora la vista y devuelve todo
    for path in ("proc/reversos.md", "faq/borrador.md", "guia/agente.md"):
        state, result, events = world.read(node(path))
        assert result == "denied", path
        assert "kb" not in state.pages, path
        assert _event(events).payload.refs == []
    _, _, events = world.read(node("proc/reversos.md"))
    assert [(f.ref, f.reason) for f in _event(events).payload.filtered_out] == [
        (page_ref("proc/reversos.md", SNAPSHOT), "audience")]
    _, _, events = world.read(node("faq/borrador.md"))
    assert [f.reason for f in _event(events).payload.filtered_out] == ["not_approved"]


def test_a_mixed_request_delivers_nothing_when_any_page_is_filtered() -> None:
    world = World(LeakySource(standard_records()))
    state, result, _ = world.read(node("faq/cargos.md#plazos", "proc/reversos.md"))
    assert result == "denied" and "kb" not in state.pages


def test_ok_delivers_only_the_requested_section_in_model_view() -> None:
    world = World()
    state, result, events = world.read(node("faq/cargos.md#plazos"))
    assert result == "ok"
    (page,) = state.pages["kb"]
    assert page.ref == REF_PLAZOS and page.meta.anchor == "plazos" and page.meta.path == "faq/cargos.md"
    assert "15 días hábiles" in page.content_model and "monto del cargo" not in page.content_model
    payload = _event(events).payload
    assert (payload.result, payload.refs, payload.filtered_out, payload.reason) == ("ok", [REF_PLAZOS], [], None)
    assert payload.purpose == "customer_answer" and payload.node_id == "saber"
    assert page.source.kind == "knowledge" and page.source.ref == REF_PLAZOS


def test_a_page_without_anchor_is_delivered_whole_and_in_order() -> None:
    world = World()
    state, result, _ = world.read(node("faq/cargos.md"))
    assert result == "ok"
    assert "monto del cargo" in state.pages["kb"][0].content_model
    assert state.pages["kb"][0].ref == page_ref("faq/cargos.md", SNAPSHOT)


def test_several_pages_keep_the_order_of_the_node() -> None:
    world = World()
    state, result, events = world.read(node("faq/cargos.md#requisitos", "faq/externa.md", "faq/cargos.md#plazos"))
    assert result == "ok"
    assert [p.meta.anchor for p in state.pages["kb"]] == ["requisitos", None, "plazos"]
    assert len(_event(events).payload.refs) == 3


def test_a_missing_page_or_anchor_is_not_found() -> None:
    world = World()
    for spec in ("faq/no-existe.md", "faq/cargos.md#no-existe"):
        state, result, events = world.read(node(spec))
        assert result == "not_found" and "kb" not in state.pages
        assert _event(events).payload.result == "not_found"


def test_not_found_when_the_service_hides_the_page_and_denied_takes_precedence_over_not_found() -> None:
    world = World(LeakySource(standard_records()))
    _, result, _ = world.read(node("faq/no-existe.md", "proc/reversos.md"))
    assert result == "denied"
    _, result, _ = world.read(node("faq/no-existe.md", "faq/cargos.md"))
    assert result == "not_found"


def test_ok_replaces_previous_pages_under_the_same_save_as_and_leaves_failure_without_them() -> None:
    world = World()
    state, _, _ = world.read(node("faq/cargos.md#plazos"))
    state, result, _ = world.read(node("faq/cargos.md#requisitos"), state)
    assert result == "ok" and [p.meta.anchor for p in state.pages["kb"]] == ["requisitos"]
    state, result, _ = world.read(node("faq/no-existe.md"), state)
    assert result == "not_found" and "kb" not in state.pages  # nada viejo queda citable tras un fallo


def test_other_save_as_names_are_untouched() -> None:
    world = World()
    state, _, _ = world.read(node("faq/cargos.md#plazos", save_as="otra"))
    state, _, _ = world.read(node("faq/externa.md"), state)
    assert set(state.pages) == {"otra", "kb"}


def test_expired_and_not_yet_valid_pages_are_denied_for_customers() -> None:
    world = World(LeakySource(standard_records()))
    for path, reason in (("faq/vencida.md", "expired"), ("faq/futura.md", "not_yet_valid")):
        state, result, events = world.read(node(path))
        assert result == "denied" and "kb" not in state.pages
        assert [f.reason for f in _event(events).payload.filtered_out] == [reason]


def test_vigencia_is_judged_with_the_clock_of_the_context() -> None:
    world = World()
    _, result, _ = world.read(node("faq/vencida.md"))
    assert result == "not_found"  # el servicio real ya la filtró
    leaky = World(LeakySource(standard_records()))
    leaky.clock.advance(timedelta(days=-200))  # 2026-03-12: dentro de la vigencia
    _, result, _ = leaky.read(node("faq/vencida.md"))
    assert result == "ok"


def test_customer_answer_uses_the_translation_when_the_turn_language_differs() -> None:
    world = World()
    state, result, _ = world.read(node("faq/cargos.md#plazos"), run_state(locale="pt"))
    assert result == "ok"
    (page,) = state.pages["kb"]
    assert page.meta.path == "faq/cargos.pt.md" and page.meta.lang == "pt"
    assert page.ref == page_ref("faq/cargos.pt.md", SNAPSHOT, "plazos")
    assert "15 dias úteis" in page.content_model


def test_customer_answer_without_a_translation_in_the_turn_language_is_denied() -> None:
    world = World(LeakySource(standard_records()))
    state, result, events = world.read(node("faq/externa.md"), run_state(locale="pt"))
    assert result == "denied" and "kb" not in state.pages
    assert [f.reason for f in _event(events).payload.filtered_out] == ["lang"]


def test_advisor_view_reads_public_and_internal_but_not_agent_only() -> None:
    world = World()
    state, result, _ = world.read(node("proc/reversos.md", "faq/cargos.md", purpose="advisor_view"),
                                  advisor_state())
    assert result == "ok" and [p.meta.audience for p in state.pages["kb"]] == ["internal", "public"]
    leaky = World(LeakySource(standard_records()))
    _, result, events = leaky.read(node("guia/agente.md", purpose="advisor_view"), advisor_state())
    assert result == "denied" and [f.reason for f in _event(events).payload.filtered_out] == ["audience"]


def test_advisor_view_does_not_require_approval_or_the_turn_language() -> None:
    world = World()
    state, result, _ = world.read(node("faq/borrador.md", purpose="advisor_view"), advisor_state(locale="pt"))
    assert result == "ok" and state.pages["kb"][0].meta.status == "draft"


def test_agent_guidance_reads_any_audience() -> None:
    world = World()
    state, result, _ = world.read(node("guia/agente.md", "proc/reversos.md", "faq/borrador.md",
                                       purpose="agent_guidance"), advisor_state())
    assert result == "ok" and len(state.pages["kb"]) == 3


def test_a_customer_asking_for_an_advisor_view_gets_nothing_and_the_source_is_not_called() -> None:
    source = CountingSource(standard_records())
    world = World(source)
    state, result, events = world.read(node("proc/reversos.md", purpose="advisor_view"))  # principal customer
    assert result == "denied" and "kb" not in state.pages and source.reads == 0
    assert _event(events).payload.filtered_out[0].reason == "view"


def test_the_service_is_asked_with_the_view_of_the_principal_and_purpose() -> None:
    seen: list[Any] = []

    class Spy(CountingSource):
        def read(self, path: str, snapshot: str, view: Any) -> Any:
            seen.append((path, snapshot, view))
            return super().read(path, snapshot, view)

    World(Spy(standard_records())).read(node("faq/cargos.md#plazos"))
    (path, snapshot, view), = seen
    assert (path, snapshot) == ("faq/cargos.md", SNAPSHOT)
    assert view.audiences == frozenset({"public"}) and view.approved_only is True


def test_a_page_from_another_snapshot_is_filtered_even_if_the_service_returns_it() -> None:
    class Wrong(LeakySource):
        def read(self, path: str, snapshot: str, view: Any) -> Any:
            found = super().read(path, snapshot, view)
            return None if found is None else found.model_copy(
                update={"meta": found.meta.model_copy(update={"snapshot": "kb-otro@9.9.9"})})

    state, result, events = World(Wrong(standard_records())).read(node("faq/cargos.md"))
    assert result == "denied" and "kb" not in state.pages
    assert [f.reason for f in _event(events).payload.filtered_out] == ["snapshot"]


@pytest.mark.parametrize("error", [ConnectionError("x"), TimeoutError("x"), RuntimeError("x")])
def test_a_source_that_fails_leaves_by_not_found_with_a_source_unavailable_event(error: Exception) -> None:
    class Failing(DownSource):
        def read(self, path: str, snapshot: str, view: Any) -> Any:
            raise error

    state, result, events = World(Failing()).read(node("faq/cargos.md"))
    assert result == "not_found" and "kb" not in state.pages
    payload = _event(events).payload
    assert (payload.result, payload.reason, payload.refs) == ("not_found", "source_unavailable", [])


def test_translation_lookup_with_a_source_that_fails_in_index_is_also_source_unavailable() -> None:
    class IndexDown(LeakySource):
        def index(self, snapshot: str, view: Any) -> Any:
            raise ConnectionError("x")

    _, result, events = World(IndexDown(standard_records())).read(node("faq/externa.md"), run_state(locale="pt"))
    assert result == "not_found" and _event(events).payload.reason == "source_unavailable"


def test_a_release_without_a_snapshot_is_not_found_and_never_calls_the_source() -> None:
    source = CountingSource(standard_records())
    state, result, events = World(source, snapshot=None).read(node("faq/cargos.md"))
    assert result == "not_found" and source.reads == 0
    assert _event(events).payload.reason == "no_snapshot"


def test_navigate_is_closed_at_runtime_and_leaves_by_not_found() -> None:
    source = CountingSource(standard_records())
    nav = node(mode="navigate", scope="faq", selector="selector@1")
    state, result, events = World(source).read(nav)
    assert result == "not_found" and source.reads == 0 and "kb" not in state.pages
    assert _event(events).payload.reason == "navigate_unavailable"


def test_external_content_is_wrapped_as_untrusted_text_and_internal_content_is_not() -> None:
    world = World()
    state, _, _ = world.read(node("faq/externa.md", "faq/cargos.md#plazos"))
    external, internal = state.pages["kb"]
    assert external.content_model.startswith("<datos_no_confiables") and "Texto de fuera" in external.content_model
    assert "datos_no_confiables" not in internal.content_model


def test_pii_inside_a_page_never_reaches_the_model_view() -> None:
    body = "# Contacto\n\nEscribe a persona.sintetica@ejemplo.test para soporte.\n"
    source = LeakySource([record("faq/contacto.md", body),
                          record("faq/contacto-ext.md", body, source_refs=["https://ejemplo.test/x"])])
    world = World(source)
    state, result, _ = world.read(node("faq/contacto.md", "faq/contacto-ext.md"))
    assert result == "ok"
    for page in state.pages["kb"]:
        assert "persona.sintetica@ejemplo.test" not in page.content_model
        assert "⟦" in page.content_model


def test_the_event_never_carries_page_content() -> None:
    world = World()
    _, _, events = world.read(node("faq/cargos.md#plazos"))
    assert "15 días" not in events[0].model_dump_json()


def test_facts_and_other_state_are_untouched_and_the_input_state_is_not_mutated() -> None:
    world = World()
    before = run_state()
    after, _, _ = world.read(node("faq/cargos.md#plazos"), before)
    assert before.pages == {} and after.facts == before.facts and after.slots == before.slots
    assert after.model_copy(update={"pages": {}}) == before


def test_determinism_same_inputs_same_events() -> None:
    first = World().read(node("faq/cargos.md#plazos"))
    second = World().read(node("faq/cargos.md#plazos"))
    assert first[0] == second[0] and first[2][0].model_dump() == second[2][0].model_dump()


def test_the_customer_principal_is_taken_from_the_run() -> None:
    world = World()
    other = run_state(principal=principal(id="cust-002"), subject={"kind": "customer", "ref": "cust-002"})
    _, result, _ = world.read(node("faq/cargos.md#plazos"), other)
    assert result == "ok"


def test_public_surface_of_m12() -> None:
    import agent_core.knowledge as m12

    assert set(m12.__all__) == {"KnowledgeContext", "KnowledgeService"}
    assert isinstance(World().service, KnowledgeService)
    assert isinstance(run_state(), RunState)
