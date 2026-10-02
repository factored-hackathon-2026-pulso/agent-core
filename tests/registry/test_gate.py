from decimal import Decimal
from typing import Any

import pytest

from agent_core.registry.evaluation.gate import evaluate_gate, meets_floor, not_worse
from agent_core.registry.evaluation.report import GateItem, GateRuns, SuiteMeasurement
from agent_core.registry.evaluation.scoring import PLATFORM_GUARDRAILS
from agent_core.registry.evaluation.yardstick import Yardstick
from tests.registry.eval_support import metric, scenario, thr, yardstick

# --- double yardstick (ADR 0020, evaluation spec section 6) -----------------------------------------------

ZEROS = {pid: "0" for pid in PLATFORM_GUARDRAILS}
GOOD = {"quality": "0.8", "speed": "0.5", "leaks": "0"}


def measured(metrics: dict[str, str], scenarios: dict[str, bool] | None = None, status: str = "ok",
             platform: dict[str, str] | None = None) -> SuiteMeasurement:
    values = {**(ZEROS if platform is None else platform), **metrics}
    return SuiteMeasurement.model_validate(
        {"status": status, "metrics": values, "scenarios": {"s1": True} if scenarios is None else scenarios})


def base_yardstick() -> Yardstick:
    return yardstick(
        [metric("quality"), metric("speed"), metric("leaks", role="guardrail", higher=False)],
        [scenario("s1")],
        {"quality": thr("0.05", "0.5"), "speed": thr("0.1", "0.4"), "leaks": thr("0", "0")})


def failed(items: list[GateItem]) -> list[str]:
    return sorted(i.metric_id for i in items if not i.passed)


def only_new(new: SuiteMeasurement) -> GateRuns:
    return GateRuns(cand_on_new=new)


def both(base_old: SuiteMeasurement, cand_old: SuiteMeasurement,
         cand_new: SuiteMeasurement | None = None) -> GateRuns:
    return GateRuns(base_on_old=base_old, cand_on_old=cand_old,
                    cand_on_new=cand_old if cand_new is None else cand_new)


def test_direction_helpers() -> None:
    assert not_worse(Decimal("0.96"), Decimal("1"), Decimal("0.05"), True)
    assert not not_worse(Decimal("0.9"), Decimal("1"), Decimal("0.05"), True)
    assert not_worse(Decimal("1.04"), Decimal("1"), Decimal("0.05"), False)
    assert not not_worse(Decimal("1.1"), Decimal("1"), Decimal("0.05"), False)
    assert meets_floor(Decimal("0.5"), Decimal("0.5"), True)
    assert not meets_floor(Decimal("0.4"), Decimal("0.5"), True)
    assert meets_floor(Decimal("3"), Decimal("3"), False)
    assert not meets_floor(Decimal("4"), Decimal("3"), False)


def test_no_base_passes_when_every_metric_meets_its_floor() -> None:
    verdict, items = evaluate_gate(None, base_yardstick(),
                                   only_new(measured({"quality": "0.7", "speed": "0.5", "leaks": "0"})))
    assert verdict == "pass"
    assert {i.metric_id for i in items} >= {"quality", "speed", "leaks", *PLATFORM_GUARDRAILS}


def test_no_base_fails_when_a_floor_is_missing_or_not_met() -> None:  # T-EVAL-13 (formerly T-REG-10)
    no_floor = yardstick([metric("quality")], [scenario("s1")], {"quality": thr("0.05")})
    verdict, items = evaluate_gate(None, no_floor, only_new(measured({"quality": "0.9"})))
    assert verdict == "fail" and failed(items) == ["quality"]
    _, below = evaluate_gate(None, base_yardstick(),
                             only_new(measured({"quality": "0.1", "speed": "0.5", "leaks": "0"})))
    assert failed(below) == ["quality"]


