"""T-M12-04: un flow con G0-17…G0-21 violadas no se publica (un fixture por regla), y `derive_claims` ignora
las páginas (T-M12-05). Cada fixture es una mutación mínima de `knowledge_flow()` que da solo esa regla."""

from typing import Any

from agent_core.domain import EntityKind, Flow
from agent_core.flows import derive_claims, entity_ref_sites, validate_flow_for_release, validate_registry
from agent_core.flows.registry import AuthoringRegistry, ReleaseDecl
from tests.m01.cases import ENTITIES, agent, flow, node
from tests.m01.cases import registry as base_registry
from tests.m12.cases import (
    check,
    explica,
    knowledge_flow,
    knowledge_node,
    page,
    registry,
    rules,
    selector,
    snapshot,
)


def release_check(d: dict[str, Any], snap: Any = None, *extra: Any) -> list[Any]:
    return validate_flow_for_release(flow(d), snap if snap is not None else snapshot(), registry(*extra))


# --- línea base ---------------------------------------------------------------------------------------------


def test_the_valid_knowledge_flow_passes_every_rule() -> None:
    d = knowledge_flow()
    assert check(d) == []
    assert release_check(d) == []


def test_knowledge_refs_is_gone_from_the_schema() -> None:
    d = knowledge_flow()
    node(d, "explica")["config"]["generate"]["knowledge_refs"] = ["x"]
    assert rules(check_safely(d)) == {"G0-01"}


def check_safely(d: dict[str, Any]) -> list[Any]:
    from agent_core.flows import FlowSchemaError, parse_flow

    try:
        parse_flow(d)
    except FlowSchemaError as error:
        return error.violations
    return []


# --- T-M12-04 · un fixture por regla --------------------------------------------------------------------------


def test_g0_17_a_page_that_is_not_in_the_snapshot() -> None:
    d = knowledge_flow()
    node(d, "saber")["config"]["pages"] = ["faq/no-existe.md#x"]
    found = release_check(d)
    assert rules(found) == {"G0-17"}
    assert found[0].node_id == "saber" and "faq/no-existe.md" in found[0].message
    assert found[0].path == "/nodes/%d/config/pages/0" % [n["id"] for n in d["nodes"]].index("saber")
    assert check(d) == []  # es una regla de la release: un flow aislado no conoce el snapshot


def test_g0_17_a_release_without_a_snapshot_cannot_publish_a_flow_with_fixed_pages() -> None:
    found = validate_flow_for_release(flow(knowledge_flow()), None, registry())
    assert rules(found) == {"G0-17"}


def test_g0_17_does_not_apply_to_flows_without_knowledge_nodes() -> None:
    from tests.m01.cases import base

    assert validate_flow_for_release(flow(base()), None, registry()) == []


def test_g0_18_a_customer_answer_respond_reads_only_customer_answer_nodes() -> None:
    d = knowledge_flow()
    node(d, "saber")["config"]["purpose"] = "advisor_view"
    assert rules(check(d)) == {"G0-18"}
    node(d, "saber")["config"]["purpose"] = "agent_guidance"
    assert rules(check(d)) == {"G0-18"}


def test_g0_18_does_not_bind_a_respond_that_is_not_customer_answer() -> None:
    d = knowledge_flow()
    node(d, "saber")["config"]["purpose"] = "advisor_view"
    node(d, "explica")["config"]["generate"]["purpose"] = "advisor_view"
    assert check(d) == []


def test_g0_19_fixed_pages_of_a_customer_answer_node_are_public_and_approved() -> None:
    for bad in ("proc/reversos.md", "faq/borrador.md"):
        d = knowledge_flow()
        node(d, "saber")["config"]["pages"] = [bad]
        found = release_check(d)
        assert rules(found) == {"G0-19"}, bad
        assert found[0].node_id == "saber"


def test_g0_19_agent_only_and_a_translation_are_judged_by_their_own_page() -> None:
    d = knowledge_flow()
    node(d, "saber")["config"]["pages"] = ["guia/agente.md"]
    snap = snapshot(page("guia/agente.md", audience="agent_only"))
    assert rules(release_check(d, snap)) == {"G0-19"}


