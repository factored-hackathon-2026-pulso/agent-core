"""Unified `eval_suite` (registry §6.1, evaluation spec §5): format and `suite_problems`."""

from copy import deepcopy
from typing import Any

import pytest
from pydantic import ValidationError

from agent_core.domain import Agent
from agent_core.registry.suite import DatasetScenario, EvalSuite, Scenario, SuiteProblemCode, suite_problems
from tests.m01.cases import AGENT
from tests.registry.eval_support import metric, scenario, suite, thr
from tests.registry.helpers import suite_content

DATASET: dict[str, Any] = {"id": "s1", "source": "dataset", "dataset_id": "casos_reales",
                           "dataset_hash": "a" * 64}


def make_agent(*metrics: Any) -> Agent:
    data = deepcopy(AGENT) | {"metrics": [m.model_dump(mode="json") for m in metrics]}
    return Agent.model_validate(data)


def codes(problems: list[Any]) -> list[str]:
    return [p.code.value for p in problems]


def test_main_format_suite_is_still_valid() -> None:  # compatibility with `main`'s suites (D1)
    parsed = EvalSuite.model_validate(suite_content())
    [only] = parsed.scenarios
    assert isinstance(only, Scenario) and only.source == "scripted"
    assert parsed.thresholds == {} and only.assertions == [] and only.repetitions is None
    assert parsed.repetitions_of(only) == parsed.repetitions


def test_valid_suite_has_no_problems() -> None:
    agent = make_agent(metric("m_gate"), metric("m_mon", role="monitor"))
    assert suite_problems(agent, suite([scenario("s1")], {"m_gate": thr("0.02")})) == []


def test_scenario_repetitions_override_the_suite_default() -> None:
    parsed = suite([scenario("s1", repetitions=None), scenario("s2", repetitions=5)], repetitions=2)
    first, second = parsed.scenarios
    assert (parsed.repetitions_of(first), parsed.repetitions_of(second)) == (2, 5)


@pytest.mark.parametrize("bad", [0, 11])
def test_repetitions_are_bounded(bad: int) -> None:
    with pytest.raises(ValidationError):
        suite([scenario("s1", repetitions=bad)])


def test_dataset_scenario_is_parsed_but_cannot_be_run() -> None:
    parsed = suite([DATASET])
    assert isinstance(parsed.scenarios[0], DatasetScenario)
    with pytest.raises(ValueError):
        parsed.scripted()


