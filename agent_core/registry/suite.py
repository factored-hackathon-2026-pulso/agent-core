"""`eval_suite` format (registry §6.1, ADR 0020 §5): scenarios bound to an agent.

It lives in the registry and not in M0: it is not part of the engine release. A scenario is `scripted`
(synthetic script run on the real engine with sandboxed tools; enabled) or `dataset` (real cases by id and
hash; designed but DISABLED until a separate ADR, open topic #20). Gate thresholds are per metric
(`thresholds`).
"""

from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Discriminator, Field, PositiveInt, Tag, model_validator

from agent_core.domain import (
    Agent,
    EntityId,
    ExactVersion,
    JsonValue,
    Outcome,
    Predicate,
    ToolStatus,
    catalog_fields,
    predicate_problems,
)
from agent_core.domain.base import Sha256Hex

NonNegativeDecimal = Annotated[Decimal, Field(ge=0, allow_inf_nan=False)]
FiniteDecimal = Annotated[Decimal, Field(allow_inf_nan=False)]
Repetitions = Annotated[int, Field(ge=1, le=10)]
ScenarioId = Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9_-]*$")]


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Step(_M):
    op: Literal["start", "turn", "confirm"]
    text: str | None = Field(default=None, max_length=4000)
    answer: Literal["yes", "no"] | None = None
    lang: str | None = None
    auth: Literal["anonymous", "session", "step_up"] = "step_up"

    @model_validator(mode="after")
    def _shape(self) -> "Step":
        if self.op == "turn" and not self.text:
            raise ValueError("un paso `turn` necesita `text`")
        if self.op == "confirm" and self.answer is None:
            raise ValueError("un paso `confirm` necesita `answer`")
        return self


class ToolReply(_M):
    status: ToolStatus = ToolStatus("ok")
    result: JsonValue = None
    error: str | None = None


class SandboxSeed(_M):
    tools: dict[str, list[ToolReply]] = Field(default_factory=dict)


class Expect(_M):
    outcome: Outcome | None = None
    actions_verified: list[str] = Field(default_factory=list)
    escalated: bool | None = None


class ScenarioPrincipal(_M):
    id: str = Field(min_length=1)
    attrs: dict[str, str] = Field(default_factory=dict)


class Assertion(_M):
    """Assertion over the events of a run: an event matching the filter appears (or does not)."""

    event: str = Field(min_length=1, max_length=80)
    where: list[Predicate] = Field(default_factory=list, max_length=8)
    expect: Literal["at_least_one", "none"] = "at_least_one"


class Scenario(_M):
    """`scripted` scenario: synthetic data, tools seeded in the sandbox and the real engine."""

    id: ScenarioId
    source: Literal["scripted"] = "scripted"
    principal: ScenarioPrincipal
    steps: list[Step] = Field(min_length=1, max_length=50)
    seed: SandboxSeed = Field(default_factory=SandboxSeed)
    sensitive_values: list[str] = Field(default_factory=list)
    expect: Expect = Field(default_factory=Expect)
    assertions: list[Assertion] = Field(default_factory=list, max_length=20)
    repetitions: Repetitions | None = None  # None: the suite's

    @model_validator(mode="after")
    def _starts(self) -> "Scenario":
        if self.steps[0].op != "start" or any(s.op == "start" for s in self.steps[1:]):
            raise ValueError("un escenario empieza con un único paso `start`")
        return self


class DatasetScenario(_M):
    """`dataset` scenario: real cases by id and hash. DISABLED (`dataset_source_disabled`, topic #20).

    Real data never lives in the repo or the registry: only its id and hash."""

    id: ScenarioId
    source: Literal["dataset"]
    dataset_id: str = Field(min_length=1, max_length=120)
    dataset_hash: Sha256Hex
    reference_outcome: bool = False
    expect: Expect = Field(default_factory=Expect)
    assertions: list[Assertion] = Field(default_factory=list, max_length=20)
    repetitions: Repetitions | None = None


def _scenario_kind(value: Any) -> str:
    """`source` decides the type; without `source` it is `scripted` (format prior to ADR 0020)."""
    if isinstance(value, dict):
        return str(value.get("source", "scripted"))
    return str(getattr(value, "source", "scripted"))


AnyScenario = Annotated[
    Annotated[Scenario, Tag("scripted")] | Annotated[DatasetScenario, Tag("dataset")],
    Discriminator(_scenario_kind),
]


