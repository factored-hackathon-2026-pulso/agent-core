"""Resultado de una evaluación (spec §6.4). Cifras en `Decimal`."""

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from agent_core.domain.metrics import MetricRole

Verdict = Literal["pass", "fail", "failed_infra"]
Label = Literal["candidate", "base"]
Phase = Literal["base_yardstick", "new_yardstick", "platform"]


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class MetricCheck(_M):
    name: str
    value: Decimal
    base: Decimal | None = None
    threshold: Decimal | None = None
    passed: bool


class GateItem(_M):
    """One element of the verdict: a metric, a scenario (`scenario/<id>`) or a platform guardrail.

    `noise_margin` is the tolerance against the base (old yardstick); `floor` is the floor (new yardstick
    and platform: a minimum if higher is better and a maximum if lower is better). There is no composite
    score."""

    metric_id: str
    phase: Phase
    role: MetricRole | None = None
    value: Decimal | None = None
    base_value: Decimal | None = None
    noise_margin: Decimal | None = None
    floor: Decimal | None = None
    passed: bool
    reason: str = ""


class SuiteMeasurement(_M):
    """What running a suite on a release measured: a value per metric (including `platform_*`) and whether
    each scenario passed. A missing key means "not measured", and the gate counts it as a failure."""

    status: Literal["ok", "failed_infra"] = "ok"
    metrics: dict[str, Decimal] = Field(default_factory=dict)
    scenarios: dict[str, bool] = Field(default_factory=dict)


class GateRuns(_M):
    """The gate's runs. The two old-yardstick runs exist only if the base recorded its suite."""

    base_on_old: SuiteMeasurement | None = None
    cand_on_old: SuiteMeasurement | None = None
    cand_on_new: SuiteMeasurement


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
