"""`Step.input` y `Expect` sobre `suggestions` (ADR 0026, B3 y B4 del informe de brecha)."""

from typing import Any

import pytest
from pydantic import ValidationError

from agent_core.domain import (
    EscalateSuggestion,
    ReplySuggestion,
    Suggestion,
    ToolSuggestion,
    canonical_bytes,
)
from agent_core.registry.evaluation.evaluator import ScenarioEvaluator
from agent_core.registry.evaluation.local_sandbox import LocalSandbox
from agent_core.registry.evaluation.ports import EvalRequest, EvalTarget, ScenarioRun
from agent_core.registry.evaluation.scoring import score_run
from agent_core.registry.evaluation.yardstick import Yardstick
from agent_core.registry.snapshot import SnapshotRegistry
from agent_core.registry.suite import EvalSuite, Expect, Scenario
from testing.fakes.ids import FakeIds
from tests.registry.helpers import demo_pinned
from tests.registry.test_evaluator import FakeHarness, _closed

REPLY = ReplySuggestion(text="El saldo es 1342.80 USD.", citations=["f1"], language="es")
TOOL = ToolSuggestion(tool="leer_movimientos@1", args={}, why="Ver movimientos.")
ESC = EscalateSuggestion(
    reason_code="rule:sugerir", evidence=["sla_estado: vencido"], motive_draft="Pide supervisor por fraude."
)


def scenario_body(**over: Any) -> dict[str, Any]:
    return {
        "id": "uno",
        "principal": {"id": "cust-001"},
        "steps": [{"op": "start", "input": {"idioma": "es", "turnos": []}}],
    } | over


def test_start_accepts_an_input_and_only_start() -> None:
    scenario = Scenario.model_validate(scenario_body())
    assert scenario.steps[0].input == {"idioma": "es", "turnos": []}
    with pytest.raises(ValidationError):
        Scenario.model_validate(
            scenario_body(steps=[{"op": "start"}, {"op": "turn", "text": "hola", "input": {"a": 1}}])
        )


def test_an_absent_input_does_not_change_the_dump_so_published_suites_keep_their_hash() -> None:
    old = {"op": "start"}
    scenario = Scenario.model_validate(scenario_body(steps=[old, {"op": "turn", "text": "hola"}]))
    dumped = scenario.model_dump(mode="json")
    assert all("input" not in step for step in dumped["steps"])
    assert "suggestions" not in dumped["expect"] and "suggestion_count" not in dumped["expect"]
    assert Scenario.model_validate(dumped) == scenario
    # and a present input is part of what is hashed
    assert canonical_bytes(
        Scenario.model_validate(scenario_body()).model_dump(mode="json")
    ) != canonical_bytes(dumped)


def test_expect_without_suggestions_is_the_same_as_before() -> None:
    assert Expect().model_dump(mode="json") == {"outcome": None, "actions_verified": [], "escalated": None}


def score(
    expect: dict[str, Any], suggestions: list[Suggestion] | None, sensitive: list[str] | None = None
) -> Any:
    return score_run(
        _closed("completed"), Expect.model_validate(expect), sensitive or [], suggestions=suggestions
    )


def test_an_empty_list_can_be_required() -> None:
    assert score({"suggestion_count": 0}, []).passed
    failed = score({"suggestion_count": 0}, [REPLY])
    assert not failed.passed and "suggestion_count" in failed.failures[0]


def test_a_kind_can_be_required_or_forbidden() -> None:
    assert score({"suggestions": [{"type": "escalate"}]}, [ESC, REPLY]).passed
    assert not score({"suggestions": [{"type": "escalate"}]}, [REPLY]).passed
    assert score({"suggestions": [{"type": "escalate", "expect": "none"}]}, [REPLY]).passed
    assert not score({"suggestions": [{"type": "escalate", "expect": "none"}]}, [ESC]).passed


def test_fields_of_an_item_are_matched_together() -> None:
    both = {"suggestions": [{"type": "tool", "tool": "leer_movimientos@1"}]}
    assert score(both, [TOOL]).passed
    assert not score({"suggestions": [{"type": "tool", "tool": "leer_productos@1"}]}, [TOOL]).passed
    assert score({"suggestions": [{"type": "escalate", "reason_code": "rule:sugerir"}]}, [ESC]).passed
    assert not score({"suggestions": [{"type": "reply", "language": "pt"}]}, [REPLY]).passed
    assert not score({"suggestions": [{"type": "reply", "citations_min": 2}]}, [REPLY]).passed


def test_text_contains_and_excludes_look_at_the_texts_of_the_item() -> None:
    assert score({"suggestions": [{"type": "reply", "text_contains": ["1342.80"]}]}, [REPLY]).passed
    assert not score({"suggestions": [{"type": "reply", "text_contains": ["9999"]}]}, [REPLY]).passed
    assert not score({"suggestions": [{"type": "reply", "text_excludes": ["1342.80"]}]}, [REPLY]).passed
    assert score({"suggestions": [{"type": "escalate", "text_contains": ["fraude"]}]}, [ESC]).passed


def test_the_expectation_fails_closed_when_the_harness_returned_no_suggestions() -> None:
    result = score({"suggestion_count": 0}, None)
    assert not result.passed and "sugerencias" in result.failures[0]
    assert score({}, None).passed  # nothing was asked of them


def test_a_sensitive_value_in_a_suggestion_is_a_pii_leak() -> None:
    leaky = ReplySuggestion(text="Tu tarjeta 4111111111111111 está bloqueada.", citations=[], language="es")
    clean = score({}, [REPLY], ["4111111111111111"])
    assert clean.guardrails["platform_pii_leak"] == 0
    assert score({}, [leaky], ["4111111111111111"]).guardrails["platform_pii_leak"] == 1


def _expect_suite(expect: dict[str, Any]) -> EvalSuite:
    return EvalSuite.model_validate(
        {
            "id": "s",
            "version": "1.0.0",
            "agent_id": "atencion",
            "repetitions": 1,
            "scenarios": [scenario_body(expect=expect)],
        }
    )


class SuggestingHarness(FakeHarness):
    def __init__(self, suggestions: list[Suggestion]) -> None:
        super().__init__({"candidate": "completed", "base": "completed"})
        self.suggestions = suggestions

    def run_with_suggestions(
        self, target: EvalTarget, agent_id: str, scenario: Scenario, tools: Any
    ) -> ScenarioRun:
        return ScenarioRun(events=_closed("completed"), suggestions=self.suggestions)


def _evaluate(harness: Any, expect: dict[str, Any]) -> str:
    pinned = demo_pinned()
    target = EvalTarget("candidate", pinned.release, SnapshotRegistry(pinned.release, pinned.entities))
    suite = _expect_suite(expect)
    report = ScenarioEvaluator(harness, LocalSandbox(FakeIds())).run(
        EvalRequest(candidate=target, new=Yardstick(metrics=[], suite=suite))
    )
    return report.verdict


def test_the_evaluator_scores_the_suggestions_of_a_harness_that_returns_them() -> None:
    assert _evaluate(SuggestingHarness([ESC]), {"suggestions": [{"type": "escalate"}]}) == "pass"
    assert _evaluate(SuggestingHarness([REPLY]), {"suggestions": [{"type": "escalate"}]}) == "fail"


def test_a_harness_that_only_returns_events_still_works_but_cannot_satisfy_a_suggestion_expectation() -> None:
    plain = FakeHarness({"candidate": "completed", "base": "completed"})
    assert _evaluate(plain, {"outcome": "completed"}) == "pass"
    assert _evaluate(plain, {"suggestion_count": 0}) == "fail"
