"""Escritura `draft` (ADR 0019, m01 §3.13): G0-05, G0-23, reclamos y referencias."""

from agent_core.domain import EntityKind
from agent_core.flows import derive_claims, entity_ref_sites
from tests.m01.cases import check, draft_base, flow, node, registry, rules


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