def test_lower_is_better_floor_is_a_ceiling() -> None:
    cand = yardstick([metric("leaks", role="guardrail", higher=False)], [scenario("s1")],
                     {"leaks": thr("0", "2")})
    assert failed(evaluate_gate(None, cand, only_new(measured({"leaks": "3"})))[1]) == ["leaks"]
    assert evaluate_gate(None, cand, only_new(measured({"leaks": "2"})))[0] == "pass"


def test_a_worse_guardrail_fails_even_if_a_gate_metric_improves() -> None:  # T-EVAL-05 (formerly T-REG-08)
    base = base_yardstick()
    runs = both(measured({"quality": "0.6", "speed": "0.5", "leaks": "0"}),
                measured({"quality": "0.9", "speed": "0.5", "leaks": "1"}))
    verdict, items = evaluate_gate(base, base, runs)
    assert verdict == "fail" and failed(items) == ["leaks"]  # unchanged: only the old yardstick judges it


def test_each_gate_metric_is_judged_on_its_own() -> None:  # T-EVAL-06 (formerly T-REG-09)
    base = base_yardstick()
    old_base = measured({"quality": "0.80", "speed": "0.50", "leaks": "0"})
    within_noise = measured({"quality": "0.76", "speed": "0.45", "leaks": "0"})
    regressed = measured({"quality": "0.95", "speed": "0.20", "leaks": "0"})
    assert evaluate_gate(base, base, both(old_base, within_noise))[0] == "pass"
    verdict, items = evaluate_gate(base, base, both(old_base, regressed))
    assert verdict == "fail" and "speed" in failed(items) and "quality" not in failed(items)


def test_the_base_yardstick_still_applies_when_the_candidate_drops_or_loosens_it() -> None:  # T-EVAL-07
    base = base_yardstick()
    cand = yardstick([metric("quality", role="monitor")], [scenario("s1")], {})
    runs = both(measured({"quality": "0.80", "speed": "0.50", "leaks": "0"}),
                measured({"quality": "0.30", "speed": "0.10", "leaks": "0"}))
    verdict, items = evaluate_gate(base, cand, runs)
    assert verdict == "fail" and {"quality", "speed"} <= set(failed(items))


def test_a_metric_without_value_fails() -> None:  # Review Focus 1
    base = base_yardstick()
    zeros = {pid: Decimal(0) for pid in PLATFORM_GUARDRAILS}
    cand_old = SuiteMeasurement(metrics=zeros | {"leaks": Decimal(0)}, scenarios={"s1": True})
    verdict, items = evaluate_gate(base, base, both(measured(GOOD), cand_old))
    assert verdict == "fail" and {"quality", "speed"} <= set(failed(items))
    missing_base = SuiteMeasurement(metrics={"leaks": Decimal(0)}, scenarios={"s1": True})
    assert "quality" in failed(evaluate_gate(base, base, both(missing_base, measured(GOOD)))[1])


def test_a_scenario_that_passed_in_the_base_and_fails_in_the_candidate_fails() -> None:
    base = base_yardstick()
    runs = both(measured(GOOD, {"s1": True}), measured(GOOD, {"s1": False}))
    verdict, items = evaluate_gate(base, base, runs)
    assert verdict == "fail" and "scenario/s1" in failed(items)


@pytest.mark.parametrize(
    ("base_scenarios", "cand_scenarios", "expected"),
    [
        ({"s1": True}, {"s1": True}, []),
        ({"s1": True}, {"s1": False}, ["scenario/s1"]),
        ({"s1": True}, {}, ["scenario/s1"]),  # the candidate did not measure the scenario
        ({}, {"s1": False}, ["scenario/s1"]),  # the base did not measure it: fails closed
        ({}, {}, ["scenario/s1"]),
        ({"s1": False}, {"s1": False}, []),  # already failing in the base: not a regression
        ({"s1": False}, {}, []),  # an explicit False in the base exempts it
    ],
)
def test_scenario_regression_fails_closed_when_unmeasured(
    base_scenarios: dict[str, bool], cand_scenarios: dict[str, bool], expected: list[str]
) -> None:
    base = base_yardstick()
    runs = both(measured(GOOD, base_scenarios), measured(GOOD, cand_scenarios), measured(GOOD, {"s1": True}))
    assert failed(evaluate_gate(base, base, runs)[1]) == expected


