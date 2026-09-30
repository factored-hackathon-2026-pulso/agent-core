"""`pin_release` (la publicación simulada) también exige G0-17, G0-19 y G0-20 con el snapshot de su release, y
fija el snapshot y el selector de `navigate` a versiones exactas (T-M12-04: un flow así no se publica)."""

from typing import Any

import pytest

from agent_core.domain import EntityKind, LanguageDetection, SchemaError
from agent_core.flows import pin_release
from agent_core.flows.registry import AuthoringRegistry, ReleaseDecl
from tests.m01.cases import ENTITIES, agent, flow, node
from tests.m12.cases import knowledge_flow, knowledge_node, selector, snapshot

LANG = LanguageDetection.model_validate(
    {"id": "lang", "version": "1.0.0", "detector": "lingua@1.4.0", "candidates": ["es", "pt"],
     "min_letters": 12, "min_letters_unsupported": 20})


def _registry(d: dict[str, Any], *extra: Any, knowledge: str | None = "kb-base@1") -> AuthoringRegistry:
    d["id"] = "base"
    decl = ReleaseDecl.model_validate({
        "id": "r", "agents": [{"agent": "atencion@1"}], "flows": ["base@1.0.0"],
        "language_detection": "lang@1", **({"knowledge": knowledge} if knowledge else {})})
    return AuthoringRegistry.from_entities([*ENTITIES, flow(d), agent(), LANG, *extra], [decl])


def test_a_valid_knowledge_release_pins_the_snapshot_to_an_exact_version() -> None:
    pinned = pin_release(_registry(knowledge_flow(), snapshot()), "r")
    assert str(pinned.release.knowledge_snapshot) == "kb-base@1.0.0"
    assert pinned.release.entities[EntityKind.knowledge_snapshot] == {"kb-base": "1.0.0"}


def test_pin_rejects_a_fixed_page_that_is_not_in_the_snapshot() -> None:
    d = knowledge_flow()
    node(d, "saber")["config"]["pages"] = ["faq/no-existe.md"]
    with pytest.raises(SchemaError, match="G0-17"):
        pin_release(_registry(d, snapshot()), "r")


def test_pin_rejects_a_customer_answer_page_that_is_not_public_and_approved() -> None:
    d = knowledge_flow()
    node(d, "saber")["config"]["pages"] = ["proc/reversos.md"]
    with pytest.raises(SchemaError, match="G0-19"):
        pin_release(_registry(d, snapshot()), "r")


def test_pin_rejects_a_knowledge_flow_when_the_release_has_no_snapshot() -> None:
    with pytest.raises(SchemaError, match="G0-17"):
        pin_release(_registry(knowledge_flow(), knowledge=None), "r")


def test_pin_fixes_the_selector_of_a_navigate_node_and_checks_its_enum() -> None:
    d = knowledge_flow()
    d["nodes"][d["nodes"].index(node(d, "saber"))] = knowledge_node(
        "saber", mode="navigate", pages=[], scope="faq", selector="selector@1")
    routes = ["faq/borrador.md", "faq/cargos.md", "faq/requisitos.md"]
    pinned = pin_release(_registry(d, snapshot(), selector(routes)), "r")
    assert pinned.release.entities[EntityKind.decision_model]["selector"] == "1.0.0"
    with pytest.raises(SchemaError, match="G0-20"):
        pin_release(_registry(d, snapshot(), selector(routes[:-1])), "r")
