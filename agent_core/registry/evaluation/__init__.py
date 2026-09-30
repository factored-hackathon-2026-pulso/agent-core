"""Evaluación por agente sobre escenarios (spec §6)."""

from agent_core.registry.evaluation.evaluator import ScenarioEvaluator
from agent_core.registry.evaluation.local_sandbox import LocalSandbox
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
    EvalReport,
    JudgeNote,
    MetricCheck,
    RunScore,
    ScenarioResult,
    SuiteMetrics,
    Verdict,
)

__all__ = [
    "EvalPort", "EvalReport", "EvalTarget", "HarnessUnavailable", "Judge", "JudgeNote", "LocalSandbox",
    "MetricCheck", "RunScore", "SandboxHandle", "SandboxPort", "ScenarioEvaluator", "ScenarioHarness",
    "ScenarioResult", "ScenarioTranscript", "SuiteMetrics", "Verdict",
]
