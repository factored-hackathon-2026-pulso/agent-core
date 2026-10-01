from typing import Any

import pytest

from agent_core.registry.evaluation import classify_yardstick_change
from tests.registry.support import metric, scenario, thr, yardstick


def kinds(base: Any, cand: Any) -> list[str]:
    return [c.kind for c in classify_yardstick_change(base, cand)]


def base_yardstick() -> Any:
    return yardstick(
        [metric("m_gate"), metric("m_guard", role="guardrail", higher=False)],
        [scenario("s1", repetitions=3), scenario("s2")],
        {"m_gate": thr("0.05", "0.6"), "m_guard": thr("0", "0")},
    )


def test_no_base_means_nothing_to_loosen() -> None:
    assert classify_yardstick_change(None, base_yardstick()) == []


def test_identical_yardstick_is_not_loosened() -> None:
    assert kinds(base_yardstick(), base_yardstick()) == []


# T-EVAL-08: solo endurecer no se marca
def test_tightening_is_not_flagged() -> None:
    cand = yardstick(
        [metric("m_gate", role="guardrail"), metric("m_guard", role="guardrail", higher=False),
         metric("m_new"), metric("m_mon", role="monitor")],
        [scenario("s1", repetitions=5), scenario("s2"), scenario("s3")],
        {"m_gate": thr("0.01", "0.8"), "m_guard": thr("0", "0"), "m_new": thr("0.1", "0.3")},
    )
    assert kinds(base_yardstick(), cand) == []


# T-EVAL-08: cada tipo de aflojamiento
def test_removing_a_gate_metric_is_flagged() -> None:
    cand = yardstick([metric("m_guard", role="guardrail", higher=False)],
                     [scenario("s1", 3), scenario("s2")], {"m_guard": thr("0", "0")})
    assert kinds(base_yardstick(), cand) == ["metric_removed"]


def test_degrading_a_role_is_flagged() -> None:
    cand = yardstick([metric("m_gate", role="monitor"), metric("m_guard", role="gate", higher=False)],
                     [scenario("s1", 3), scenario("s2")],
                     {"m_gate": thr("0.05", "0.6"), "m_guard": thr("0", "0")})
    assert sorted(kinds(base_yardstick(), cand)) == ["role_degraded", "role_degraded"]


def test_changing_an_expression_direction_or_judge_is_flagged() -> None:
    cand = yardstick(
        [metric("m_gate", event="engine.run_closed"), metric("m_guard", role="guardrail", higher=True)],
        [scenario("s1", 3), scenario("s2")],
        {"m_gate": thr("0.05", "0.6"), "m_guard": thr("0", "0")})
    assert kinds(base_yardstick(), cand) == ["metric_changed", "metric_changed"]


def test_description_or_alert_changes_are_not_flagged() -> None:
    changed = metric("m_gate").model_copy(update={"description": "otra descripcion"})
    cand = yardstick([changed, metric("m_guard", role="guardrail", higher=False)],
                     [scenario("s1", 3), scenario("s2")],
                     {"m_gate": thr("0.05", "0.6"), "m_guard": thr("0", "0")})
    assert kinds(base_yardstick(), cand) == []


def test_removing_a_threshold_is_flagged() -> None:
    cand = yardstick([metric("m_gate"), metric("m_guard", role="guardrail", higher=False)],
                     [scenario("s1", 3), scenario("s2")], {"m_guard": thr("0", "0")})
    assert kinds(base_yardstick(), cand) == ["threshold_removed"]


@pytest.mark.parametrize(
    ("floor", "expected"),
    [("0.5", ["floor_loosened"]), (None, ["floor_loosened"]), ("0.6", []), ("0.9", [])],
)
def test_floor_loosening_when_higher_is_better(floor: str | None, expected: list[str]) -> None:
    cand = yardstick([metric("m_gate"), metric("m_guard", role="guardrail", higher=False)],
                     [scenario("s1", 3), scenario("s2")],
                     {"m_gate": thr("0.05", floor), "m_guard": thr("0", "0")})
    assert kinds(base_yardstick(), cand) == expected


# Review Focus 2: con "menor es mejor" el piso es un máximo
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
    changed_turns = scenario("s2")
    changed_turns["source"] = changed_turns["source"] | {"user_turns": ["otra cosa"]}
    changed = yardstick(metrics, [scenario("s1", 3), changed_turns], thresholds)
    fewer_runs = yardstick(metrics, [scenario("s1", 1), scenario("s2")], thresholds)
    assert kinds(base_yardstick(), removed) == ["scenario_removed"]
    assert kinds(base_yardstick(), changed) == ["scenario_changed"]
    assert kinds(base_yardstick(), fewer_runs) == ["repetitions_lowered"]


def test_monitor_metrics_are_not_part_of_the_yardstick() -> None:
    base = yardstick([metric("m_mon", role="monitor")], [scenario("s1")])
    cand_removed = yardstick([], [scenario("s1")])
    cand_changed = yardstick(
        [metric("m_mon", role="monitor", event="engine.run_closed")], [scenario("s1")])
    assert kinds(base, cand_removed) == [] and kinds(base, cand_changed) == []


def test_output_is_sorted_and_deterministic() -> None:
    cand = yardstick([metric("m_gate", role="monitor")], [scenario("s2")], {})
    first = classify_yardstick_change(base_yardstick(), cand)
    assert first == classify_yardstick_change(base_yardstick(), cand)
    assert first == sorted(first, key=lambda c: (c.kind, c.target))
    assert all(c.target and c.message for c in first)