def test_a_metric_whose_identity_changed_is_judged_on_the_new_yardstick() -> None:
    base = base_yardstick()
    old = measured(GOOD)

    def candidate(floor: str | None, **over: Any) -> Yardstick:
        return yardstick(
            [metric("quality", **over), metric("speed"), metric("leaks", role="guardrail", higher=False)],
            [scenario("s1")],
            {"quality": thr("0.05", floor), "speed": thr("0.1", "0.4"), "leaks": thr("0", "0")})

    def judge(cand: Yardstick, quality: str) -> tuple[str, list[GateItem]]:
        return evaluate_gate(base, cand, both(old, old, measured({**GOOD, "quality": quality})))

    for change in ({"event": "engine.run_closed"}, {"higher": False}):
        higher = change.get("higher", True)
        verdict, items = judge(candidate("0.5", **change), "0.6" if higher else "0.3")
        assert verdict == "pass"
        assert ("quality", "new_yardstick") in {(i.metric_id, i.phase) for i in items}
        assert failed(judge(candidate("0.5", **change), "0.4" if higher else "0.9")[1]) == ["quality"]
        assert failed(judge(candidate(None, **change), "0.8")[1]) == ["quality"]


def test_a_guardrail_with_a_declared_noise_margin_still_has_zero_tolerance() -> None:
    base = yardstick([metric("leaks", role="guardrail", higher=False)], [scenario("s1")],
                     {"leaks": thr("0.5", "0")})
    _, items = evaluate_gate(base, base, both(measured({"leaks": "0"}), measured({"leaks": "0.3"})))
    item = next(i for i in items if i.metric_id == "leaks" and i.phase == "base_yardstick")
    assert not item.passed and item.noise_margin == Decimal(0)
    assert failed(items) == ["leaks"]


def _four_metrics() -> list[Any]:
    return [metric("quality"), metric("speed"), metric("leaks", role="guardrail", higher=False),
            metric("fresh")]


def test_new_metrics_and_scenarios_must_meet_the_new_yardstick() -> None:
    cand = yardstick(
        _four_metrics(),
        [scenario("s1"), scenario("s_new")],
        {"quality": thr("0.05", "0.5"), "speed": thr("0.1", "0.4"), "leaks": thr("0", "0"),
         "fresh": thr("0", "0.5")})
    old = measured(GOOD)
    ok_new = measured({**GOOD, "fresh": "0.6"}, {"s1": True, "s_new": True})
    bad_new = measured({**GOOD, "fresh": "0.1"}, {"s1": True, "s_new": False})
    assert evaluate_gate(base_yardstick(), cand, both(old, old, ok_new))[0] == "pass"
    verdict, items = evaluate_gate(base_yardstick(), cand, both(old, old, bad_new))
    assert verdict == "fail" and {"fresh", "scenario/s_new"} <= set(failed(items))


def test_a_new_metric_without_floor_fails() -> None:
    cand = yardstick(
        _four_metrics(),
        [scenario("s1")],
        {"quality": thr("0.05", "0.5"), "speed": thr("0.1", "0.4"), "leaks": thr("0", "0"),
         "fresh": thr("0")})
    old = measured({**GOOD, "fresh": "1"})
    assert failed(evaluate_gate(base_yardstick(), cand, both(old, old))[1]) == ["fresh"]


def test_platform_guardrails_must_be_zero() -> None:
    runs = only_new(measured({"quality": "0.9", "speed": "0.9", "leaks": "0"},
                             platform=ZEROS | {"platform_pii_leak": "1"}))
    verdict, items = evaluate_gate(None, base_yardstick(), runs)
    assert verdict == "fail" and failed(items) == ["platform_pii_leak"]


