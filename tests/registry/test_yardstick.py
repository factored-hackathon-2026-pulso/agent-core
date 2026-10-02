"""Classification of yardstick loosening (evaluation spec section 6.2, T-EVAL-08)."""

from typing import Any

import pytest

from agent_core.registry.evaluation.yardstick import Yardstick, classify_yardstick_change
from tests.registry.eval_support import metric, scenario, suite, thr, yardstick


def kinds(base: Any, cand: Any) -> list[str]:
    return [c.kind for c in classify_yardstick_change(base, cand)]


def base_yardstick() -> Yardstick:
    return yardstick(
        [metric("m_gate"), metric("m_guard", role="guardrail", higher=False)],
        [scenario("s1", repetitions=3), scenario("s2")],
        {"m_gate": thr("0.05", "0.6"), "m_guard": thr("0", "0")})


def test_no_base_means_nothing_to_loosen() -> None:
    assert classify_yardstick_change(None, base_yardstick()) == []


def test_identical_yardstick_is_not_loosened() -> None:
    assert kinds(base_yardstick(), base_yardstick()) == []


def test_tightening_is_not_flagged() -> None:  # T-EVAL-08
    cand = yardstick(
        [metric("m_gate", role="guardrail"), metric("m_guard", role="guardrail", higher=False),
         metric("m_new"), metric("m_mon", role="monitor")],
        [scenario("s1", repetitions=5), scenario("s2"), scenario("s3")],
        {"m_gate": thr("0.01", "0.8"), "m_guard": thr("0", "0"), "m_new": thr("0.1", "0.3")})
    assert kinds(base_yardstick(), cand) == []


def test_removing_a_gate_metric_is_flagged() -> None:
    cand = yardstick([metric("m_guard", role="guardrail", higher=False)],
                     [scenario("s1", 3), scenario("s2")], {"m_guard": thr("0", "0")})
    assert kinds(base_yardstick(), cand) == ["metric_removed"]


def test_degrading_a_role_is_flagged() -> None:
    cand = yardstick([metric("m_gate", role="monitor"), metric("m_guard", role="gate", higher=False)],
                     [scenario("s1", 3), scenario("s2")],
                     {"m_gate": thr("0.05", "0.6"), "m_guard": thr("0", "0")})
    assert sorted(kinds(base_yardstick(), cand)) == ["role_degraded", "role_degraded"]


def test_changing_an_expression_or_direction_is_flagged() -> None:
    cand = yardstick(
        [metric("m_gate", event="engine.run_closed"), metric("m_guard", role="guardrail", higher=True)],
        [scenario("s1", 3), scenario("s2")],
        {"m_gate": thr("0.05", "0.6"), "m_guard": thr("0", "0")})
    assert kinds(base_yardstick(), cand) == ["metric_changed", "metric_changed"]


def _promoted(**over: Any) -> Yardstick:
    """`m_gate` promoted to guardrail in the proposal, with `over` changing its definition."""
    higher = over.pop("higher", True)
    return yardstick(
        [metric("m_gate", role="guardrail", higher=higher, **over),
         metric("m_guard", role="guardrail", higher=False)],
        [scenario("s1", 3), scenario("s2")],
        {"m_gate": thr("0.05", "0.6"), "m_guard": thr("0", "0")})


def test_a_pure_promotion_is_not_flagged() -> None:
    assert kinds(base_yardstick(), _promoted()) == []


def test_promotion_with_an_expression_change_is_flagged() -> None:
    assert kinds(base_yardstick(), _promoted(event="engine.run_closed")) == ["metric_changed"]


def test_promotion_with_a_direction_flip_is_flagged() -> None:
    assert kinds(base_yardstick(), _promoted(higher=False)) == ["metric_changed"]


def test_promotion_that_changes_expression_and_direction_is_flagged() -> None:
    base = yardstick([metric("q")], [scenario("s1")], {"q": thr("0", "0.5")})
    cand = yardstick([metric("q", role="guardrail", higher=False, event="engine.escalated")],
                     [scenario("s1")], {"q": thr("0", "1000")})
    assert kinds(base, cand) == ["metric_changed"]


def test_degraded_role_with_changed_expression_reports_only_role_degraded() -> None:
    cand = yardstick(
        [metric("m_gate", role="monitor", event="engine.run_closed"),
         metric("m_guard", role="guardrail", higher=False)],
        [scenario("s1", 3), scenario("s2")],
        {"m_gate": thr("0.05", "0.6"), "m_guard": thr("0", "0")})
    assert kinds(base_yardstick(), cand) == ["role_degraded"]


def test_adding_a_threshold_where_the_base_had_none_widens_the_noise() -> None:
    base = yardstick([metric("q")], [scenario("s1")])
    assert kinds(base, yardstick([metric("q")], [scenario("s1")], {"q": thr("100")})) == ["noise_widened"]
    assert kinds(base, yardstick([metric("q")], [scenario("s1")], {"q": thr("0", "0.5")})) == []


