"""Gate verdict (ADR 0020, evaluation spec section 6): double yardstick, no composite score."""

from decimal import Decimal

from agent_core.domain import MetricDef
from agent_core.registry.evaluation.report import GateItem, GateRuns, SuiteMeasurement, Verdict
from agent_core.registry.evaluation.scoring import PLATFORM_GUARDRAILS
from agent_core.registry.evaluation.yardstick import Yardstick, metric_identity
from agent_core.registry.suite import EvalSuite


def not_worse(candidate: Decimal, base: Decimal, margin: Decimal, higher_is_better: bool) -> bool:
    """The candidate is not worse than the base, with `margin` tolerance in the metric's direction."""
    return candidate >= base - margin if higher_is_better else candidate <= base + margin


def meets_floor(value: Decimal, floor: Decimal, higher_is_better: bool) -> bool:
    """The floor is a minimum if higher is better and a maximum if lower is better."""
    return value >= floor if higher_is_better else value <= floor


def _gating(metrics: list[MetricDef]) -> list[MetricDef]:
    return sorted((m for m in metrics if m.role in ("gate", "guardrail")), key=lambda m: m.id)


def _base_items(metrics: list[MetricDef], suite: EvalSuite, base_run: SuiteMeasurement,
                cand_run: SuiteMeasurement) -> list[GateItem]:
    items: list[GateItem] = []
    for metric in _gating(metrics):
        threshold = suite.thresholds.get(metric.id)
        margin = Decimal(0) if metric.role == "guardrail" or threshold is None else threshold.noise_margin
        base_value, value = base_run.metrics.get(metric.id), cand_run.metrics.get(metric.id)
        if base_value is None or value is None:
            items.append(GateItem(metric_id=metric.id, phase="base_yardstick", role=metric.role, value=value,
                                  base_value=base_value, noise_margin=margin, passed=False,
                                  reason="la métrica no se pudo calcular en la base o en la candidata"))
            continue
        ok = not_worse(value, base_value, margin, metric.higher_is_better)
        items.append(GateItem(metric_id=metric.id, phase="base_yardstick", role=metric.role, value=value,
                              base_value=base_value, noise_margin=margin, passed=ok,
                              reason="" if ok else "empeora frente a la base"))
    for scenario in sorted(suite.scenarios, key=lambda s: s.id):
        before, after = base_run.scenarios.get(scenario.id), cand_run.scenarios.get(scenario.id)
        if before is False:
            continue  # already failing in the base: not a regression
        if before is None or after is None:
            reason = "el escenario no se midió en la base o en la candidata"
        elif after is not True:
            reason = "el escenario pasaba en la base y falla en la candidata"
        else:
            continue
        items.append(GateItem(metric_id=f"scenario/{scenario.id}", phase="base_yardstick", passed=False,
                              reason=reason))
    return items


def _new_items(old: Yardstick | None, metrics: list[MetricDef], suite: EvalSuite,
               run: SuiteMeasurement) -> list[GateItem]:
    items: list[GateItem] = []
    base_metrics = {m.id: m for m in _gating(old.metrics)} if old is not None else {}
    base_scenarios = {s.id: s for s in old.suite.scenarios} if old is not None and old.suite else {}
    for metric in _gating(metrics):
        previous = base_metrics.get(metric.id)
        if previous is not None and metric_identity(previous) == metric_identity(metric):
            # Unchanged: the old yardstick already judged it. A raised floor or a lowered margin on an
            # unchanged metric is classified as tightening but not enforced here (errata A11). It must
            # still be measurable on the new suite, or the next proposal could never use it as its base.
            if run.metrics.get(metric.id) is None:
                items.append(GateItem(metric_id=metric.id, phase="new_yardstick", role=metric.role,
                                      value=None, passed=False,
                                      reason="la métrica no se pudo calcular con la suite nueva"))
            continue
        threshold = suite.thresholds.get(metric.id)
        floor = threshold.floor if threshold else None
        value = run.metrics.get(metric.id)
        if floor is None:
            ok, reason = False, "la métrica es nueva o cambió y no declara piso"
        elif value is None:
            ok, reason = False, "la métrica no se pudo calcular"
        else:
            ok = meets_floor(value, floor, metric.higher_is_better)
            reason = "" if ok else "no alcanza el piso"
        items.append(GateItem(metric_id=metric.id, phase="new_yardstick", role=metric.role, value=value,
                              floor=floor, passed=ok, reason=reason))
    for scenario in sorted(suite.scenarios, key=lambda s: s.id):
        if base_scenarios.get(scenario.id) == scenario:
            continue
        ok = run.scenarios.get(scenario.id) is True
        items.append(GateItem(metric_id=f"scenario/{scenario.id}", phase="new_yardstick", passed=ok,
                              reason="" if ok else "alguna expectativa o aserción no se cumple"))
    return items


def _platform_items(runs: GateRuns) -> list[GateItem]:
    items: list[GateItem] = []
    has_base = runs.base_on_old is not None and runs.cand_on_old is not None
    for guardrail in PLATFORM_GUARDRAILS:
        value = runs.cand_on_new.metrics.get(guardrail)
        base_value = runs.base_on_old.metrics.get(guardrail) if runs.base_on_old else None
        old_value = runs.cand_on_old.metrics.get(guardrail) if runs.cand_on_old else None
        if value is None:
            ok, reason = False, "el guardarraíl de plataforma no se midió"
        elif has_base and (base_value is None or old_value is None):
            ok, reason = False, "el guardarraíl de plataforma no se midió en la suite vieja"
        elif value != 0 or (has_base and old_value != 0):
            ok, reason = False, "el guardarraíl de plataforma debe valer 0"
        else:
            ok, reason = True, ""
        items.append(GateItem(metric_id=guardrail, phase="platform", role="guardrail", value=value,
                              base_value=base_value, floor=Decimal(0), passed=ok, reason=reason))
    return items


def evaluate_gate(base: Yardstick | None, cand: Yardstick, runs: GateRuns) -> tuple[Verdict, list[GateItem]]:
    """Verdict of the candidate against its base (if any). `failed_infra` is neither pass nor fail.

    With `base.suite = None` (the base recorded no suite, D3) only the new yardstick and the platform apply.

    Preconditions this function does NOT check (the caller guarantees them): empty `suite_problems` for
    `cand`'s suite; unique metric ids (`MT-03`); and that each measurement matches its suite and release:
    `base_on_old` is the base with the old suite, `cand_on_old` the candidate with the old suite and
    `cand_on_new` the candidate with the new suite (`GateRuns` carries neither the suite nor the
    `candidate_hash`)."""
    measured = [r for r in (runs.base_on_old, runs.cand_on_old, runs.cand_on_new) if r is not None]
    if any(r.status == "failed_infra" for r in measured):
        return "failed_infra", []
    if cand.suite is None:
        raise ValueError("the new yardstick needs its suite")
    old = base if base is not None and base.suite is not None else None
    items: list[GateItem] = []
    if old is not None and old.suite is not None:
        if runs.base_on_old is None or runs.cand_on_old is None:
            raise ValueError("with an old yardstick both `base_on_old` and `cand_on_old` are required")
        items += _base_items(old.metrics, old.suite, runs.base_on_old, runs.cand_on_old)
    items += _new_items(old, cand.metrics, cand.suite, runs.cand_on_new)
    items += _platform_items(runs)
    return ("pass" if all(item.passed for item in items) else "fail"), items
