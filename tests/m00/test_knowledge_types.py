"""Tipos de M12 que viven en M0 (m12 §2, m00 §2.5–§2.10): páginas, vista, nodo `knowledge`, `pages` y
evento."""

from datetime import date
from typing import Any

import pytest
from pydantic import ValidationError

import agent_core.domain as domain
from agent_core.domain import (
    EVENT_EMITTERS,
    EVENT_TYPES,
    RESULTS,
    Flow,
    GenerateConfig,
    KnowledgeNode,
    KnowledgeRead,
    KnowledgeReadPayload,
    KnowledgeView,
    PageMeta,
    PageRecord,
    PageView,
    RespondNode,
    RunState,
    page_ref,
    parse_page_ref,
    parse_page_spec,
)
from testing.builders import NOW, run_state


def meta(**over: Any) -> PageMeta:
    base: dict[str, Any] = {
        "path": "faq/cargos.md", "anchor": "plazos", "snapshot": "kb-base@1.0.0", "type": "faq",
        "audience": "public", "status": "approved", "approved_by": "revisor-demo", "lang": "es"}
    return PageMeta.model_validate(base | over)


def knowledge_node(**config: Any) -> dict[str, Any]:
    cfg = {"mode": "read", "pages": ["faq/cargos.md#plazos"], "purpose": "customer_answer",
           "save_as": "kb"} | config
    return {"id": "saber", "type": "knowledge", "config": cfg,
            "next": {"ok": "fin", "not_found": "fin", "denied": "fin"}}


def test_page_ref_round_trip() -> None:
    ref = page_ref("faq/cargos.md", "kb-base@1.0.0", "plazos")
    assert ref == "faq/cargos.md@kb-base@1.0.0#plazos"
    parsed = parse_page_ref(ref)
    assert parsed is not None and (parsed.path, parsed.snapshot, parsed.anchor) == (
        "faq/cargos.md", "kb-base@1.0.0", "plazos")
    assert page_ref("index.md", "kb-base@1.0.0") == "index.md@kb-base@1.0.0"
    whole = parse_page_ref("index.md@kb-base@1.0.0")
    assert whole is not None and whole.anchor is None


@pytest.mark.parametrize(
    "text", ["", "f_1234", "page:1", "a@b#", "../x@s#a", "x@", "@s#a", "x@s#a b", "x@s#a#b"])
def test_parse_page_ref_rejects_what_is_not_a_page_ref(text: str) -> None:
    assert parse_page_ref(text) is None


def test_parse_page_spec_is_the_authoring_form() -> None:
    spec = parse_page_spec("faq/cargos.md#plazos")
    assert (spec.path, spec.anchor) == ("faq/cargos.md", "plazos")
    assert parse_page_spec("faq/cargos.md").anchor is None
    for bad in ("", "#a", "faq/../x.md", "faq//x.md", "x.md#", "x.md@s#a"):
        with pytest.raises(ValueError):
            parse_page_spec(bad)


def test_page_meta_is_coherent() -> None:
    assert meta().audience == "public"
    with pytest.raises(ValidationError):  # aprobada si y solo si tiene approved_by
        meta(approved_by=None)
    with pytest.raises(ValidationError):
        meta(status="draft", approved_by="x")
    with pytest.raises(ValidationError):
        meta(valid_from=date(2026, 2, 1), valid_to=date(2026, 1, 1))
    with pytest.raises(ValidationError):
        meta(audience="secreta")


def test_page_view_carries_its_provenance_as_a_knowledge_fact_source() -> None:
    ref = page_ref("faq/cargos.md", "kb-base@1.0.0", "plazos")
    view = PageView(ref=ref, meta=meta(), content_model="Respondemos en 15 días.")
    assert view.source.kind == "knowledge" and view.source.ref == ref
    # la procedencia se deriva: no se guarda ni se serializa como un hecho aparte
    assert "source" not in view.model_dump()


def test_page_view_ref_must_match_its_meta() -> None:
    with pytest.raises(ValidationError):
        PageView(ref=page_ref("otra.md", "kb-base@1.0.0"), meta=meta(), content_model="x")


def test_page_record_never_shows_its_full_content() -> None:
    record = PageRecord(meta=meta(anchor=None), content="CONTENIDO-COMPLETO-SECRETO")
    assert "CONTENIDO-COMPLETO-SECRETO" not in repr(record)
    assert "CONTENIDO-COMPLETO-SECRETO" not in record.model_dump_json()


def test_knowledge_view_is_frozen_and_closed() -> None:
    view = KnowledgeView(audiences=frozenset({"public"}), approved_only=True)
    with pytest.raises(ValidationError):
        view.approved_only = False  # type: ignore[misc]
    with pytest.raises(ValidationError):
        KnowledgeView(audiences=frozenset({"otra"}), approved_only=True)  # type: ignore[arg-type]


def test_knowledge_node_read_is_a_node_of_the_closed_catalog() -> None:
    flow = Flow.model_validate({"id": "f", "version": "1.0.0", "priority": 1, "nodes": [knowledge_node(), {
        "id": "fin", "type": "end", "config": {"outcome": "resolved"}}]})
    node = flow.nodes[0]
    assert isinstance(node, KnowledgeNode)
    assert node.config.mode == "read" and node.config.purpose == "customer_answer"


