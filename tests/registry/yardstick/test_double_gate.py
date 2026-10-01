from decimal import Decimal
from typing import Any

import pytest

from agent_core.registry.evaluation.double_gate import (
    EvalReport,
    GateRuns,
    evaluate_gate,
    meets_floor,
    not_worse,
)
from agent_core.registry.evaluation.platform import PLATFORM_GUARDRAILS
from tests.registry.yardstick.support import metric, scenario, thr, yardstick

ZEROS = {pid: "0" for pid in PLATFORM_GUARDRAILS}


def report(
    metrics: dict[str, str],
    scenarios: dict[str, bool] | None = None,
    status: str = "ok",
    platform: dict[str, str] | None = None,
) -> EvalReport:
    values = {**(ZEROS if platform is None else platform), **metrics}
    return EvalReport.model_validate(
        {
            "status": status,
            "metrics": values,
            "scenarios": scenarios if scenarios is not None else {"s1": True},
        }
    )


def base_yardstick() -> Any:
    return yardstick(
        [metric("quality"), metric("speed"), metric("leaks", role="guardrail", higher=False)],
        [scenario("s1")],
        {"quality": thr("0.05", "0.5"), "speed": thr("0.1", "0.4"), "leaks": thr("0", "0")},
    )


def failed(verdict: Any) -> list[str]:
    return sorted(i.metric_id for i in verdict.items if not i.passed)


def test_direction_helpers() -> None:
    assert not_worse(Decimal("0.96"), Decimal("1"), Decimal("0.05"), True)
    assert not not_worse(Decimal("0.9"), Decimal("1"), Decimal("0.05"), True)
    assert not_worse(Decimal("1.04"), Decimal("1"), Decimal("0.05"), False)
    assert not not_worse(Decimal("1.1"), Decimal("1"), Decimal("0.05"), False)
    assert meets_floor(Decimal("0.5"), Decimal("0.5"), True) and not meets_floor(
        Decimal("0.4"), Decimal("0.5"), True
    )
    assert meets_floor(Decimal("3"), Decimal("3"), False) and not meets_floor(
        Decimal("4"), Decimal("3"), False
    )


# Sin base: solo vara nueva contra los pisos
def test_no_base_passes_when_every_metric_meets_its_floor() -> None:
    cand = base_yardstick()
    runs = GateRuns(
        base_on_old=None,
        cand_on_old=None,
        cand_on_new=report({"quality": "0.7", "speed": "0.5", "leaks": "0"}),
    )
    verdict = evaluate_gate(None, cand, runs)
    assert verdict.status == "passed"
    assert {i.metric_id for i in verdict.items} >= {"quality", "speed", "leaks", *PLATFORM_GUARDRAILS}


# T-EVAL-13
def test_no_base_fails_when_a_floor_is_missing_or_not_met() -> None:
    no_floor = yardstick([metric("quality")], [scenario("s1")], {"quality": thr("0.05")})
    runs = GateRuns(base_on_old=None, cand_on_old=None, cand_on_new=report({"quality": "0.9"}))
    verdict = evaluate_gate(None, no_floor, runs)
    assert verdict.status == "failed" and failed(verdict) == ["quality"]
    below = GateRuns(
        base_on_old=None,
        cand_on_old=None,
        cand_on_new=report({"quality": "0.1", "speed": "0.5", "leaks": "0"}),
    )
    assert failed(evaluate_gate(None, base_yardstick(), below)) == ["quality"]


def test_lower_is_better_floor_is_a_ceiling() -> None:
    cand = yardstick(
        [metric("leaks", role="guardrail", higher=False)], [scenario("s1")], {"leaks": thr("0", "2")}
    )
    over = GateRuns(base_on_old=None, cand_on_old=None, cand_on_new=report({"leaks": "3"}))
    under = GateRuns(base_on_old=None, cand_on_old=None, cand_on_new=report({"leaks": "2"}))
    assert failed(evaluate_gate(None, cand, over)) == ["leaks"]
    assert evaluate_gate(None, cand, under).status == "passed"


# T-EVAL-05: un guardarraíl que empeora falla el gate aunque una métrica gate mejore
def test_a_worse_guardrail_fails_even_if_a_gate_metric_improves() -> None:
    base = base_yardstick()
    runs = GateRuns(
        base_on_old=report({"quality": "0.6", "speed": "0.5", "leaks": "0"}),
        cand_on_old=report({"quality": "0.9", "speed": "0.5", "leaks": "1"}),
        cand_on_new=report({"quality": "0.9", "speed": "0.5", "leaks": "1"}),
    )
    verdict = evaluate_gate(base, base, runs)
    # La métrica `leaks` no cambió respecto de la base: solo la juzga la vara vieja, así que falla una vez.
    assert verdict.status == "failed" and failed(verdict) == ["leaks"]


