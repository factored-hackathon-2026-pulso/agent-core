from copy import deepcopy
from typing import Any

import pytest
from pydantic import ValidationError

from agent_core.domain import Agent
from agent_core.registry.evaluation import (
    PLATFORM_GUARDRAILS,
    EvalSuite,
    SuiteProblemCode,
    suite_problems,
)
from tests.m01.cases import AGENT
from tests.registry.support import metric, scenario, suite, thr


def make_agent(*metrics: Any) -> Agent:
    data = deepcopy(AGENT) | {"metrics": [m.model_dump(mode="json") for m in metrics]}
    return Agent.model_validate(data)


def codes(problems: list[Any]) -> list[str]:
    return [p.code.value for p in problems]


def test_valid_suite_has_no_problems() -> None:
    agent = make_agent(metric("m_gate"), metric("m_mon", role="monitor"))
    assert suite_problems(agent, suite([scenario("s1")], {"m_gate": thr("0.02")})) == []


def test_scripted_source_needs_exactly_one_input() -> None:
    both = scenario("s1")
    both["source"] = both["source"] | {"signal": {"x": 1}}
    neither = scenario("s2")
    neither["source"] = neither["source"] | {"user_turns": None}
    for bad in (both, neither):
        with pytest.raises(ValidationError):
            suite([bad])


def test_scripted_source_rejects_empty_user_turns() -> None:
    empty = scenario("s1")
    empty["source"] = empty["source"] | {"user_turns": []}
    with pytest.raises(ValidationError):
        suite([empty])


def test_assertion_filters_must_match_the_catalog() -> None:
    agent = make_agent(metric("m_gate"))
    typo = scenario("s1", assertions=[
        {"event": "engine.escalated", "expect": "none",
         "where": [{"field": "reason_cod", "op": "eq", "value": "x"}]}])
    bad_type = scenario("s2", assertions=[
        {"event": "engine.agent_step", "where": [{"field": "step", "op": "eq", "value": "uno"}]}])
    bad_op = scenario("s3", assertions=[
        {"event": "engine.escalated",
         "where": [{"field": "priority", "op": "gt", "value": 1}]}])
    good = scenario("s4", assertions=[
        {"event": "engine.agent_step", "where": [{"field": "step", "op": "ge", "value": 2}]}])
    problems = suite_problems(agent, suite([typo, bad_type, bad_op, good], {"m_gate": thr()}))
    assert [(p.code.value, p.path) for p in problems] == [
        ("invalid_assertion_filter", "/scenarios/0/assertions/0/where/0/field"),
        ("invalid_assertion_filter", "/scenarios/1/assertions/0/where/0/value"),
        ("invalid_assertion_filter", "/scenarios/2/assertions/0/where/0/field"),
    ]


def test_suite_needs_scenarios_and_positive_repetitions() -> None:
    with pytest.raises(ValidationError):
        suite([])
    with pytest.raises(ValidationError):
        suite([scenario("s1", repetitions=0)])


def test_thresholds_are_finite_decimals() -> None:
    with pytest.raises(ValidationError):
        suite([scenario("s1")], {"m": thr("NaN")})
    with pytest.raises(ValidationError):
        suite([scenario("s1")], {"m": thr("-0.1")})


# T-EVAL-11
def test_agent_without_suite_is_not_publishable() -> None:
    assert codes(suite_problems(make_agent(metric("m_gate")), None)) == ["missing_suite"]


# Review Focus 3: sin suite no se pueden medir los guardarraíles de plataforma, aunque solo haya monitor
def test_agent_with_only_monitor_metrics_or_none_still_needs_a_suite() -> None:
    assert codes(suite_problems(make_agent(metric("m_mon", role="monitor")), None)) == ["missing_suite"]
    assert codes(suite_problems(make_agent(), None)) == ["missing_suite"]


def test_gate_and_guardrail_metrics_need_thresholds() -> None:
    agent = make_agent(metric("m_gate"), metric("m_guard", role="guardrail"), metric("m_mon", role="monitor"))
    problems = suite_problems(agent, suite([scenario("s1")], {"m_gate": thr()}))
    assert [(p.code.value, p.path) for p in problems] == [("missing_threshold", "/thresholds/m_guard")]


# Review Focus 4
def test_thresholds_for_unknown_metrics_and_duplicate_scenarios_are_rejected() -> None:
    agent = make_agent(metric("m_gate"))
    problems = suite_problems(
        agent, suite([scenario("s1"), scenario("s1")], {"m_gate": thr(), "ghost": thr()})
    )
    assert sorted(codes(problems)) == ["duplicate_scenario", "unknown_threshold_metric"]


def test_assertion_events_must_be_in_the_catalog() -> None:
    agent = make_agent(metric("m_gate"))
    bad = scenario("s1", assertions=[{"event": "engine.nope"}])
    good = scenario("s2", assertions=[{"event": "engine.escalated",
                                       "where": [{"field": "reason_code", "op": "eq", "value": "x"}]}])
    problems = suite_problems(agent, suite([bad, good], {"m_gate": thr()}))
    assert [(p.code.value, p.path) for p in problems] == [
        ("unknown_assertion_event", "/scenarios/0/assertions/0/event")
    ]


# T-EVAL-12
def test_dataset_source_is_disabled() -> None:
    agent = make_agent(metric("m_gate"))
    dataset = scenario("s1")
    dataset["source"] = {"kind": "dataset", "dataset_id": "casos_reales", "dataset_hash": "a" * 64}
    problems = suite_problems(agent, suite([dataset], {"m_gate": thr()}))
    assert [(p.code, p.path) for p in problems] == [
        (SuiteProblemCode.dataset_source_disabled, "/scenarios/0/source")
    ]


def test_suite_must_belong_to_the_agent() -> None:
    agent = make_agent(metric("m_gate"))
    other = suite([scenario("s1")], {"m_gate": thr()}, agent_id="otro")
    assert codes(suite_problems(agent, other)) == ["agent_mismatch"]


def test_problems_are_sorted_and_deterministic() -> None:
    agent = make_agent(metric("m_gate"), metric("m_guard", role="guardrail"))
    s = suite([scenario("s1"), scenario("s1")], {"ghost": thr()})
    first, second = suite_problems(agent, s), suite_problems(agent, s)
    assert first == second
    assert first == sorted(first, key=lambda p: (p.path, p.code.value))


def test_platform_guardrails_are_reserved_names() -> None:
    assert len(PLATFORM_GUARDRAILS) == len(set(PLATFORM_GUARDRAILS)) == 4
    assert all(name.startswith("platform_") for name in PLATFORM_GUARDRAILS)
    assert "platform_pii_leak" in PLATFORM_GUARDRAILS
    assert isinstance(EvalSuite, type)