@pytest.mark.parametrize("bad", [
    DATASET | {"steps": [{"op": "start"}]},  # a dataset carries no script
    DATASET | {"principal": {"id": "cust-001"}},
    {"id": "s1", "source": "otra"},  # unknown source
    {"id": "s1", "source": "dataset"},  # dataset missing
])
def test_bad_scenario_sources_are_rejected(bad: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        suite([bad])


def test_assertion_filters_must_match_the_catalog() -> None:
    agent = make_agent(metric("m_gate"))
    typo = scenario("s1", assertions=[
        {"event": "engine.escalated", "expect": "none",
         "where": [{"field": "reason_cod", "op": "eq", "value": "x"}]}])
    bad_type = scenario("s2", assertions=[
        {"event": "engine.agent_step", "where": [{"field": "step", "op": "eq", "value": "uno"}]}])
    bad_op = scenario("s3", assertions=[
        {"event": "engine.escalated", "where": [{"field": "priority", "op": "gt", "value": 1}]}])
    good = scenario("s4", assertions=[
        {"event": "engine.agent_step", "where": [{"field": "step", "op": "ge", "value": 2}]}])
    problems = suite_problems(agent, suite([typo, bad_type, bad_op, good], {"m_gate": thr()}))
    assert [(p.code.value, p.path) for p in problems] == [
        ("invalid_assertion_filter", "/scenarios/0/assertions/0/where/0/field"),
        ("invalid_assertion_filter", "/scenarios/1/assertions/0/where/0/value"),
        ("invalid_assertion_filter", "/scenarios/2/assertions/0/where/0/field"),
    ]


def test_suite_needs_scenarios() -> None:
    with pytest.raises(ValidationError):
        suite([])


def test_duplicate_scenario_ids_are_rejected_when_parsing() -> None:  # `main` already did this
    with pytest.raises(ValidationError):
        suite([scenario("s1"), scenario("s1")])


def test_duplicate_scenario_problem_appears_on_unvalidated_instances() -> None:
    agent = make_agent(metric("m_gate"))
    built = suite([scenario("s1")], {"m_gate": thr()})
    twin = built.scenarios[0]
    raw = EvalSuite.model_construct(**(built.__dict__ | {"scenarios": [twin, twin]}))
    problems = suite_problems(agent, raw)
    assert [(p.code, p.path) for p in problems] == [(SuiteProblemCode.duplicate_scenario, "/scenarios/1/id")]


def test_thresholds_are_finite_non_negative_decimals() -> None:
    for bad in ({"m": thr("NaN")}, {"m": thr("-0.1")}, {"m": {"noise_margin": "0", "floor": "NaN"}}):
        with pytest.raises(ValidationError):
            suite([scenario("s1")], bad)


def test_agent_without_suite_is_not_publishable() -> None:  # T-EVAL-11
    assert codes(suite_problems(make_agent(metric("m_gate")), None)) == ["missing_suite"]


def test_agent_with_only_monitor_metrics_or_none_still_needs_a_suite() -> None:
    assert codes(suite_problems(make_agent(metric("m_mon", role="monitor")), None)) == ["missing_suite"]
    assert codes(suite_problems(make_agent(), None)) == ["missing_suite"]


def test_gate_and_guardrail_metrics_need_thresholds() -> None:  # T-EVAL-11
    agent = make_agent(metric("m_gate"), metric("m_guard", role="guardrail"), metric("m_mon", role="monitor"))
    problems = suite_problems(agent, suite([scenario("s1")], {"m_gate": thr()}))
    assert [(p.code.value, p.path) for p in problems] == [("missing_threshold", "/thresholds/m_guard")]


def test_thresholds_for_unknown_metrics_are_rejected() -> None:
    agent = make_agent(metric("m_gate"))
    problems = suite_problems(agent, suite([scenario("s1")], {"m_gate": thr(), "ghost": thr()}))
    assert [(p.code.value, p.path) for p in problems] == [("unknown_threshold_metric", "/thresholds/ghost")]


def test_assertion_events_must_be_in_the_catalog() -> None:
    agent = make_agent(metric("m_gate"))
    bad = scenario("s1", assertions=[{"event": "engine.nope"}])
    good = scenario("s2", assertions=[{"event": "engine.escalated",
                                       "where": [{"field": "reason_code", "op": "eq", "value": "x"}]}])
    problems = suite_problems(agent, suite([bad, good], {"m_gate": thr()}))
    assert [(p.code.value, p.path) for p in problems] == [
        ("unknown_assertion_event", "/scenarios/0/assertions/0/event")]


def test_dataset_source_is_disabled() -> None:  # T-EVAL-12
    agent = make_agent(metric("m_gate"))
    problems = suite_problems(agent, suite([DATASET], {"m_gate": thr()}))
    assert [(p.code, p.path) for p in problems] == [
        (SuiteProblemCode.dataset_source_disabled, "/scenarios/0/source")]


def test_suite_must_belong_to_the_agent() -> None:
    agent = make_agent(metric("m_gate"))
    other = suite([scenario("s1")], {"m_gate": thr()}, agent_id="otro")
    assert codes(suite_problems(agent, other)) == ["agent_mismatch"]


def test_problems_are_sorted_and_deterministic() -> None:
    agent = make_agent(metric("m_gate"), metric("m_guard", role="guardrail"))
    s = suite([scenario("s1"), DATASET | {"id": "s2"}], {"ghost": thr()})
    first, second = suite_problems(agent, s), suite_problems(agent, s)
    assert first == second == sorted(first, key=lambda p: (p.path, p.code.value))
    assert len(first) == 4  # two missing thresholds, one unknown and the dataset