# T-EVAL-06: cada métrica gate por separado; el ruido se tolera
def test_each_gate_metric_is_judged_on_its_own() -> None:
    base = base_yardstick()
    old_base = report({"quality": "0.80", "speed": "0.50", "leaks": "0"})
    within_noise = report({"quality": "0.76", "speed": "0.45", "leaks": "0"})
    regressed = report({"quality": "0.95", "speed": "0.20", "leaks": "0"})
    ok = evaluate_gate(
        base, base, GateRuns(base_on_old=old_base, cand_on_old=within_noise, cand_on_new=within_noise)
    )
    bad = evaluate_gate(
        base, base, GateRuns(base_on_old=old_base, cand_on_old=regressed, cand_on_new=regressed)
    )
    assert ok.status == "passed"
    assert bad.status == "failed" and "speed" in failed(bad) and "quality" not in failed(bad)


# T-EVAL-07: la vara vieja sigue vigente aunque la propuesta cambie o borre métricas
def test_the_base_yardstick_still_applies_when_the_candidate_drops_or_loosens_it() -> None:
    base = base_yardstick()
    cand = yardstick(
        [metric("quality", role="monitor")], [scenario("s1")], {}
    )  # borra speed y leaks, degrada quality
    old_base = report({"quality": "0.80", "speed": "0.50", "leaks": "0"})
    cand_old = report({"quality": "0.30", "speed": "0.10", "leaks": "0"})
    verdict = evaluate_gate(
        base, cand, GateRuns(base_on_old=old_base, cand_on_old=cand_old, cand_on_new=cand_old)
    )
    assert verdict.status == "failed"
    assert {"quality", "speed"} <= set(failed(verdict))


# Review Focus 1: una métrica sin valor es un fallo, no un pase
def test_a_metric_without_value_fails() -> None:
    base = base_yardstick()
    old_base = report({"quality": "0.8", "speed": "0.5", "leaks": "0"})
    cand_old = EvalReport(
        metrics={pid: Decimal(0) for pid in PLATFORM_GUARDRAILS} | {"leaks": Decimal(0)},
        scenarios={"s1": True},
    )  # faltan quality y speed
    verdict = evaluate_gate(
        base, base, GateRuns(base_on_old=old_base, cand_on_old=cand_old, cand_on_new=cand_old)
    )
    assert verdict.status == "failed" and {"quality", "speed"} <= set(failed(verdict))
    missing_base = EvalReport(metrics={"leaks": Decimal(0)}, scenarios={"s1": True})
    v2 = evaluate_gate(
        base, base, GateRuns(base_on_old=missing_base, cand_on_old=old_base, cand_on_new=old_base)
    )
    assert "quality" in failed(v2)


def test_a_scenario_that_passed_in_the_base_and_fails_in_the_candidate_fails() -> None:
    base = base_yardstick()
    good = report({"quality": "0.8", "speed": "0.5", "leaks": "0"}, {"s1": True})
    broken = report({"quality": "0.8", "speed": "0.5", "leaks": "0"}, {"s1": False})
    verdict = evaluate_gate(base, base, GateRuns(base_on_old=good, cand_on_old=broken, cand_on_new=broken))
    assert "scenario/s1" in failed(verdict)
    assert verdict.status == "failed"


GOOD = {"quality": "0.8", "speed": "0.5", "leaks": "0"}


def _scenario_verdict(base_scenarios: dict[str, bool], cand_scenarios: dict[str, bool]) -> Any:
    base = base_yardstick()
    old_base = report(GOOD, base_scenarios)
    cand_old = report(GOOD, cand_scenarios)
    new = report(GOOD, {"s1": True})
    return evaluate_gate(base, base, GateRuns(base_on_old=old_base, cand_on_old=cand_old, cand_on_new=new))


