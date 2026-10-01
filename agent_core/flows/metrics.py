"""Validación del DSL de métricas de un agente contra el catálogo cerrado de eventos (ADR 0020).

Reglas MT-01 a MT-06. Total y determinista: ninguna entrada hace que lance. Todo lo que se repite en un
mensaje o en una ruta se acota con `clip`.
"""

from agent_core.domain import (
    PLATFORM_METRIC_PREFIX,
    Agent,
    EntityKind,
    JudgeExpr,
    MetricDef,
    MetricExpr,
    ModelProfile,
    RefSpec,
    catalog_fields,
    predicate_problems,
)
from agent_core.flows.view import RegistryView
from agent_core.flows.violations import Violation, clip

_Problem = tuple[str, str, str]


def _problem(rule: str, pointer: str, text: str) -> _Problem:
    return (rule, pointer, clip(text, 200))


_NUMERIC = frozenset({"int", "decimal"})


def _expr_problems(expr: MetricExpr, at: str) -> list[_Problem]:
    fields = catalog_fields(expr.event)
    if fields is None:
        return [_problem("MT-01", f"{at}/event", f"evento {clip(expr.event, 60)} fuera del catálogo")]
    found: list[_Problem] = []
    for i, sub, text in predicate_problems(expr.event, expr.where):
        found.append(_problem("MT-02", f"{at}/where/{i}/{sub}", text))
    for i, name in enumerate(expr.group_by):
        if name not in fields:
            text = f"campo {clip(name, 60)} no existe en {clip(expr.event, 60)}"
            found.append(_problem("MT-02", f"{at}/group_by/{i}", text))
    if expr.field is not None:
        kind = fields.get(expr.field)
        if kind is None:
            text = f"campo {clip(expr.field, 60)} no existe en {clip(expr.event, 60)}"
            found.append(_problem("MT-02", f"{at}/field", text))
        elif kind not in _NUMERIC:
            text = f"{clip(expr.field, 60)} no es numérico y {expr.aggregation} lo exige"
            found.append(_problem("MT-02", f"{at}/field", text))
    if expr.denominator is not None:
        found += _expr_problems(expr.denominator, f"{at}/denominator")
    return found


def _judge_problems(expr: JudgeExpr, at: str, reg: RegistryView) -> list[_Problem]:
    found: list[_Problem] = []
    if catalog_fields(expr.target_event) is None:
        text = f"evento {clip(expr.target_event, 60)} fuera del catálogo"
        found.append(_problem("MT-04", f"{at}/target_event", text))
    ref = RefSpec.model_validate(f"{expr.judge_profile.id}@{expr.judge_profile.version}")
    if not isinstance(reg.resolve(EntityKind.model_profile, ref), ModelProfile):
        text = f"model_profile {clip(str(ref), 120)} no existe en el registro"
        found.append(_problem("MT-06", f"{at}/judge_profile", text))
    return found


def _metric_problems(metric: MetricDef, at: str, reg: RegistryView) -> list[_Problem]:
    found: list[_Problem] = []
    if metric.id.startswith(PLATFORM_METRIC_PREFIX):
        text = f"el prefijo {PLATFORM_METRIC_PREFIX} está reservado a la plataforma"
        found.append(_problem("MT-05", f"{at}/id", text))
    if isinstance(metric.expr, MetricExpr):
        found += _expr_problems(metric.expr, f"{at}/expr")
    else:
        found += _judge_problems(metric.expr, f"{at}/expr", reg)
    return found


def validate_agent_metrics(agent: Agent, where: str, reg: RegistryView) -> list[Violation]:
    """MT-01 a MT-06 sobre `agent.metrics`. `where` es el origen del agente, como en `validate_agent`."""
    found: list[_Problem] = []
    seen: set[str] = set()
    for index, metric in enumerate(agent.metrics):
        at = f"/metrics/{index}"
        if metric.id in seen:
            text = f"id de métrica duplicado: {clip(metric.id, 60)}"
            found.append(_problem("MT-03", f"{at}/id", text))
        seen.add(metric.id)
        found += _metric_problems(metric, at, reg)
    return [Violation(rule=rule, path=clip(f"{where}#{pointer}", 240), message=message)
            for rule, pointer, message in found]
