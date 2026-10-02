"""Evaluación del registry.

- Evaluación por agente sobre escenarios (spec del registry §6): `EvalPort`, `ScenarioEvaluator`, sandbox y
  el reporte que guarda el registry (`agent_core.registry.evaluation.report`).
- ADR 0020: suite, aflojamientos de la vara y veredicto del gate con doble vara (`evaluate_gate`). Funciones
  puras y sin Postgres.

`EvalReport` y `Verdict` en este paquete son los del gate del ADR 0020 (`gate`); los del reporte de
`EvalPort` se importan de `agent_core.registry.evaluation.report`.
"""

from agent_core.registry.evaluation.evaluator import ScenarioEvaluator
from agent_core.registry.evaluation.gate import (
    EvalReport,
    GateItem,
    GateRuns,
    Verdict,
    evaluate_gate,
    meets_floor,
    not_worse,
)
from agent_core.registry.evaluation.local_sandbox import LocalSandbox
from agent_core.registry.evaluation.platform import PLATFORM_GUARDRAILS
from agent_core.registry.evaluation.ports import (
    EvalPort,
    EvalTarget,
    HarnessUnavailable,
    Judge,
    SandboxHandle,
    SandboxPort,
    ScenarioHarness,
    ScenarioTranscript,
)
from agent_core.registry.evaluation.report import (
    JudgeNote,
    MetricCheck,
    RunScore,
    ScenarioResult,
    SuiteMetrics,
)
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
    "EvalPort",
    "EvalReport",
    "EvalSuite",
    "EvalTarget",
    "GateItem",
    "GateRuns",
    "HarnessUnavailable",
    "Judge",
    "JudgeNote",
    "LocalSandbox",
    "MetricCheck",
    "MetricThreshold",
    "RunScore",
    "SandboxHandle",
    "SandboxPort",
    "Scenario",
    "ScenarioEvaluator",
    "ScenarioHarness",
    "ScenarioResult",
    "ScenarioTranscript",
    "ScriptedSource",
    "SuiteMetrics",
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
