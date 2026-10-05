"""Nodo `suggest` (ADR 0026, m01 §3.13): referencias, lecturas, tools de lectura, G0-28 y salidas seguras."""

from copy import deepcopy
from typing import Any

import pytest

from agent_core.domain import EntityKind, ModelProfile
from agent_core.flows.refs import entity_ref_sites
from tests.m01.cases import check, flow, node, registry, rules, task_base

NATIVE = ModelProfile.model_validate(
    {
        "id": "nativo",
        "version": "1.0.0",
        "endpoint_alias": "demo",
        "model": "modelo-sintetico",
        "temperature": "0",
        "max_tokens": 400,
        "structured": "native",
        "price": {
            "input_per_mtok": "1",
            "output_per_mtok": "2",
            "source": "sintético",
            "as_of": "2026-09-28",
        },
    }
)


def suggest_node(node_id: str = "sug", **config: Any) -> dict[str, Any]:
    cfg: dict[str, Any] = {
        "prompt_ref": "p/gen",
        "goal": "g",
        "reads": ["slots.q"],
        "tools_allowed": ["leer@1"],
    } | config
    return {
        "id": node_id,
        "type": "suggest",
        "config": cfg,
        "next": {"suggested": "fin_ok", "gave_up": "fin_fallo"},
    }


def suggest_flow(**config: Any) -> dict[str, Any]:
    """Flow task válido: rule → (true) suggest con escalate | (false) suggest sin escalate."""
    d = deepcopy(task_base())
    d["nodes"] = [
        {
            "id": "regla",
            "type": "rule",
            "config": {"policy": "pol@1"},
            "next": {"true": "sug_esc", "false": "sug"},
        },
        suggest_node("sug_esc", escalate={"reason_code": "rule:pol", "evidence_from": ["slots.q"]}, **config),
        suggest_node("sug", **config),
        {"id": "fin_ok", "type": "end", "config": {"outcome": "completed"}},
        {"id": "fin_fallo", "type": "end", "config": {"outcome": "failed"}},
    ]
    return d


def test_a_well_formed_suggest_flow_is_valid() -> None:
    assert check(suggest_flow()) == []


def test_suggest_refs_are_reference_sites() -> None:
    sites = entity_ref_sites(flow(suggest_flow(actions_allowed=["escribir@1"])))
    got = {(s.kind, str(s.ref)) for s in sites if s.node_id == "sug"}
    assert got == {(EntityKind.tool, "leer@1"), (EntityKind.tool, "escribir@1"), (EntityKind.prompt, "p/gen")}


def test_unresolved_refs_are_g0_02() -> None:
    found = check(suggest_flow(tools_allowed=["nada@1"], prompt_ref="p/nada"))
    assert {v.rule for v in found} == {"G0-02"}


def test_a_write_tool_in_tools_allowed_is_g0_07() -> None:
    assert rules(check(suggest_flow(tools_allowed=["escribir@1"]))) == {"G0-07"}


def test_an_undocumented_tool_is_g0_24() -> None:
    assert rules(check(suggest_flow(tools_allowed=["sindoc@1"]))) == {"G0-24"}


def test_a_prompt_without_model_profile_is_g0_15() -> None:
    assert rules(check(suggest_flow(prompt_ref="p/sinperfil"))) == {"G0-15"}


def test_a_native_profile_prompt_is_g0_25() -> None:
    from agent_core.domain import Prompt

    prompt = Prompt.model_validate(
        {"id": "p/nativo", "version": "1.0.0", "locales": {"es": "x", "pt": "x"}, "model_profile": "nativo@1"}
    )
    assert rules(check(suggest_flow(prompt_ref="p/nativo"), registry(NATIVE, prompt))) == {"G0-25"}


def test_suggest_only_in_task_flows_g0_28() -> None:
    d = suggest_flow()
    node(d, "fin_ok")["config"]["outcome"] = "resolved"
    node(d, "fin_fallo")["config"]["outcome"] = "abstained"
    assert "G0-28" in rules(check(d))


def test_escalate_needs_a_rule_true_branch_g0_28() -> None:
    d = suggest_flow()
    node(d, "regla")["next"] = {"true": "sug", "false": "sug_esc"}  # the escalation hangs from `false`
    found = [v for v in check(d) if v.rule == "G0-28"]
    assert len(found) == 1 and found[0].node_id == "sug_esc"


def test_escalate_reached_without_a_rule_is_g0_28() -> None:
    d = suggest_flow()
    d["nodes"].insert(
        0,
        {
            "id": "buscar",
            "type": "tool",
            "config": {"tool": "leer@1", "args": {}, "save_as": "d"},
            "next": {"ok": "sug_esc", "error": "fin_fallo", "timeout": "fin_fallo", "denied": "fin_fallo"},
        },
    )
    assert "G0-28" in rules(check(d))


