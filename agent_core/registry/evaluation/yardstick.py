"""Clasificación de cambios en la vara de evaluación (ADR 0020, spec de evaluación §6.2).

Solo se marca lo que afloja la vara. Endurecer (añadir escenarios, subir pisos, promover roles, añadir
métricas) no se marca. Solo las métricas `gate` y `guardrail` forman parte de la vara.
"""

from enum import StrEnum

from agent_core.domain import MetricDef, Model
from agent_core.registry.evaluation.suite import EvalSuite, MetricThreshold

_RANK = {"monitor": 1, "gate": 2, "guardrail": 3}


class Yardstick(Model):
    """Las definiciones de métricas y la suite de una release: con lo que se mide a la siguiente."""

    metrics: list[MetricDef]
    suite: EvalSuite


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
    """Lo que de una métrica cuenta como vara: papel, dirección y expresión (no descripción ni alerta)."""
    return (metric.role, metric.higher_is_better, metric.expr)


def _gating(metrics: list[MetricDef]) -> dict[str, MetricDef]:
    return {m.id: m for m in metrics if m.role in ("gate", "guardrail")}


def _floor_loosened(
    base: MetricThreshold, cand: MetricThreshold, base_higher: bool, cand_higher: bool
) -> bool:
    if base_higher != cand_higher:
        return False  # la dirección cambió: ya se marca como `metric_changed`
    higher_is_better = base_higher
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
        # Solo la definición cuenta aquí (dirección y expresión), con o sin promoción de rol.
        if metric_identity(cand_metric)[1:] != metric_identity(base_metric)[1:]:
            found.append(_change(YardstickChangeKind.metric_changed, metric_id,
                                 f"cambió la expresión o la dirección de {metric_id}"))
            continue
        base_thr = base.suite.thresholds.get(metric_id)
        cand_thr = cand.suite.thresholds.get(metric_id)
        if base_thr is None:
            # Sin umbral en la base el gate medía con margen 0 y sin piso.
            if cand_thr is not None and cand_thr.noise_margin > 0:
                found.append(_change(YardstickChangeKind.noise_widened, metric_id,
                                     f"el margen de ruido de {metric_id} aumentó"))
            continue
        if cand_thr is None:
            found.append(_change(YardstickChangeKind.threshold_removed, metric_id,
                                 f"{metric_id} ya no tiene umbral en la suite"))
            continue
        if _floor_loosened(base_thr, cand_thr, base_metric.higher_is_better, cand_metric.higher_is_better):
            found.append(_change(YardstickChangeKind.floor_loosened, metric_id,
                                 f"el piso de {metric_id} se aflojó"))
        if cand_thr.noise_margin > base_thr.noise_margin:
            found.append(_change(YardstickChangeKind.noise_widened, metric_id,
                                 f"el margen de ruido de {metric_id} aumentó"))
    return found


def _scenario_changes(base: Yardstick, cand: Yardstick) -> list[YardstickChange]:
    found: list[YardstickChange] = []
    cand_by_id = {s.id: s for s in cand.suite.scenarios}
    for scenario in sorted(base.suite.scenarios, key=lambda s: s.id):
        other = cand_by_id.get(scenario.id)
        if other is None:
            found.append(_change(YardstickChangeKind.scenario_removed, scenario.id,
                                 f"el escenario {scenario.id} ya no está en la suite"))
        elif other != scenario:
            same_but_runs = other.model_copy(update={"repetitions": scenario.repetitions}) == scenario
            if same_but_runs and other.repetitions >= scenario.repetitions:
                continue  # solo más repeticiones: endurece
            if same_but_runs:
                kind, text = YardstickChangeKind.repetitions_lowered, "bajaron las repeticiones"
            else:
                kind, text = YardstickChangeKind.scenario_changed, "cambió el escenario"
            found.append(_change(kind, scenario.id, f"{text}: {scenario.id}"))
    return found


def classify_yardstick_change(base: Yardstick | None, cand: Yardstick) -> list[YardstickChange]:
    """Los cambios que aflojan la vara de `cand` frente a `base`. Sin base no hay nada que aflojar."""
    if base is None:
        return []
    found = [*_metric_changes(base, cand), *_scenario_changes(base, cand)]
    return sorted(found, key=lambda c: (c.kind.value, c.target))