class MetricThreshold(_M):
    """Gate threshold for a `gate` or `guardrail` metric of the agent (evaluation spec §5).

    `noise_margin` is the tolerance against the base, in the metric's direction. `floor` is required of any
    new or changed metric, with or without a base: a minimum if higher is better, a maximum if lower is."""

    noise_margin: NonNegativeDecimal
    floor: FiniteDecimal | None = None


class EvalSuite(_M):
    id: EntityId
    version: ExactVersion
    agent_id: EntityId
    repetitions: PositiveInt = Field(default=3, le=10)  # for each scenario that does not declare its own
    scenarios: list[AnyScenario] = Field(min_length=1, max_length=200)
    thresholds: dict[str, MetricThreshold] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _unique_ids(self) -> "EvalSuite":
        ids = [s.id for s in self.scenarios]
        if len(ids) != len(set(ids)):
            raise ValueError("ids de escenario repetidos")
        return self

    def repetitions_of(self, scenario: Scenario | DatasetScenario) -> int:
        return scenario.repetitions if scenario.repetitions is not None else self.repetitions

    def scripted(self) -> list[Scenario]:
        """The scenarios the evaluator runs. A `dataset` here is a caller error:
        `suite_problems` rejects it before evaluating."""
        out = [s for s in self.scenarios if isinstance(s, Scenario)]
        if len(out) != len(self.scenarios):
            raise ValueError("la suite tiene escenarios `dataset`, que están desactivados")
        return out


class SuiteProblemCode(StrEnum):
    missing_suite = "missing_suite"
    agent_mismatch = "agent_mismatch"
    duplicate_scenario = "duplicate_scenario"
    dataset_source_disabled = "dataset_source_disabled"
    unknown_assertion_event = "unknown_assertion_event"
    invalid_assertion_filter = "invalid_assertion_filter"
    missing_threshold = "missing_threshold"
    unknown_threshold_metric = "unknown_threshold_metric"


class SuiteProblem(_M):
    code: SuiteProblemCode
    path: str
    message: str


def _problem(code: SuiteProblemCode, path: str, message: str) -> SuiteProblem:
    return SuiteProblem(code=code, path=path, message=message)


def suite_problems(agent: Agent, suite: EvalSuite | None) -> list[SuiteProblem]:
    """What prevents the agent from being published through the gate (evaluation spec §5). Empty = usable.

    Every agent needs a suite, even one with only `monitor` metrics: without scenarios the platform
    guardrails cannot be measured. `duplicate_scenario` only appears with instances built without
    validation: `EvalSuite` already rejects repeated ids when parsing."""
    if suite is None:
        return [_problem(SuiteProblemCode.missing_suite, "/", f"el agente {agent.id} no tiene eval_suite")]
    found: list[SuiteProblem] = []
    if suite.agent_id != agent.id:
        found.append(_problem(SuiteProblemCode.agent_mismatch, "/agent_id",
                              f"la suite es de {suite.agent_id} y el agente es {agent.id}"))
    seen: set[str] = set()
    for i, scenario in enumerate(suite.scenarios):
        at = f"/scenarios/{i}"
        if scenario.id in seen:
            found.append(_problem(SuiteProblemCode.duplicate_scenario, f"{at}/id",
                                  f"escenario duplicado: {scenario.id}"))
        seen.add(scenario.id)
        if isinstance(scenario, DatasetScenario):
            found.append(_problem(SuiteProblemCode.dataset_source_disabled, f"{at}/source",
                                  "la fuente `dataset` está desactivada hasta que exista su ADR"))
        for j, assertion in enumerate(scenario.assertions):
            if catalog_fields(assertion.event) is None:
                found.append(_problem(SuiteProblemCode.unknown_assertion_event, f"{at}/assertions/{j}/event",
                                      f"evento {assertion.event[:60]} fuera del catálogo"))
            for k, sub, text in predicate_problems(assertion.event, assertion.where):
                found.append(_problem(SuiteProblemCode.invalid_assertion_filter,
                                      f"{at}/assertions/{j}/where/{k}/{sub}", text))
    gating = {m.id for m in agent.metrics if m.role in ("gate", "guardrail")}
    declared = {m.id for m in agent.metrics}
    for metric_id in sorted(gating - set(suite.thresholds)):
        found.append(_problem(SuiteProblemCode.missing_threshold, f"/thresholds/{metric_id}",
                              f"la métrica {metric_id} no tiene umbral en la suite"))
    for metric_id in sorted(set(suite.thresholds) - declared):
        found.append(_problem(SuiteProblemCode.unknown_threshold_metric, f"/thresholds/{metric_id[:80]}",
                              f"la suite declara un umbral para {metric_id[:80]}, que el agente no tiene"))
    return sorted(found, key=lambda p: (p.path, p.code.value))
