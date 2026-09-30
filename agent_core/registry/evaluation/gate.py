"""Veredicto del gate (spec §6.4, ADR 0018): guardarraíles con tolerancia cero y métrica principal."""

from decimal import Decimal

from agent_core.registry.evaluation.report import MetricCheck, SuiteMetrics, Verdict
from agent_core.registry.evaluation.scoring import GUARDRAILS
from agent_core.registry.suite import EvalSuite


def decide(
    suite: EvalSuite, candidate: SuiteMetrics, base: SuiteMetrics | None
) -> tuple[Verdict, list[MetricCheck]]:
    checks: list[MetricCheck] = []
    for name in GUARDRAILS:
        value = candidate.guardrails.get(name, 0)
        limit = base.guardrails.get(name, 0) if base is not None else 0
        checks.append(MetricCheck(name=name, value=Decimal(value),
                                  base=Decimal(limit) if base is not None else None,
                                  threshold=Decimal(limit), passed=value <= limit))
    if base is not None:
        threshold = base.primary - suite.noise_margin
        checks.append(MetricCheck(name="primary", value=candidate.primary, base=base.primary,
                                  threshold=threshold, passed=candidate.primary >= threshold))
    else:
        checks.append(MetricCheck(name="primary", value=candidate.primary, base=None, threshold=suite.floor,
                                  passed=candidate.primary >= suite.floor))
    return ("pass" if all(c.passed for c in checks) else "fail"), checks
