"""A release's evaluation yardstick and what loosens it (ADR 0020, evaluation spec section 6.2).

Only what loosens the yardstick is flagged. Tightening (adding scenarios or metrics, raising floors,
lowering margins, promoting roles, raising repetitions) is not flagged. Only `gate` and `guardrail` metrics
are part of the yardstick.
"""

from enum import StrEnum

from agent_core.domain import MetricDef, Model
from agent_core.registry.suite import DatasetScenario, EvalSuite, MetricThreshold, Scenario

_RANK = {"monitor": 1, "gate": 2, "guardrail": 3}


class Yardstick(Model):
    """Metric definitions and suite a release is measured with: the base's or the candidate's.

    `suite` is None if the base release recorded none (empty `eval_suite_refs`): then the old yardstick
    cannot be run and the gate applies the new yardstick's floors (decision D3)."""

    metrics: list[MetricDef]
    suite: EvalSuite | None = None


class YardstickChangeKind(StrEnum):
    metric_removed = "metric_removed"
    role_degraded = "role_degraded"
    metric_changed = "metric_changed"
    threshold_removed = "threshold_removed"
    floor_loosened = "floor_loosened"
    noise_widened = "noise_widened"
    scenario_removed = "scenario_removed"
    scenario_changed = "scenario_changed"
    repetitions_lowered = "repetitions_lowered"


class YardstickChange(Model):
    kind: YardstickChangeKind
    target: str
    message: str


def metric_identity(metric: MetricDef) -> tuple[str, bool, object]:
    """What of a metric counts as yardstick: role, direction and expression (not description or alert)."""
    return (metric.role, metric.higher_is_better, metric.expr)


def _gating(metrics: list[MetricDef]) -> dict[str, MetricDef]:
    return {m.id: m for m in metrics if m.role in ("gate", "guardrail")}


def _floor_loosened(base: MetricThreshold, cand: MetricThreshold, higher_is_better: bool) -> bool:
    if base.floor is None:
        return False
    if cand.floor is None:
        return True
    return cand.floor < base.floor if higher_is_better else cand.floor > base.floor


def _change(kind: YardstickChangeKind, target: str, message: str) -> YardstickChange:
    return YardstickChange(kind=kind, target=target, message=message)


def _metric_changes(base: Yardstick, cand: Yardstick) -> list[YardstickChange]:
    found: list[YardstickChange] = []
    cand_all = {m.id: m for m in cand.metrics}
    cand_thresholds = cand.suite.thresholds if cand.suite is not None else {}
    for metric_id, base_metric in sorted(_gating(base.metrics).items()):
        cand_metric = cand_all.get(metric_id)
        if cand_metric is None:
            found.append(_change(YardstickChangeKind.metric_removed, metric_id,
                                 f"la métrica {metric_id} ya no está declarada"))
            continue
        if _RANK[cand_metric.role] < _RANK[base_metric.role]:
            found.append(_change(YardstickChangeKind.role_degraded, metric_id,
                                 f"{metric_id} pasa de {base_metric.role} a {cand_metric.role}"))
            continue
        # Only the definition counts here (direction and expression), with or without a role promotion.
        if metric_identity(cand_metric)[1:] != metric_identity(base_metric)[1:]:
            found.append(_change(YardstickChangeKind.metric_changed, metric_id,
                                 f"cambió la expresión o la dirección de {metric_id}"))
            continue
        if base.suite is None:
            continue  # the base measured with no suite: there is no threshold to loosen (D3)
        base_thr, cand_thr = base.suite.thresholds.get(metric_id), cand_thresholds.get(metric_id)
        if base_thr is None:
            # With no threshold in the base the gate measured with margin 0 and no floor.
            if cand_thr is not None and cand_thr.noise_margin > 0:
                found.append(_change(YardstickChangeKind.noise_widened, metric_id,
                                     f"el margen de ruido de {metric_id} aumentó"))
            continue
        if cand_thr is None:
            found.append(_change(YardstickChangeKind.threshold_removed, metric_id,
                                 f"{metric_id} ya no tiene umbral en la suite"))
            continue
        if _floor_loosened(base_thr, cand_thr, base_metric.higher_is_better):
            found.append(_change(YardstickChangeKind.floor_loosened, metric_id,
                                 f"el piso de {metric_id} se aflojó"))
        if cand_thr.noise_margin > base_thr.noise_margin:
            found.append(_change(YardstickChangeKind.noise_widened, metric_id,
                                 f"el margen de ruido de {metric_id} aumentó"))
    return found


def _same_except_runs(a: Scenario | DatasetScenario, b: Scenario | DatasetScenario) -> bool:
    return a.model_copy(update={"repetitions": None}) == b.model_copy(update={"repetitions": None})


def _scenario_changes(base: Yardstick, cand: Yardstick) -> list[YardstickChange]:
    base_suite, cand_suite = base.suite, cand.suite
    if base_suite is None:
        return []
    cand_by_id = {s.id: s for s in cand_suite.scenarios} if cand_suite is not None else {}
    found: list[YardstickChange] = []
    for scenario in sorted(base_suite.scenarios, key=lambda s: s.id):
        other = cand_by_id.get(scenario.id)
        if other is None or cand_suite is None:
            found.append(_change(YardstickChangeKind.scenario_removed, scenario.id,
                                 f"el escenario {scenario.id} ya no está en la suite"))
            continue
        if not _same_except_runs(scenario, other):
            found.append(_change(YardstickChangeKind.scenario_changed, scenario.id,
                                 f"cambió el escenario: {scenario.id}"))
        elif cand_suite.repetitions_of(other) < base_suite.repetitions_of(scenario):
            found.append(_change(YardstickChangeKind.repetitions_lowered, scenario.id,
                                 f"bajaron las repeticiones: {scenario.id}"))
    return found


def classify_yardstick_change(base: Yardstick | None, cand: Yardstick) -> list[YardstickChange]:
    """The changes that loosen `cand`'s yardstick against `base`. With no base there is nothing to loosen."""
    if base is None:
        return []
    found = [*_metric_changes(base, cand), *_scenario_changes(base, cand)]
    return sorted(found, key=lambda c: (c.kind.value, c.target))
