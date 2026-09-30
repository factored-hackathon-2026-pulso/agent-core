"""Flows y snapshot sintéticos para G0-17…G0-21: cada prueba muta una copia de `knowledge_flow()`."""

from copy import deepcopy
from typing import Any

from agent_core.domain import DecisionModelDef, KnowledgePage, KnowledgeSnapshot
from agent_core.flows import Violation, validate_flow
from agent_core.flows.registry import AuthoringRegistry
from tests.m01.cases import ENTITIES, base, flow, node

SHA = "a" * 64


def page(path: str, **over: Any) -> KnowledgePage:
    data: dict[str, Any] = {"path": path, "hash": SHA, "audience": "public", "status": "approved",
                            "approved_by": "revisor-demo", "lang": "es"}
    merged = data | over
    if merged["status"] == "draft":
        merged["approved_by"] = None
    return KnowledgePage.model_validate(merged)


def snapshot(*extra: KnowledgePage) -> KnowledgeSnapshot:
    pages = [page("faq/cargos.md"), page("faq/requisitos.md"), page("index.md"),
             page("proc/reversos.md", audience="internal"), page("faq/borrador.md", status="draft"), *extra]
    return KnowledgeSnapshot.model_validate({"id": "kb-base", "version": "1.0.0", "pages": pages})


def selector(enum: list[str], prop: str = "path") -> DecisionModelDef:
    return DecisionModelDef.model_validate({
        "id": "selector", "version": "1.0.0",
        "output_schema": {"type": "object", "properties": {prop: {"enum": enum}}},
        "calibrated_fields": [prop], "providers": [{"provider": "rule"}], "calibration": {"method": "none"}})


def registry(*extra: Any) -> AuthoringRegistry:
    return AuthoringRegistry.from_entities([*ENTITIES, *extra])


def knowledge_node(node_id: str = "saber", **config: Any) -> dict[str, Any]:
    cfg = {"mode": "read", "pages": ["faq/cargos.md#plazos"], "purpose": "customer_answer",
           "save_as": "kb"} | config
    results = {"ok": "buscar", "not_found": "esc", "denied": "esc"}
    if cfg["mode"] == "navigate":
        results["low_confidence"] = "esc"
    return {"id": node_id, "type": "knowledge", "config": cfg, "next": results}


def explica(**generate: Any) -> dict[str, Any]:
    cfg = {"prompt_ref": "p/gen", "fallback_template_ref": "t/seguro", "knowledge_from": ["kb"],
           "purpose": "customer_answer"} | generate
    return {"id": "explica", "type": "respond", "config": {"generate": cfg}, "next": {"next": "fin"}}


def knowledge_flow() -> dict[str, Any]:
    """`base()` con `saber` (knowledge read, customer_answer) entre `pedir` y `buscar`, y `explica`
    (respond generate con `knowledge_from: [kb]`) entre `ok_msg` y `fin`. Es válido para G0-01…G0-24."""
    d = base()
    node(d, "pedir")["next"]["ok"] = "saber"
    node(d, "ok_msg")["next"]["next"] = "explica"
    d["nodes"] += [knowledge_node(), explica()]
    return deepcopy(d)


def check(d: dict[str, Any], *extra: Any) -> list[Violation]:
    return validate_flow(flow(d), registry(*extra))


def rules(violations: list[Violation]) -> set[str]:
    return {v.rule for v in violations}