@pytest.mark.parametrize(
    ("base_scenarios", "cand_scenarios", "expected"),
    [
        ({"s1": True}, {"s1": True}, []),
        ({"s1": True}, {"s1": False}, ["scenario/s1"]),
        ({"s1": True}, {}, ["scenario/s1"]),  # la candidata no midió el escenario
        ({}, {"s1": False}, ["scenario/s1"]),  # la base no midió el escenario: falla cerrado
        ({}, {}, ["scenario/s1"]),
        ({"s1": False}, {"s1": False}, []),  # fallaba ya en la base: no es regresión
        ({"s1": False}, {}, []),  # un False explícito en la base exime
    ],
)
def test_scenario_regression_fails_closed_when_unmeasured(
    base_scenarios: dict[str, bool], cand_scenarios: dict[str, bool], expected: list[str]
) -> None:
    assert failed(_scenario_verdict(base_scenarios, cand_scenarios)) == expected


# Una métrica cuya identidad cambia conserva el id pero se juzga en la vara nueva
def test_a_metric_whose_identity_changed_is_judged_on_the_new_yardstick() -> None:
    base = base_yardstick()
    old = report(GOOD)

    def candidate(floor: str | None, **over: Any) -> Any:
        return yardstick(
            [metric("quality", **over), metric("speed"), metric("leaks", role="guardrail", higher=False)],
            [scenario("s1")],
            {"quality": thr("0.05", floor), "speed": thr("0.1", "0.4"), "leaks": thr("0", "0")},
        )

    def verdict(cand: Any, quality: str) -> Any:
        new = report({**GOOD, "quality": quality})
        return evaluate_gate(base, cand, GateRuns(base_on_old=old, cand_on_old=old, cand_on_new=new))

    for change in ({"event": "engine.run_closed"}, {"higher": False}):
        higher = change.get("higher", True)
        ok = verdict(candidate("0.5", **change), "0.6" if higher else "0.3")
        assert ok.status == "passed"
        phases = {(i.metric_id, i.phase) for i in ok.items}
        assert ("quality", "new_yardstick") in phases
        below = verdict(candidate("0.5", **change), "0.4" if higher else "0.9")
        assert failed(below) == ["quality"]
        no_floor = verdict(candidate(None, **change), "0.8")
        assert failed(no_floor) == ["quality"]


def test_a_guardrail_with_a_declared_noise_margin_still_has_zero_tolerance() -> None:
    base = yardstick(
        [metric("leaks", role="guardrail", higher=False)], [scenario("s1")], {"leaks": thr("0.5", "0")}
    )
    old_base = report({"leaks": "0"})
    slightly_worse = report({"leaks": "0.3"})
    verdict = evaluate_gate(
        base, base, GateRuns(base_on_old=old_base, cand_on_old=slightly_worse, cand_on_new=slightly_worse)
    )
    item = next(i for i in verdict.items if i.metric_id == "leaks" and i.phase == "base_yardstick")
    assert not item.passed and item.threshold == Decimal(0)
    assert failed(verdict) == ["leaks"]


# Vara nueva: métricas y escenarios nuevos o modificados
def test_new_metrics_and_scenarios_must_meet_the_new_yardstick() -> None:
    base = base_yardstick()
    cand = yardstick(
        [
            metric("quality"),
            metric("speed"),
            metric("leaks", role="guardrail", higher=False),
            metric("fresh"),
        ],
        [scenario("s1"), scenario("s_new")],
        {
            "quality": thr("0.05", "0.5"),
            "speed": thr("0.1", "0.4"),
            "leaks": thr("0", "0"),
            "fresh": thr("0", "0.5"),
        },
    )
    old = report({"quality": "0.8", "speed": "0.5", "leaks": "0"})
    ok_new = report(
        {"quality": "0.8", "speed": "0.5", "leaks": "0", "fresh": "0.6"}, {"s1": True, "s_new": True}
    )
    bad_new = report(
        {"quality": "0.8", "speed": "0.5", "leaks": "0", "fresh": "0.1"}, {"s1": True, "s_new": False}
    )
    assert (
        evaluate_gate(base, cand, GateRuns(base_on_old=old, cand_on_old=old, cand_on_new=ok_new)).status
        == "passed"
    )
    verdict = evaluate_gate(base, cand, GateRuns(base_on_old=old, cand_on_old=old, cand_on_new=bad_new))
    assert verdict.status == "failed" and {"fresh", "scenario/s_new"} <= set(failed(verdict))


