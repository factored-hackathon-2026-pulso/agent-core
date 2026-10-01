"""Static rules for transfer flows (ADR 0021, spec §6): G0-03, G0-10, G0-11, G0-26, G0-27 and AG-03."""

from copy import deepcopy
from typing import Any

from agent_core.domain import Agent, DecisionModelDef, Flow, ToolDef
from agent_core.flows.agent import validate_agent, validate_flow_for_agent
from tests.m01.cases import AGENT, check, node, registry, rules

DIRECTORY_TOOL = ToolDef.model_validate({
    "id": "directory/list", "version": "1.0.0", "risk_class": "read", "min_auth_level": "session",
    "idempotent": True, "description": "Lists specialists", "args_schema": {"type": "object"}})
ROUTER = DecisionModelDef.model_validate({
    "id": "router", "version": "1.0.0",
    "output_schema": {"type": "object", "properties": {"choice": {"type": "string"}}},
    "calibrated_fields": ["choice"], "providers": [{"provider": "llm_structured"}],
    "calibration": {"method": "none"}})


def reception() -> dict[str, Any]:
    return deepcopy({"id": "reception", "version": "1.0.0", "priority": 10, "nodes": [
        {"id": "ask", "type": "collect", "config": {"slot": "problem", "prompt_ref": "t/pedir"},
         "next": {"ok": "directory", "max_attempts": "esc"}},
        {"id": "directory", "type": "tool",
         "config": {"tool": "directory/list@1", "args": {"directory": "customer-care"},
                    "save_as": "directory"},
         "next": {"ok": "route", "error": "esc", "timeout": "esc", "denied": "esc"}},
        {"id": "route", "type": "decide",
         "config": {"model": "router@1", "branch_on": "choice", "save_as": "route",
                    "choices_from": "facts.directory.value.choices", "input_view": ["slots.problem"]},
         "next": {"chosen": "transfer", "none": "esc", "low_confidence": "esc"}},
        {"id": "transfer", "type": "transfer",
         "config": {"target_from": "decisions.route.choice", "directory_from": "directory",
                    "packet": {"reason": "routed", "slots": ["problem"]}},
         "next": {"rejected": "esc"}},
        {"id": "esc", "type": "escalate", "config": {"reason_code": "policy:transfer-rejected"}},
    ]})


def reg() -> Any:
    return registry(DIRECTORY_TOOL, ROUTER)


def test_reception_flow_is_valid() -> None:
    assert check(reception(), reg()) == []


def test_choices_decide_needs_its_three_results() -> None:
    d = reception()
    del node(d, "route")["next"]["none"]
    assert rules(check(d, reg())) == {"G0-03"}


def test_choices_decide_branches_on_choice() -> None:
    d = reception()
    node(d, "route")["config"]["branch_on"] = "other"
    assert "G0-03" in rules(check(d, reg()))


def test_choices_from_reads_only_facts() -> None:
    d = reception()
    node(d, "route")["config"]["choices_from"] = "slots.problem"
    assert "G0-10" in rules(check(d, reg()))


def test_g0_26_target_must_come_from_a_dominating_choices_decide() -> None:
    d = reception()
    node(d, "transfer")["config"]["target_from"] = "decisions.other.choice"
    assert "G0-26" in rules(check(d, reg()))


def test_g0_26_directory_must_come_from_directory_list() -> None:
    d = reception()
    node(d, "transfer")["config"]["directory_from"] = "problem"
    assert "G0-26" in rules(check(d, reg()))


def test_g0_26_a_path_around_the_decide_is_a_violation() -> None:
    d = reception()
    node(d, "directory")["next"]["ok"] = "transfer"  # skips `route`
    assert rules(check(d, reg())) == {"G0-03", "G0-26"}  # `route` also becomes unreachable


def test_g0_27_packet_slots_must_be_collected() -> None:
    d = reception()
    node(d, "transfer")["config"]["packet"]["slots"] = ["problem", "card"]
    assert rules(check(d, reg())) == {"G0-27"}


def test_ag_03_transfer_needs_a_conversational_agent() -> None:
    flow = Flow.model_validate(reception())
    task = Agent.model_validate(AGENT | {"mode": "task", "entry_flow": "reception@1"})
    assert "AG-03" in {v.rule for v in validate_flow_for_agent(flow, task, reg())}


def test_ag_03_an_agent_with_accepts_needs_routing_and_understand() -> None:
    agent = Agent.model_validate(AGENT | {"accepts": {"slots": {}}})
    assert "AG-03" in {v.rule for v in validate_agent(agent, reg())}


def test_g0_26_none_or_low_confidence_to_transfer_is_a_violation() -> None:
    # `low_confidence` is a failure branch, so G0-06 also objects (transfer is not a safe exit).
    for result, expected in (("none", {"G0-26"}), ("low_confidence", {"G0-06", "G0-26"})):
        d = reception()
        node(d, "route")["next"][result] = "transfer"
        assert rules(check(d, reg())) == expected


def test_g0_11_choice_must_be_calibrated() -> None:
    uncalibrated = DecisionModelDef.model_validate(ROUTER.model_dump(mode="json") | {"calibrated_fields": []})
    assert rules(check(reception(), registry(DIRECTORY_TOOL, uncalibrated))) == {"G0-11"}


def test_g0_01_malformed_choices_from() -> None:
    d = reception()
    node(d, "route")["config"]["choices_from"] = "facts..bad"
    assert "G0-01" in rules(check(d, reg()))
    node(d, "route")["config"]["choices_from"] = "literal"
    assert "G0-01" in rules(check(d, reg()))


def test_g0_10_choices_from_needs_value() -> None:
    d = reception()
    node(d, "route")["config"]["choices_from"] = "facts.directory"
    assert rules(check(d, reg())) == {"G0-10"}
