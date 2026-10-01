"""Veredicto del gate de evaluación con doble vara (ADR 0020, spec de evaluación §6).

Función pura: recibe las definiciones y los reportes ya medidos (los produce `EvalPort`, unidad 6) y decide.
Sin puntaje compuesto: cada métrica se juzga por separado y basta una que falle.
"""

from decimal import Decimal
from typing import Literal

from pydantic import Field

from agent_core.domain import MetricDef, Model
from agent_core.domain.metrics import MetricRole
from agent_core.registry.evaluation.platform import PLATFORM_GUARDRAILS
from agent_core.registry.evaluation.yardstick import Yardstick, metric_identity

Phase = Literal["base_yardstick", "new_yardstick", "platform"]


class EvalReport(Model):
    """Lo que devuelve `EvalPort.run`: valor por métrica (incluidos los `platform_*`) y pase por escenario."""

    status: Literal["ok", "failed_infra"] = "ok"
    metrics: dict[str, Decimal] = Field(default_factory=dict)
    scenarios: dict[str, bool] = Field(default_factory=dict)


class GateRuns(Model):
    """Las mediciones que necesita el gate. `base_on_old` y `cand_on_old` son obligatorias si hay base."""

    base_on_old: EvalReport | None = None  # vara vieja medida sobre la base
    cand_on_old: EvalReport | None = None  # vara vieja medida sobre la candidata
    cand_on_new: EvalReport  # vara nueva medida sobre la candidata


class GateItem(Model):
    metric_id: str
    phase: Phase
    role: MetricRole | None = None
    value: Decimal | None = None
    base_value: Decimal | None = None
    threshold: Decimal | None = None
    passed: bool
    reason: str = ""


class Verdict(Model):
    status: Literal["passed", "failed", "failed_infra"]
    items: list[GateItem]


def not_worse(candidate: Decimal, base: Decimal, margin: Decimal, higher_is_better: bool) -> bool:
    """La candidata no es peor que la base, con `margin` de tolerancia en la dirección de la métrica."""
    return candidate >= base - margin if higher_is_better else candidate <= base + margin


def meets_floor(value: Decimal, floor: Decimal, higher_is_better: bool) -> bool:
    """El piso es un mínimo si mayor es mejor y un máximo si menor es mejor."""
    return value >= floor if higher_is_better else value <= floor


def _gating(metrics: list[MetricDef]) -> list[MetricDef]:
    return sorted((m for m in metrics if m.role in ("gate", "guardrail")), key=lambda m: m.id)


def _base_items(base: Yardstick, base_run: EvalReport, cand_run: EvalReport) -> list[GateItem]:
    items: list[GateItem] = []
    for metric in _gating(base.metrics):
        threshold = base.suite.thresholds.get(metric.id)
        margin = Decimal(0) if metric.role == "guardrail" or threshold is None else threshold.noise_margin
        base_value, value = base_run.metrics.get(metric.id), cand_run.metrics.get(metric.id)
        if base_value is None or value is None:
            items.append(
                GateItem(
                    metric_id=metric.id,
                    phase="base_yardstick",
                    role=metric.role,
                    value=value,
                    base_value=base_value,
                    threshold=margin,
                    passed=False,
                    reason="la métrica no se pudo calcular en la base o en la candidata",
                )
            )
            continue
        ok = not_worse(value, base_value, margin, metric.higher_is_better)
        items.append(
            GateItem(
                metric_id=metric.id,
                phase="base_yardstick",
                role=metric.role,
                value=value,
                base_value=base_value,
                threshold=margin,
                passed=ok,
                reason="" if ok else "empeora frente a la base",
            )
        )
    for scenario in sorted(base.suite.scenarios, key=lambda s: s.id):
        before, after = base_run.scenarios.get(scenario.id), cand_run.scenarios.get(scenario.id)
        if before is False:
            continue  # ya fallaba en la base: no es una regresión
        if before is None or after is None:
            reason = "el escenario no se midió en la base o en la candidata"
        elif after is not True:
            reason = "el escenario pasaba en la base y falla en la candidata"
        else:
            continue
        items.append(
            GateItem(metric_id=f"scenario/{scenario.id}", phase="base_yardstick", passed=False, reason=reason)
        )
    return items


def _new_items(base: Yardstick | None, cand: Yardstick, run: EvalReport) -> list[GateItem]:
    items: list[GateItem] = []
    base_metrics = {m.id: m for m in _gating(base.metrics)} if base else {}
    base_scenarios = {s.id: s for s in base.suite.scenarios} if base else {}
    for metric in _gating(cand.metrics):
        previous = base_metrics.get(metric.id)
        if previous is not None and metric_identity(previous) == metric_identity(metric):
            continue  # sin cambios: ya lo juzgó la vara vieja
        threshold = cand.suite.thresholds.get(metric.id)
        floor = threshold.floor if threshold else None
        value = run.metrics.get(metric.id)
        if floor is None:
            ok, reason = False, "la métrica es nueva o cambió y no declara piso"
        elif value is None:
            ok, reason = False, "la métrica no se pudo calcular"
        else:
            ok = meets_floor(value, floor, metric.higher_is_better)
            reason = "" if ok else "no alcanza el piso"
        items.append(
            GateItem(
                metric_id=metric.id,
                phase="new_yardstick",
                role=metric.role,
                value=value,
                threshold=floor,
                passed=ok,
                reason=reason,
            )
        )
    for scenario in sorted(cand.suite.scenarios, key=lambda s: s.id):
        if base_scenarios.get(scenario.id) == scenario:
            continue
        ok = run.scenarios.get(scenario.id) is True
        items.append(
            GateItem(
                metric_id=f"scenario/{scenario.id}",
                phase="new_yardstick",
                passed=ok,
                reason="" if ok else "alguna aserción del escenario no se cumple",
            )
        )
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
        items.append(
            GateItem(
                metric_id=guardrail,
                phase="platform",
                role="guardrail",
                value=value,
                base_value=base_value,
                threshold=Decimal(0),
                passed=ok,
                reason=reason,
            )
        )
    return items


def evaluate_gate(base: Yardstick | None, cand: Yardstick, runs: GateRuns) -> Verdict:
    """Veredicto de una candidata frente a su base (si existe). `failed_infra` no es pase ni fallo.

    Precondiciones que esta función NO verifica (quien la llama las garantiza):
    - `suite_problems` devolvió lista vacía para la candidata (suite completa, umbrales declarados).
    - Los ids de métrica son únicos (MT-03); `Yardstick` acepta duplicados y aquí el último gana.
    - Cada reporte se midió sobre la suite y la release correctas: `base_on_old` es la base con la suite
      vieja, `cand_on_old` la candidata con la suite vieja y `cand_on_new` la candidata con la suite nueva.
      `GateRuns` no lleva referencia a la suite ni al `candidate_hash`.
    """
    reports = [r for r in (runs.base_on_old, runs.cand_on_old, runs.cand_on_new) if r is not None]
    if any(report.status == "failed_infra" for report in reports):
        return Verdict(status="failed_infra", items=[])
    items: list[GateItem] = []
    if base is not None:
        if runs.base_on_old is None or runs.cand_on_old is None:
            raise ValueError("con release base se necesitan `base_on_old` y `cand_on_old`")
        items += _base_items(base, runs.base_on_old, runs.cand_on_old)
    items += _new_items(base, cand, runs.cand_on_new)
    items += _platform_items(runs)
    return Verdict(status="passed" if all(item.passed for item in items) else "failed", items=items)
