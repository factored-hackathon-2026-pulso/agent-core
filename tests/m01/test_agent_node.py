"""Nodo `agent` habilitado (ADR 0019, m01 §3.13): G0-01 ya no lo rechaza, sus referencias se resuelven y se
fijan, y G0-07, G0-06 y G0-22 acotan lo que puede leer y adónde puede llegar su salida."""

from typing import Any

import pytest

from agent_core.domain import EntityKind, Flow, Template
from agent_core.flows.pin import pin_release
from agent_core.flows.refs import entity_ref_sites
from agent_core.flows.registry import AuthoringRegistry
from tests.m01.cases import ENTITIES, agent, base, check, flow, node, registry, rules, with_agent
from tests.m01.test_pin import LANG, _decl

READS_HALLAZGO = Template.model_validate(
    {"id": "t/lee_hallazgo", "version": "1.0.0",
     "locales": {"es": "Hallazgo: {{ facts.hallazgo.value.id }}",
                 "pt": "Achado: {{ facts.hallazgo.value.id }}"}}
)


def test_agent_node_is_valid() -> None:
    assert check(with_agent()) == []


def test_agent_refs_are_reference_sites() -> None:
    sites = entity_ref_sites(flow(with_agent()))
    kinds = {(s.kind, str(s.ref)) for s in sites if s.node_id == "investigar"}
    assert kinds == {(EntityKind.tool, "leer@1"), (EntityKind.tool, "calc@1"), (EntityKind.prompt, "p/gen")}


def test_agent_unresolved_refs_are_g0_02() -> None:
    d = with_agent(tools_allowed=["nada@1"], prompt_ref="p/nada")
    assert [v.rule for v in check(d)].count("G0-02") == 2


def test_agent_with_a_write_tool_is_g0_07() -> None:
    found = check(with_agent(tools_allowed=["escribir@1"]))
    assert rules(found) == {"G0-07"}


def test_agent_prompt_without_model_profile_is_g0_15() -> None:
    assert rules(check(with_agent(prompt_ref="p/sinperfil"))) == {"G0-15"}


def test_agent_gave_up_must_reach_a_safe_exit_g0_06() -> None:
    d = with_agent()
    node(d, "investigar")["next"]["gave_up"] = "ok_msg"  # un respond que reclama una acción sin verificar
    assert "G0-06" in rules(check(d))


# --- G0-22: lo que el modelo genera no alimenta decisiones ni escrituras ---------------------------


def _reads(site: str) -> dict[str, Any]:
    d = with_agent()
    if site == "rule":
        node(d, "buscar")["next"]["ok"] = "r"
        d["nodes"].append({"id": "r", "type": "rule",
                           "config": {"expr": {"==": [{"var": "facts.hallazgo.value.ok"}, True]}},
                           "next": {"true": "confirmar", "false": "confirmar"}})
    elif site == "tool_args":
        node(d, "buscar")["config"]["args"] = {"q": "facts.hallazgo.value.q"}
    elif site == "confirm_args":
        node(d, "confirmar")["config"]["action"]["args"] = {"q": "facts.hallazgo.value.q"}
    elif site == "verify":
        node(d, "verificar")["config"]["predicate"] = {"==": [{"var": "facts.hallazgo.value.ok"}, True]}
    elif site == "escalate":
        node(d, "esc")["config"]["priority_expr"] = {"var": "facts.hallazgo.value.p"}
    elif site == "end":
        node(d, "fin")["config"]["output_map"] = {"h": "facts.hallazgo.value"}
    return d


@pytest.mark.parametrize("site", ["rule", "tool_args", "confirm_args", "verify", "escalate", "end"])
def test_g0_22_agent_output_cannot_feed_decisions_or_writes(site: str) -> None:
    found = [v for v in check(_reads(site), registry()) if v.rule == "G0-22"]
    assert len(found) == 1, site
    assert "hallazgo" in found[0].message


def test_g0_22_respond_template_may_read_the_agent_output() -> None:
    d = with_agent()
    node(d, "ok_msg")["config"]["template_ref"] = "t/lee_hallazgo"
    assert "G0-22" not in rules(check(d, registry(READS_HALLAZGO)))


def test_g0_22_respond_generate_may_list_the_agent_output() -> None:
    d = with_agent()
    node(d, "ok_msg")["config"] = {
        "generate": {"prompt_ref": "p/gen", "allowed_facts": ["facts.hallazgo"],
                     "fallback_template_ref": "t/hecho"},
        "claims": ["confirmar"]}
    assert "G0-22" not in rules(check(d))


def test_g0_22_decide_input_view_may_read_the_agent_output() -> None:
    d = with_agent()
    node(d, "buscar")["next"]["ok"] = "elige"
    d["nodes"].append({"id": "elige", "type": "decide",
                       "config": {"model": "modelo@1", "branch_on": "campo", "save_as": "d",
                                  "input_view": ["facts.hallazgo.value.texto"]},
                       "next": {"a": "confirmar", "b": "confirmar", "low_confidence": "esc"}})
    assert "G0-22" not in rules(check(d))


def test_g0_22_ignores_other_facts_and_flows_without_agent() -> None:
    assert "G0-22" not in rules(check(base()))
    d = with_agent(save_as="otro")
    assert "G0-22" not in rules(check(d))  # base() lee facts.datos y facts.verif, no facts.otro


# --- pin: las referencias del agent quedan exactas ---------------------------------------------------------


def test_pin_fixes_the_agent_node_references() -> None:
    d = with_agent(tools_allowed=["leer@1", "calc@1"], prompt_ref="p/gen@1")
    reg = AuthoringRegistry.from_entities([*ENTITIES, LANG, Flow.model_validate(d), agent()], [_decl()])
    pinned = pin_release(reg, "r")
    pinned_flow = next(e for e in pinned.entities if isinstance(e, Flow))
    config = next(n for n in pinned_flow.nodes if n.id == "investigar").config
    assert [str(r) for r in config.tools_allowed] == ["leer@1.0.0", "calc@1.0.0"]  # type: ignore[union-attr]
    assert str(config.prompt_ref) == "p/gen@1.0.0"  # type: ignore[union-attr]


def test_g0_24_agent_tool_without_documentation() -> None:
    assert rules(check(with_agent(tools_allowed=["sindoc@1"]))) == {"G0-24"}


def test_g0_24_agent_tool_with_args_schema_outside_the_subset() -> None:
    found = check(with_agent(tools_allowed=["malschema@1"]))
    assert rules(found) == {"G0-24"}
    assert "oneOf" in found[0].message


def test_g0_24_does_not_apply_to_tools_outside_agent_nodes() -> None:
    assert "G0-24" not in rules(check(base()))  # `leer@1` sin documentar sería válida fuera de un nodo agent
