"""Result of an evaluation (registry section 6.4; evaluation spec section 6). Figures are `Decimal`; there is
no composite score."""

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from agent_core.domain.metrics import MetricRole
from agent_core.registry.evaluation.yardstick import YardstickChange

Verdict = Literal["pass", "fail", "failed_infra"]
Label = Literal["candidate", "base"]
RunName = Literal["base_on_old", "cand_on_old", "cand_on_new"]
Phase = Literal["base_yardstick", "new_yardstick", "platform"]


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


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


class RunScore(_M):
    passed: bool
    failures: list[str] = Field(default_factory=list)
    guardrails: dict[str, int]
    outcome: str | None = None
    escalated: bool = False


class ScenarioResult(_M):
    scenario_id: str
    label: Label
    run: RunName
    repetition: int
    score: RunScore


class JudgeNote(_M):
    scenario_id: str
    label: Label
    note: str
    score: Decimal | None = None


class EvalReport(_M):
    """What the approver sees: verdict, each gate item separately, the measurements, the result of each run,
    the judge's notes (informative) and what the proposal loosens in the yardstick."""

    verdict: Verdict
    items: list[GateItem] = Field(default_factory=list)
    runs: GateRuns | None = None
    results: list[ScenarioResult] = Field(default_factory=list)
    judge_notes: list[JudgeNote] = Field(default_factory=list)
    yardstick_changes: list[YardstickChange] = Field(default_factory=list)
    detail: str | None = None
