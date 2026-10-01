"""Escritura `draft` (ADR 0019, m01 §3.13): G0-05, G0-23, reclamos y referencias."""

from typing import Any

from agent_core.domain import EntityKind
from agent_core.domain.schema import check_output, unsupported_keyword
from agent_core.flows import DRAFT_OUTPUT_SCHEMA, derive_claims, entity_ref_sites, validate_flow_for_agent
from tests.m01.cases import agent, agent_node, base, check, draft_base, flow, node, registry, rules


def test_draft_flow_is_valid() -> None:
    assert check(draft_base()) == []


def test_a_plain_tool_node_with_a_write_tool_is_still_g0_05() -> None:
    d = draft_base()
    node(d, "guardar")["config"] = {"tool": "escribir@1", "args": {"q": "slots.desc"}, "save_as": "res"}
    node(d, "guardar")["next"] = {"ok": "verificar", "error": "esc", "timeout": "esc", "denied": "esc"}
    assert "G0-05" in rules(check(d))


def test_draft_with_a_non_draft_write_tool_is_g0_23() -> None:
    d = draft_base()
    node(d, "guardar")["config"]["tool"] = "escribir@1"  # write_reversible: pediría confirm
    assert "G0-23" in rules(check(d))


def test_draft_with_a_read_tool_is_g0_23() -> None:
    d = draft_base()
    node(d, "guardar")["config"]["tool"] = "leer@1"
    assert "G0-23" in rules(check(d))


def test_draft_must_link_ok_and_uncertain_to_the_same_verify() -> None:
    d = draft_base()
    node(d, "guardar")["next"]["uncertain"] = "esc"
    assert "G0-23" in rules(check(d))


def test_draft_verify_must_be_by_idempotency_key() -> None:
    d = draft_base()
    node(d, "verificar")["config"]["by"] = "fact:facts.res.value.id"
    assert "G0-23" in rules(check(d))


def test_going_back_to_the_draft_from_verify_failed_is_g0_23() -> None:  # Review Focus 3
    d = draft_base()
    node(d, "verificar")["next"]["failed"] = "guardar"
    assert "G0-23" in rules(check(d))


def test_going_back_to_the_draft_from_verified_is_allowed() -> None:  # Review Focus 3
    d = draft_base()
    node(d, "ok_msg")["next"]["next"] = "elige"
    d["nodes"] += [
        {"id": "elige", "type": "decide",
         "config": {"model": "modelo@1", "branch_on": "campo", "save_as": "d"},
         "next": {"a": "otra", "b": "fin", "low_confidence": "esc"}},
        {"id": "otra", "type": "collect", "config": {"slot": "mas", "prompt_ref": "t/pedir"},
         "next": {"ok": "guardar", "max_attempts": "esc"}},
    ]
    assert check(d) == []  # si otra regla rechaza este fixture por un motivo ajeno, simplifica el fixture


def test_a_respond_claiming_a_draft_write_must_pass_through_its_verified_branch() -> None:
    d = draft_base()
    node(d, "verificar")["next"]["failed"] = "ok_msg"
    assert "G0-05" in rules(check(d))


def test_derive_claims_sees_a_draft_write() -> None:
    d = draft_base()
    node(d, "ok_msg")["config"]["claims"] = []  # sin reclamo declarado: el derivado basta
    assert derive_claims(flow(d), registry())["ok_msg"] == frozenset({"guardar"})


def test_the_draft_tool_is_a_reference_site() -> None:
    sites = entity_ref_sites(flow(draft_base()))
    assert (EntityKind.tool, "guardar@1") in {(s.kind, str(s.ref)) for s in sites if s.node_id == "guardar"}


def test_an_unresolved_draft_tool_is_g0_02() -> None:
    d = draft_base()
    node(d, "guardar")["config"]["tool"] = "nada@1"
    assert "G0-02" in rules(check(d))


def test_the_draft_schema_is_inside_the_supported_subset_and_accepts_a_draft() -> None:
    assert unsupported_keyword(DRAFT_OUTPUT_SCHEMA) is None
    good = {"changes": [{"kind": "prompt", "content": {"id": "p/x", "version": "1.0.0"},
                         "docs": {"description": "d", "rationale": "r", "changelog": "c"}}]}
    assert check_output(DRAFT_OUTPUT_SCHEMA, good) is None
    assert check_output(DRAFT_OUTPUT_SCHEMA, {"changes": [{"kind": "prompt"}]}) is not None
    assert check_output(DRAFT_OUTPUT_SCHEMA, {}) is not None


def _agent_into_draft(schema: dict[str, Any]) -> dict[str, Any]:
    d = draft_base()
    node(d, "pedir")["next"]["ok"] = "investigar"
    investigar = agent_node(output_schema=schema, save_as="hallazgo")
    investigar["next"]["answered"] = "guardar"
    d["nodes"].append(investigar)
    node(d, "guardar")["config"]["args"] = {"changes": "facts.hallazgo.value.changes"}
    return d


def test_agent_output_may_feed_a_draft_write_when_its_schema_is_the_draft_schema() -> None:
    assert check(_agent_into_draft(dict(DRAFT_OUTPUT_SCHEMA))) == []


def test_agent_output_with_another_schema_cannot_feed_a_draft_write() -> None:
    assert rules(check(_agent_into_draft({"type": "object"}))) == {"G0-22"}


def test_agent_output_still_cannot_feed_a_verify_even_with_the_draft_schema() -> None:
    d = _agent_into_draft(dict(DRAFT_OUTPUT_SCHEMA))
    node(d, "verificar")["config"]["predicate"] = {"==": [{"var": "facts.hallazgo.value.changes"}, []]}
    assert "G0-22" in rules(check(d))


def _ag02(d: dict[str, Any], **over: Any) -> list[str]:
    return [v.rule for v in validate_flow_for_agent(flow(d), agent(**over), registry())]


def test_a_builder_agent_without_subjects_may_use_write_draft() -> None:
    assert _ag02(draft_base(), invocable_by=["builder"], subject_kinds=[]) == []


def test_write_draft_is_closed_to_agents_invocable_by_others() -> None:
    assert _ag02(draft_base(), invocable_by=["builder", "customer"], subject_kinds=[]) == ["AG-02"]
    assert _ag02(draft_base(), invocable_by=["advisor"], subject_kinds=[]) == ["AG-02"]


def test_write_draft_is_closed_to_agents_that_declare_subjects() -> None:
    assert _ag02(draft_base(), invocable_by=["builder"], subject_kinds=["customer"]) == ["AG-02"]


def test_a_flow_without_write_draft_has_no_ag_02() -> None:
    assert "AG-02" not in _ag02(base())  # el agente de prueba es invocable por customer
