from typing import Any

import pytest
from pydantic import ValidationError

from agent_core.domain import JudgeExpr, MetricDef, MetricExpr, Predicate


def expr(**over: Any) -> MetricExpr:
    data: dict[str, Any] = {"event": "engine.escalated", "aggregation": "count", "window": "scenario"}
    return MetricExpr.model_validate(data | over)


def test_count_metric_is_valid() -> None:
    where = [{"field": "reason_code", "op": "eq", "value": "release_revoked"}]
    value = expr(where=where, group_by=["release"])
    assert value.aggregation == "count"
    assert value.where[0].value == "release_revoked"


@pytest.mark.parametrize(
    "over",
    [
        {"aggregation": "sum"},  # sum exige field
        {"aggregation": "avg"},
        {"aggregation": "percentile", "field": "duration_ms"},  # percentile exige el percentil
        {"aggregation": "count", "field": "duration_ms"},  # count no lleva field
        {"aggregation": "count", "percentile": 50},
        {"aggregation": "rate"},  # rate exige denominador
        {"aggregation": "sum", "field": "duration_ms", "percentile": 50},
    ],
)
def test_inconsistent_shapes_are_rejected(over: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        expr(**over)


def test_percentile_bounds() -> None:
    assert expr(aggregation="percentile", field="duration_ms", percentile=95).percentile == 95
    for bad in (0, 100):
        with pytest.raises(ValidationError):
            expr(aggregation="percentile", field="duration_ms", percentile=bad)


# T-EVAL-02: la ventana es obligatoria y acotada
def test_window_is_required_and_positive() -> None:
    with pytest.raises(ValidationError):
        MetricExpr.model_validate({"event": "engine.escalated", "aggregation": "count"})
    assert expr(window="PT1H").window.total_seconds() == 3600
    for bad in ("PT0S", "-PT1H", "forever"):
        with pytest.raises(ValidationError):
            expr(window=bad)


# T-EVAL-03: el DSL no tiene funciones de hora ni claves libres
@pytest.mark.parametrize("extra", [{"now": "2026-01-01"}, {"sql": "SELECT 1"}, {"since": "PT1H"}])
def test_free_keys_are_rejected(extra: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        expr(**extra)


def test_rate_needs_an_aligned_count_denominator() -> None:
    denominator = {"event": "engine.turn_completed", "aggregation": "count", "window": "scenario"}
    ok = expr(aggregation="rate", denominator=denominator)
    assert ok.denominator is not None
    with pytest.raises(ValidationError):  # ventana distinta
        expr(aggregation="rate", denominator=denominator | {"window": "run"})
    with pytest.raises(ValidationError):  # el denominador es un count
        expr(aggregation="rate", denominator=denominator | {"aggregation": "sum", "field": "duration_ms"})
    with pytest.raises(ValidationError):  # agrupación distinta
        expr(aggregation="rate", denominator=denominator | {"group_by": ["release"]})
    with pytest.raises(ValidationError):  # un denominador sin rate
        expr(denominator=denominator)


def test_predicate_shape() -> None:
    assert Predicate(field="reason_code", op="in", value=["a", "b"]).op == "in"
    for bad in ({"op": "in", "value": "a"}, {"op": "eq", "value": ["a"]}, {"op": "in", "value": []}):
        with pytest.raises(ValidationError):
            Predicate.model_validate({"field": "reason_code"} | bad)


def test_metric_def_with_judge_expr_and_roundtrip() -> None:
    data: dict[str, Any] = {
        "id": "proposal_quality", "description": "Calidad de la propuesta", "role": "gate",
        "higher_is_better": True,
        "expr": {
            "judge_profile": "juez@1.0.0", "rubric": "Califica de 0 a 1",
            "target_event": "registry.evaluated",
        },
    }
    metric = MetricDef.model_validate(data)
    assert isinstance(metric.expr, JudgeExpr)
    assert MetricDef.model_validate_json(metric.model_dump_json()) == metric


def test_metric_def_with_expr_and_alert() -> None:
    metric = MetricDef.model_validate(
        {"id": "esc_rate", "description": "d", "role": "monitor", "higher_is_better": False,
         "alert": {"breach_when": "above", "value": "0.25"},
         "expr": {"event": "engine.escalated", "aggregation": "count", "window": "scenario"}}
    )
    assert isinstance(metric.expr, MetricExpr)
    assert str(metric.alert.value if metric.alert else None) == "0.25"


@pytest.mark.parametrize("bad", ["Mayúscula", "con espacio", "", "x" * 0])
def test_metric_id_pattern(bad: str) -> None:
    with pytest.raises(ValidationError):
        MetricDef.model_validate(
            {"id": bad, "description": "d", "role": "monitor", "higher_is_better": True,
             "expr": {"event": "engine.escalated", "aggregation": "count", "window": "scenario"}}
        )


def test_alert_value_is_a_finite_decimal() -> None:
    with pytest.raises(ValidationError):
        MetricDef.model_validate(
            {"id": "m", "description": "d", "role": "monitor", "higher_is_better": True,
             "alert": {"breach_when": "above", "value": "NaN"},
             "expr": {"event": "engine.escalated", "aggregation": "count", "window": "scenario"}}
        )