def test_knowledge_node_read_requires_pages_and_forbids_navigate_fields() -> None:
    for bad in ({"pages": []}, {"scope": "faq/"}, {"selector": "sel@1"}):
        with pytest.raises(ValidationError):
            KnowledgeNode.model_validate(knowledge_node(**bad))


def test_knowledge_node_navigate_requires_scope_and_selector_and_forbids_pages() -> None:
    ok = knowledge_node(mode="navigate", pages=[], scope="faq", selector="selector@1")
    assert KnowledgeNode.model_validate(ok).config.scope == "faq"
    for bad in ({"scope": None}, {"selector": None}, {"pages": ["faq/cargos.md"]}):
        with pytest.raises(ValidationError):
            KnowledgeNode.model_validate(knowledge_node(**(dict(mode="navigate", pages=[], scope="faq",
                                                              selector="selector@1") | bad)))


def test_knowledge_node_rejects_a_bad_page_spec_and_an_unknown_purpose() -> None:
    for bad in ({"pages": ["../secreto.md"]}, {"pages": ["x.md#"]}, {"purpose": "todos"}, {"save_as": "KB"}):
        with pytest.raises(ValidationError):
            KnowledgeNode.model_validate(knowledge_node(**bad))


def test_results_of_the_knowledge_node() -> None:
    assert RESULTS["knowledge"] == frozenset({"ok", "not_found", "denied", "low_confidence"})
    assert "knowledge" not in domain.TERMINAL and "knowledge" not in domain.WAITING


def test_generate_takes_knowledge_from_and_purpose_instead_of_knowledge_refs() -> None:
    cfg = GenerateConfig.model_validate({
        "prompt_ref": "p/gen", "fallback_template_ref": "t/seguro", "knowledge_from": ["kb"]})
    assert cfg.knowledge_from == ["kb"] and cfg.purpose == "customer_answer"  # el defecto es el más estricto
    assert not hasattr(cfg, "knowledge_refs")
    with pytest.raises(ValidationError):
        GenerateConfig.model_validate({"prompt_ref": "p/gen", "fallback_template_ref": "t/seguro",
                                       "knowledge_refs": ["x"]})
    with pytest.raises(ValidationError):
        GenerateConfig.model_validate({"prompt_ref": "p/gen", "fallback_template_ref": "t/seguro",
                                       "knowledge_from": ["KB"]})
    advisor = GenerateConfig.model_validate({
        "prompt_ref": "p/gen", "fallback_template_ref": "t/seguro", "purpose": "advisor_view"})
    assert advisor.purpose == "advisor_view"


def test_respond_generate_with_knowledge_from_validates() -> None:
    node = RespondNode.model_validate({"id": "r", "type": "respond", "config": {"generate": {
        "prompt_ref": "p/gen", "fallback_template_ref": "t/seguro", "knowledge_from": ["kb"]}}})
    assert node.config.generate is not None and node.config.generate.knowledge_from == ["kb"]


def test_run_state_pages_default_empty_and_round_trip() -> None:
    assert run_state().pages == {}
    ref = page_ref("faq/cargos.md", "kb-base@1.0.0", "plazos")
    state = run_state(pages={"kb": [PageView(ref=ref, meta=meta(), content_model="texto")]})
    again = RunState.model_validate_json(state.model_dump_json())
    assert again.pages["kb"][0].ref == ref and again == state


def test_run_state_pages_key_must_be_a_save_as_name() -> None:
    with pytest.raises(ValidationError):
        run_state(pages={"Mal Nombre": []})


def test_knowledge_read_event() -> None:
    payload = KnowledgeReadPayload.model_validate({
        "node_id": "saber", "purpose": "customer_answer", "result": "denied",
        "refs": [], "filtered_out": [{"ref": "proc/reversos.md@kb-base@1.0.0", "reason": "audience"}],
        "reason": None})
    event = KnowledgeRead.model_validate({
        "event_id": "e1", "run_id": "r1", "release": "rel", "ts": NOW, "payload": payload})
    assert event.type == "knowledge_read" and EVENT_TYPES["knowledge_read"] is KnowledgeRead
    assert EVENT_EMITTERS["knowledge_read"] == frozenset({"M12"})
    assert payload.filtered_out[0].reason == "audience"


def test_knowledge_read_payload_reasons_are_closed() -> None:
    base: dict[str, Any] = {"node_id": "s", "purpose": "customer_answer", "result": "not_found",
                            "refs": [], "filtered_out": []}
    for reason in ("source_unavailable", "navigate_unavailable", "no_snapshot", None):
        assert KnowledgeReadPayload.model_validate(base | {"reason": reason}).reason == reason
    with pytest.raises(ValidationError):
        KnowledgeReadPayload.model_validate(base | {"reason": "otro"})


def test_schema_version_is_major_one() -> None:
    assert domain.SCHEMA_VERSION == "1.1.0"  # 1.1.0: write_draft (ADR 0019); la mayor sigue siendo 1