def test_g0_19_does_not_apply_to_other_purposes() -> None:
    d = knowledge_flow()
    node(d, "saber")["config"].update({"pages": ["proc/reversos.md"], "purpose": "advisor_view"})
    node(d, "explica")["config"]["generate"]["purpose"] = "advisor_view"
    assert release_check(d) == []


def _navigating() -> dict[str, Any]:
    """`knowledge_flow()` con `saber` convertido en un nodo `navigate` sobre el scope `faq`."""
    d = knowledge_flow()
    d["nodes"][d["nodes"].index(node(d, "saber"))] = knowledge_node(
        "saber", mode="navigate", pages=[], scope="faq", selector="selector@1")
    return d


def test_g0_20_navigate_selector_enum_is_exactly_the_routes_of_the_scope() -> None:
    d = _navigating()
    good = ["faq/borrador.md", "faq/cargos.md", "faq/requisitos.md"]
    assert release_check(d, None, selector(good)) == []
    for bad in (good[:-1], [*good, "faq/fantasma.md"], [*good, "proc/reversos.md"], []):
        found = release_check(d, None, selector(bad))
        assert rules(found) == {"G0-20"}, bad
        assert found[0].node_id == "saber"
    assert rules(release_check(d, None, selector(good, prop="otra"))) == {"G0-20"}


def test_g0_20_the_index_of_the_scope_is_not_a_route() -> None:
    d = _navigating()
    snap = snapshot(page("faq/index.md"))
    routes = ["faq/borrador.md", "faq/cargos.md", "faq/requisitos.md"]
    assert release_check(d, snap, selector(routes)) == []


def test_g0_21_a_respond_that_can_be_reached_without_going_through_the_knowledge_node() -> None:
    d = knowledge_flow()
    node(d, "pedir")["next"]["max_attempts"] = "explica"  # rodea a `saber`
    found = check(d)
    assert rules(found) == {"G0-21"}
    assert found[0].node_id == "explica"


def test_g0_21_knowledge_from_must_name_a_knowledge_node() -> None:
    d = knowledge_flow()
    node(d, "explica")["config"]["generate"]["knowledge_from"] = ["kb", "nadie"]
    found = check(d)
    assert rules(found) == {"G0-21"} and "nadie" in found[0].message


def test_g0_21_a_node_that_comes_after_the_respond_does_not_dominate_it() -> None:
    d = knowledge_flow()
    node(d, "pedir")["next"]["ok"] = "buscar"
    node(d, "ok_msg")["next"]["next"] = "explica"
    node(d, "explica")["next"]["next"] = "saber"  # `saber` queda después del respond
    node(d, "saber")["next"] = {"ok": "fin", "not_found": "fin", "denied": "fin"}
    assert rules(check(d)) == {"G0-21"}


def test_g0_21_a_knowledge_node_whose_not_found_edge_skips_the_respond_still_dominates() -> None:
    d = knowledge_flow()
    assert node(d, "saber")["next"]["not_found"] == "esc"
    assert check(d) == []


# --- forma del nodo dentro de M1 -------------------------------------------------------------------------------


def test_a_read_node_wires_exactly_ok_not_found_and_denied() -> None:
    d = knowledge_flow()
    del node(d, "saber")["next"]["denied"]
    assert "G0-03" in rules(check(d))
    d = knowledge_flow()
    node(d, "saber")["next"]["low_confidence"] = "esc"
    assert "G0-03" in rules(check(d))


def test_a_navigate_node_also_wires_low_confidence() -> None:
    d = _navigating()
    assert check(d, selector(["faq/cargos.md"])) == []
    del node(d, "saber")["next"]["low_confidence"]
    assert "G0-03" in rules(check(d, selector(["faq/cargos.md"])))