def test_a_measured_pii_leak_on_the_candidate_old_suite_fails_even_if_not_worse_than_base() -> None:
    base = base_yardstick()
    leaky = measured(GOOD, platform=ZEROS | {"platform_pii_leak": "2"})
    verdict, items = evaluate_gate(base, base, both(leaky, leaky, measured(GOOD)))
    assert verdict == "fail" and failed(items) == ["platform_pii_leak"]
    assert next(i for i in items if i.metric_id == "platform_pii_leak").base_value == Decimal(2)


def test_a_platform_guardrail_that_was_not_measured_fails() -> None:
    partial = {pid: "0" for pid in PLATFORM_GUARDRAILS[:-1]}
    runs = only_new(measured({"quality": "0.9", "speed": "0.9", "leaks": "0"}, platform=partial))
    assert failed(evaluate_gate(None, base_yardstick(), runs)[1]) == [PLATFORM_GUARDRAILS[-1]]


@pytest.mark.parametrize("which", ["base_on_old", "cand_on_old", "cand_on_new"])
def test_failed_infra_is_not_a_verdict(which: str) -> None:  # T-EVAL-15
    base = base_yardstick()
    ok = measured(GOOD)
    runs = {"base_on_old": ok, "cand_on_old": ok, "cand_on_new": ok} | {
        which: SuiteMeasurement(status="failed_infra")}
    assert evaluate_gate(base, base, GateRuns(**runs)) == ("failed_infra", [])


def test_a_base_without_its_runs_is_a_programming_error() -> None:
    with pytest.raises(ValueError):
        evaluate_gate(base_yardstick(), base_yardstick(), only_new(measured({})))


def test_items_carry_value_base_and_margin_apart_from_floor() -> None:  # decision D8
    base = base_yardstick()
    _, items = evaluate_gate(base, base, both(measured({"quality": "0.80", "speed": "0.50", "leaks": "0"}),
                                              measured({"quality": "0.85", "speed": "0.50", "leaks": "0"})))
    item = next(i for i in items if i.metric_id == "quality" and i.phase == "base_yardstick")
    assert (item.value, item.base_value, item.noise_margin, item.floor, item.role) == (
        Decimal("0.85"), Decimal("0.80"), Decimal("0.05"), None, "gate")


@pytest.mark.parametrize("which", ["base_on_old", "cand_on_old"])
def test_a_platform_guardrail_unmeasured_on_the_old_suite_fails_with_a_base(which: str) -> None:
    base = base_yardstick()
    full = measured(GOOD)
    gap = PLATFORM_GUARDRAILS[-1]
    partial = SuiteMeasurement(metrics={k: v for k, v in full.metrics.items() if k != gap},
                               scenarios={"s1": True})
    reports = {"base_on_old": full, "cand_on_old": full} | {which: partial}
    verdict, items = evaluate_gate(base, base, GateRuns(**reports, cand_on_new=full))
    assert verdict == "fail" and failed(items) == [gap]


def test_a_base_without_a_recorded_suite_is_judged_by_floors_only() -> None:  # decision D3
    base = Yardstick(metrics=base_yardstick().metrics, suite=None)
    verdict, items = evaluate_gate(base, base_yardstick(),
                                   only_new(measured({"quality": "0.6", "speed": "0.5", "leaks": "0"})))
    assert verdict == "pass" and {i.phase for i in items} == {"new_yardstick", "platform"}
    quality = next(i for i in items if i.metric_id == "quality")
    assert (quality.floor, quality.noise_margin) == (Decimal("0.5"), None)
    _, low = evaluate_gate(base, base_yardstick(),
                           only_new(measured({"quality": "0.4", "speed": "0.5", "leaks": "0"})))
    assert failed(low) == ["quality"]