def test_a_new_metric_without_floor_fails() -> None:
    base = base_yardstick()
    cand = yardstick(
        [
            metric("quality"),
            metric("speed"),
            metric("leaks", role="guardrail", higher=False),
            metric("fresh"),
        ],
        [scenario("s1")],
        {
            "quality": thr("0.05", "0.5"),
            "speed": thr("0.1", "0.4"),
            "leaks": thr("0", "0"),
            "fresh": thr("0"),
        },
    )
    old = report({"quality": "0.8", "speed": "0.5", "leaks": "0", "fresh": "1"})
    assert failed(evaluate_gate(base, cand, GateRuns(base_on_old=old, cand_on_old=old, cand_on_new=old))) == [
        "fresh"
    ]


# Guardarraíles de plataforma
def test_platform_guardrails_must_be_zero() -> None:
    runs = GateRuns(
        base_on_old=None,
        cand_on_old=None,
        cand_on_new=report(
            {"quality": "0.9", "speed": "0.9", "leaks": "0"}, platform=ZEROS | {"platform_pii_leak": "1"}
        ),
    )
    verdict = evaluate_gate(None, base_yardstick(), runs)
    assert verdict.status == "failed" and failed(verdict) == ["platform_pii_leak"]


def test_a_measured_pii_leak_on_the_candidate_old_suite_fails_even_if_not_worse_than_base() -> None:
    base = base_yardstick()
    leaky = report(GOOD, platform=ZEROS | {"platform_pii_leak": "2"})
    clean_new = report(GOOD)  # la candidata quitó de su suite nueva el escenario que filtraba
    verdict = evaluate_gate(base, base, GateRuns(base_on_old=leaky, cand_on_old=leaky, cand_on_new=clean_new))
    assert verdict.status == "failed" and failed(verdict) == ["platform_pii_leak"]
    item = next(i for i in verdict.items if i.metric_id == "platform_pii_leak")
    assert item.base_value == Decimal(2)


def test_a_platform_guardrail_that_was_not_measured_fails() -> None:
    partial = {pid: "0" for pid in PLATFORM_GUARDRAILS[:-1]}
    runs = GateRuns(
        base_on_old=None,
        cand_on_old=None,
        cand_on_new=report({"quality": "0.9", "speed": "0.9", "leaks": "0"}, platform=partial),
    )
    assert failed(evaluate_gate(None, base_yardstick(), runs)) == [PLATFORM_GUARDRAILS[-1]]


# T-EVAL-15
@pytest.mark.parametrize("which", ["base_on_old", "cand_on_old", "cand_on_new"])
def test_failed_infra_is_not_a_verdict(which: str) -> None:
    base = base_yardstick()
    ok = report({"quality": "0.8", "speed": "0.5", "leaks": "0"})
    broken = EvalReport(status="failed_infra", metrics={}, scenarios={})
    runs = {"base_on_old": ok, "cand_on_old": ok, "cand_on_new": ok} | {which: broken}
    verdict = evaluate_gate(base, base, GateRuns(**runs))
    assert verdict.status == "failed_infra" and verdict.items == []


def test_a_base_without_its_runs_is_a_programming_error() -> None:
    with pytest.raises(ValueError):
        evaluate_gate(
            base_yardstick(),
            base_yardstick(),
            GateRuns(base_on_old=None, cand_on_old=None, cand_on_new=report({})),
        )


def test_items_carry_value_base_and_threshold() -> None:
    base = base_yardstick()
    old_base = report({"quality": "0.80", "speed": "0.50", "leaks": "0"})
    cand_old = report({"quality": "0.85", "speed": "0.50", "leaks": "0"})
    verdict = evaluate_gate(
        base, base, GateRuns(base_on_old=old_base, cand_on_old=cand_old, cand_on_new=cand_old)
    )
    item = next(i for i in verdict.items if i.metric_id == "quality" and i.phase == "base_yardstick")
    assert (item.value, item.base_value, item.threshold, item.role) == (
        Decimal("0.85"),
        Decimal("0.80"),
        Decimal("0.05"),
        "gate",
    )


@pytest.mark.parametrize("which", ["base_on_old", "cand_on_old"])
def test_a_platform_guardrail_unmeasured_on_the_old_suite_fails_with_a_base(which: str) -> None:
    base = base_yardstick()
    full = report({"quality": "0.8", "speed": "0.5", "leaks": "0"})
    gap = PLATFORM_GUARDRAILS[-1]
    partial = EvalReport(metrics={k: v for k, v in full.metrics.items() if k != gap}, scenarios={"s1": True})
    reports = {"base_on_old": full, "cand_on_old": full} | {which: partial}
    verdict = evaluate_gate(base, base, GateRuns(**reports, cand_on_new=full))
    assert verdict.status == "failed" and failed(verdict) == [gap]