def test_the_selector_of_a_navigate_node_is_a_decision_model_reference_checked_by_g0_02() -> None:
    d = _navigating()
    node(d, "saber")["config"]["selector"] = "fantasma@1"
    assert "G0-02" in rules(check(d))
    sites = [s for s in entity_ref_sites(flow(d)) if s.node_id == "saber"]
    assert [(s.kind, str(s.ref)) for s in sites] == [(EntityKind.decision_model, "fantasma@1")]


def test_a_read_node_has_no_reference_sites() -> None:
    assert [s for s in entity_ref_sites(flow(knowledge_flow())) if s.node_id == "saber"] == []


def test_knowledge_node_is_not_terminal_and_does_not_wait() -> None:
    from agent_core.flows.graph import is_waiting

    assert not is_waiting(flow(knowledge_flow()).nodes[-2]) and isinstance(flow(knowledge_flow()), Flow)


# --- el gate de la release -------------------------------------------------------------------------------------


def _decl(**over: Any) -> ReleaseDecl:
    data: dict[str, Any] = {"id": "r", "agents": [{"agent": "atencion@1"}], "flows": ["base@1.0.0"],
                            "language_detection": "lang@1", "knowledge": "kb-base@1"}
    return ReleaseDecl.model_validate(data | over)


def _release_registry(d: dict[str, Any], snap: Any = None, **decl: Any) -> AuthoringRegistry:
    from agent_core.domain import LanguageDetection

    lang = LanguageDetection.model_validate(
        {"id": "lang", "version": "1.0.0", "detector": "lingua@1.4.0", "candidates": ["es", "pt"],
         "min_letters": 12, "min_letters_unsupported": 20})
    entities = [*ENTITIES, flow(d), agent(), lang, *([snap] if snap is not None else [])]
    return AuthoringRegistry.from_entities(entities, [_decl(**decl)])


def test_validate_registry_applies_the_release_rules_with_the_snapshot_of_each_release() -> None:
    d = knowledge_flow()
    d["id"] = "base"
    assert validate_registry(_release_registry(d, snapshot())) == []
    node(d, "saber")["config"]["pages"] = ["faq/no-existe.md"]
    found = validate_registry(_release_registry(d, snapshot()))
    assert [(v.rule, v.node_id) for v in found] == [("G0-17", "saber")]


def test_validate_registry_flags_a_release_with_knowledge_nodes_but_no_snapshot() -> None:
    d = knowledge_flow()
    d["id"] = "base"
    reg = _release_registry(d, None, knowledge=None)
    assert {v.rule for v in validate_registry(reg)} == {"G0-17"}


# --- T-M12-05 · derive_claims ignora las páginas -----------------------------------------------------------------


def test_t_m12_05_derive_claims_ignores_pages() -> None:
    plain = knowledge_flow()
    with_nodes = dict(derive_claims(flow(plain), base_registry()))
    assert with_nodes["explica"] == frozenset()  # una página describe procedimientos, no el resultado de una acción
    # la respuesta de éxito conserva sus reclamos y la de conocimiento no hereda ninguno
    assert with_nodes["ok_msg"] == frozenset({"confirmar"})


def test_t_m12_05_a_page_name_that_collides_with_a_contaminated_fact_does_not_claim() -> None:
    d = knowledge_flow()
    # `res` es el `save_as` de la escritura: como hecho está contaminado; como `save_as` de páginas no cuenta
    node(d, "saber")["config"]["save_as"] = "res"
    node(d, "explica")["config"]["generate"]["knowledge_from"] = ["res"]
    claims = dict(derive_claims(flow(d), base_registry()))
    assert claims["explica"] == frozenset()
    assert check(d) == []  # y G0-05 no ve un lector de una acción sin verificar


def test_t_m12_05_knowledge_from_is_not_a_fact_read() -> None:
    d = knowledge_flow()
    node(d, "pedir")["next"]["max_attempts"] = "saber"
    claims = dict(derive_claims(flow(d), base_registry()))
    assert claims == dict(derive_claims(flow(knowledge_flow()), base_registry()))


def test_explica_helper_has_the_default_purpose() -> None:
    assert explica()["config"]["generate"]["purpose"] == "customer_answer"
