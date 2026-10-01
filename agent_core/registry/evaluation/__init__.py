"""Evaluación del registry (ADR 0020): suite, aflojamientos de la vara y veredicto del gate.

Funciones puras y sin Postgres; el servicio del registry y la unidad 6 (`EvalPort`) las consumen.
"""

from agent_core.registry.evaluation.gate import (
    EvalReport,
    GateItem,
    GateRuns,
    Verdict,
    evaluate_gate,
    meets_floor,
    not_worse,
)
from agent_core.registry.evaluation.platform import PLATFORM_GUARDRAILS
from agent_core.registry.evaluation.suite import (
    Assertion,
    DatasetSource,
    EvalSuite,
    MetricThreshold,
    Scenario,
    ScriptedSource,
    SuiteProblem,
    SuiteProblemCode,
    suite_problems,
)
from agent_core.registry.evaluation.yardstick import (
    Yardstick,
    YardstickChange,
    YardstickChangeKind,
    classify_yardstick_change,
    metric_identity,
)

__all__ = [
    "PLATFORM_GUARDRAILS",
    "Assertion",
    "DatasetSource",
    "EvalReport",
    "EvalSuite",
    "GateItem",
    "GateRuns",
    "MetricThreshold",
    "Scenario",
    "ScriptedSource",
    "SuiteProblem",
    "SuiteProblemCode",
    "Verdict",
    "Yardstick",
    "YardstickChange",
    "YardstickChangeKind",
    "classify_yardstick_change",
    "evaluate_gate",
    "meets_floor",
    "metric_identity",
    "not_worse",
    "suite_problems",
]