def test_a_flow_with_suggest_does_not_write_g0_28() -> None:
    d = suggest_flow()
    d["nodes"].append(
        {
            "id": "conf",
            "type": "confirm",
            "config": {"action": {"tool": "escribir@1", "args": {}}, "summary_template": "t/resumen"},
            "next": {"yes": "fin_ok", "no": "fin_ok", "unclear": "conf", "max_attempts": "fin_fallo"},
        }
    )
    assert "G0-28" in rules(check(d))


def test_actions_allowed_must_be_write_tools_apart_from_tools_allowed_g0_28() -> None:
    assert "G0-28" in rules(check(suggest_flow(actions_allowed=["leer@1"])))  # a read is not an action
    assert "G0-28" in rules(check(suggest_flow(actions_allowed=["escribir@1"], tools_allowed=["escribir@1"])))
    assert "G0-28" not in rules(check(suggest_flow(actions_allowed=["escribir@1"])))


def test_reads_must_be_slots_or_facts_g0_10() -> None:
    assert rules(check(suggest_flow(reads=["decisions.d.campo"]))) == {"G0-10"}


@pytest.mark.parametrize("entry", ["texto libre", "facts.Mal"])
def test_reads_must_be_paths_g0_01(entry: str) -> None:
    assert "G0-01" in rules(check(suggest_flow(reads=[entry])))
    assert "G0-01" in rules(check(suggest_flow(optional_reads=[entry])))


def test_optional_reads_follow_the_same_rules_as_reads() -> None:
    assert check(suggest_flow(optional_reads=["slots.motivo", "facts.previo.value.x"])) == []
    assert rules(check(suggest_flow(optional_reads=["decisions.d.campo"]))) == {"G0-10"}


def test_suggested_must_go_straight_to_an_end_g0_28() -> None:
    d = suggest_flow()
    d["nodes"].insert(
        1,
        {
            "id": "paso",
            "type": "tool",
            "config": {"tool": "leer@1", "args": {}, "save_as": "x"},
            "next": {"ok": "fin_ok", "error": "fin_fallo", "timeout": "fin_fallo", "denied": "fin_fallo"},
        },
    )
    node(d, "sug")["next"]["suggested"] = "paso"
    assert "G0-28" in rules(check(d))


def test_a_waiting_node_after_suggest_is_still_rejected_in_a_task_flow_g0_16() -> None:
    d = suggest_flow()
    d["nodes"].insert(
        1,
        {
            "id": "espera",
            "type": "collect",
            "config": {"slot": "x", "prompt_ref": "t/pedir"},
            "next": {"ok": "fin_ok", "max_attempts": "fin_fallo"},
        },
    )
    node(d, "sug")["next"]["suggested"] = "espera"
    assert {"G0-16", "G0-28"} <= rules(check(d))


def test_gave_up_must_reach_a_safe_exit_g0_06() -> None:
    d = suggest_flow()
    node(d, "sug")["next"]["gave_up"] = "fin_ok"  # `completed` is not a safe exit for a failure
    assert "G0-06" in rules(check(d))


def test_every_result_must_be_wired_g0_03() -> None:
    d = suggest_flow()
    del node(d, "sug")["next"]["gave_up"]
    assert "G0-03" in rules(check(d))


def _with_agent_fact(d: dict[str, Any], *, reads: list[str], evidence: list[str]) -> dict[str, Any]:
    """`agent` produces facts.hallazgo before the rule; `suggest` reads/evidences it."""
    d["nodes"].insert(
        0,
        {
            "id": "inv",
            "type": "agent",
            "config": {
                "tools_allowed": ["leer@1"],
                "max_steps": 2,
                "prompt_ref": "p/gen",
                "goal": "x",
                "save_as": "hallazgo",
                "output_schema": {"type": "object"},
            },
            "next": {"answered": "regla", "gave_up": "fin_fallo"},
        },
    )
    node(d, "sug")["config"]["reads"] = reads
    node(d, "sug_esc")["config"]["reads"] = reads
    node(d, "sug_esc")["config"]["escalate"]["evidence_from"] = evidence
    return d


def test_suggest_may_feed_a_model_from_an_agent_output() -> None:
    d = _with_agent_fact(suggest_flow(), reads=["facts.hallazgo.value.texto"], evidence=["slots.q"])
    assert "G0-22" not in rules(check(d))


def test_escalate_evidence_cannot_come_from_an_agent_output_g0_22() -> None:
    """The evidence reaches the output as it is: it must not carry what a model generated."""
    d = _with_agent_fact(suggest_flow(), reads=["slots.q"], evidence=["facts.hallazgo.value.texto"])
    assert [v.rule for v in check(d) if v.rule == "G0-22"] == ["G0-22"]


def test_a_flow_with_suggest_does_not_escalate_g0_28() -> None:
    d = suggest_flow()
    d["nodes"].append({"id": "esc", "type": "escalate", "config": {"reason_code": "tool_failure"}})
    node(d, "sug")["next"]["gave_up"] = "esc"
    assert "G0-28" in rules(check(d))


def test_a_read_in_actions_allowed_is_g0_28_with_its_own_message() -> None:
    found = [v for v in check(suggest_flow(actions_allowed=["leer@1"])) if v.rule == "G0-28"]
    assert any("lectura" in v.message for v in found)