def test_description_or_alert_changes_are_not_flagged() -> None:
    changed = metric("m_gate").model_copy(update={"description": "another description"})
    cand = yardstick([changed, metric("m_guard", role="guardrail", higher=False)],
                     [scenario("s1", 3), scenario("s2")],
                     {"m_gate": thr("0.05", "0.6"), "m_guard": thr("0", "0")})
    assert kinds(base_yardstick(), cand) == []


def test_removing_a_threshold_is_flagged() -> None:
    cand = yardstick([metric("m_gate"), metric("m_guard", role="guardrail", higher=False)],
                     [scenario("s1", 3), scenario("s2")], {"m_guard": thr("0", "0")})
    assert kinds(base_yardstick(), cand) == ["threshold_removed"]


@pytest.mark.parametrize(("floor", "expected"),
                         [("0.5", ["floor_loosened"]), (None, ["floor_loosened"]), ("0.6", []), ("0.9", [])])
def test_floor_loosening_when_higher_is_better(floor: str | None, expected: list[str]) -> None:
    cand = yardstick([metric("m_gate"), metric("m_guard", role="guardrail", higher=False)],
                     [scenario("s1", 3), scenario("s2")],
                     {"m_gate": thr("0.05", floor), "m_guard": thr("0", "0")})
    assert kinds(base_yardstick(), cand) == expected


@pytest.mark.parametrize(("floor", "expected"), [("1", ["floor_loosened"]), ("0", []), ("-1", [])])
def test_floor_loosening_when_lower_is_better(floor: str, expected: list[str]) -> None:
    cand = yardstick([metric("m_gate"), metric("m_guard", role="guardrail", higher=False)],
                     [scenario("s1", 3), scenario("s2")],
                     {"m_gate": thr("0.05", "0.6"), "m_guard": thr("0", floor)})
    assert kinds(base_yardstick(), cand) == expected


def test_widening_the_noise_margin_is_flagged() -> None:
    cand = yardstick([metric("m_gate"), metric("m_guard", role="guardrail", higher=False)],
                     [scenario("s1", 3), scenario("s2")],
                     {"m_gate": thr("0.2", "0.6"), "m_guard": thr("0", "0")})
    assert kinds(base_yardstick(), cand) == ["noise_widened"]


def test_removing_changing_or_weakening_scenarios_is_flagged() -> None:
    metrics = [metric("m_gate"), metric("m_guard", role="guardrail", higher=False)]
    thresholds = {"m_gate": thr("0.05", "0.6"), "m_guard": thr("0", "0")}
    removed = yardstick(metrics, [scenario("s1", 3)], thresholds)
    changed_steps = scenario("s2", steps=[{"op": "start"}, {"op": "turn", "text": "something else"}])
    changed = yardstick(metrics, [scenario("s1", 3), changed_steps], thresholds)
    fewer_runs = yardstick(metrics, [scenario("s1", 1), scenario("s2")], thresholds)
    assert kinds(base_yardstick(), removed) == ["scenario_removed"]
    assert kinds(base_yardstick(), changed) == ["scenario_changed"]
    assert kinds(base_yardstick(), fewer_runs) == ["repetitions_lowered"]


def test_lowering_the_suite_default_repetitions_is_flagged() -> None:  # Review Focus 3
    base = Yardstick(metrics=[], suite=suite([scenario("s1", repetitions=None)], repetitions=3))
    lowered = Yardstick(metrics=[], suite=suite([scenario("s1", repetitions=None)], repetitions=2))
    explicit = Yardstick(metrics=[], suite=suite([scenario("s1", repetitions=3)], repetitions=1))
    assert kinds(base, lowered) == ["repetitions_lowered"]
    assert kinds(base, explicit) == []  # inherited or explicit, they are the same runs


def test_monitor_metrics_are_not_part_of_the_yardstick() -> None:
    base = yardstick([metric("m_mon", role="monitor")], [scenario("s1")])
    assert kinds(base, yardstick([], [scenario("s1")])) == []
    assert kinds(base, yardstick([metric("m_mon", role="monitor", event="engine.run_closed")],
                                 [scenario("s1")])) == []


def test_a_base_without_a_recorded_suite_only_compares_metrics() -> None:  # decision D3
    base = Yardstick(metrics=[metric("m_gate"), metric("m_mon", role="monitor")], suite=None)
    assert kinds(base, yardstick([], [scenario("s1")])) == ["metric_removed"]
    kept = yardstick([metric("m_gate")], [scenario("s1")], {"m_gate": thr("0.3", "0.1")})
    assert kinds(base, kept) == []  # with no base suite there are no thresholds or scenarios to loosen


def test_output_is_sorted_and_deterministic() -> None:
    cand = yardstick([metric("m_gate", role="monitor")], [scenario("s2")], {})
    first = classify_yardstick_change(base_yardstick(), cand)
    assert first == classify_yardstick_change(base_yardstick(), cand)
    assert first == sorted(first, key=lambda c: (c.kind, c.target))
    assert all(c.target and c.message for c in first)
