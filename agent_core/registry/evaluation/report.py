"""Resultado de una evaluación (spec §6.4). Cifras en `Decimal`."""

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Verdict = Literal["pass", "fail", "failed_infra"]
Label = Literal["candidate", "base"]


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class MetricCheck(_M):
    name: str
    value: Decimal
    base: Decimal | None = None
    threshold: Decimal | None = None
    passed: bool


class SuiteMetrics(_M):
    primary: Decimal
    guardrails: dict[str, int]
    runs: int


class RunScore(_M):
    passed: bool
    failures: list[str] = Field(default_factory=list)
    guardrails: dict[str, int]
    outcome: str | None = None
    escalated: bool = False


class ScenarioResult(_M):
    scenario_id: str
    label: Label
    repetition: int
    score: RunScore


class JudgeNote(_M):
    scenario_id: str
    label: Label
    note: str
    score: Decimal | None = None


class EvalReport(_M):
    verdict: Verdict
    checks: list[MetricCheck] = Field(default_factory=list)
    candidate: SuiteMetrics | None = None
    base: SuiteMetrics | None = None
    results: list[ScenarioResult] = Field(default_factory=list)
    judge_notes: list[JudgeNote] = Field(default_factory=list)
    detail: str | None = None
