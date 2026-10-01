"""Eval suite (ADR 0020): escenarios y umbrales de evaluación de un agente.

Vive en el registry y no en M0: no entra en la release del motor. La fuente `dataset` (datos reales) está
diseñada y desactivada hasta un ADR aparte.
"""

from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import Field, PositiveInt, model_validator

from agent_core.domain import (
    Agent,
    EntityId,
    ExactVersion,
    JsonValue,
    Model,
    Predicate,
    UtcDatetime,
    catalog_fields,
)
from agent_core.domain.base import Sha256Hex

NonNegativeDecimal = Annotated[Decimal, Field(ge=0, allow_inf_nan=False)]
FiniteDecimal = Annotated[Decimal, Field(allow_inf_nan=False)]


class MetricThreshold(Model):
    """Tolerancia frente a la base y piso mínimo (este último solo cuenta si no hay release base)."""

    noise_margin: NonNegativeDecimal
    floor: FiniteDecimal | None = None


class Assertion(Model):
    """Afirmación sobre los eventos de un escenario: aparece (o no) un evento que cumple el filtro."""

    event: str = Field(min_length=1, max_length=80)
    where: list[Predicate] = Field(default_factory=list, max_length=8)
    expect: Literal["at_least_one", "none"] = "at_least_one"


class ScriptedSource(Model):
    """Conversación o señal sintética sobre el motor real, con tools simuladas y `Clock` fijo."""

    kind: Literal["scripted"] = "scripted"
    user_turns: list[str] | None = None
    signal: JsonValue = None
    tool_fixtures: dict[str, JsonValue] = Field(default_factory=dict)
    clock_start: UtcDatetime

    @model_validator(mode="after")
    def _one_input(self) -> "ScriptedSource":
        if (self.user_turns is None) == (self.signal is None):
            raise ValueError(
                "un escenario guionado lleva `user_turns` (conversacional) o `signal` (task), no ambos"
            )
        return self


class DatasetSource(Model):
    """Casos de un dataset real, referenciado por id y hash. DESACTIVADA (ADR 0020, tema #13)."""

    kind: Literal["dataset"] = "dataset"
    dataset_id: str = Field(min_length=1, max_length=120)
    dataset_hash: Sha256Hex
    reference_outcome: bool = False


class Scenario(Model):
    id: EntityId
    source: Annotated[ScriptedSource | DatasetSource, Field(discriminator="kind")]
    assertions: list[Assertion] = Field(default_factory=list, max_length=20)
    repetitions: PositiveInt = 1


class EvalSuite(Model):
    id: EntityId
    version: ExactVersion
    agent_id: EntityId
    scenarios: list[Scenario] = Field(min_length=1, max_length=200)
    thresholds: dict[str, MetricThreshold] = Field(default_factory=dict)


class SuiteProblemCode(StrEnum):
    missing_suite = "missing_suite"
    agent_mismatch = "agent_mismatch"
    duplicate_scenario = "duplicate_scenario"
    dataset_source_disabled = "dataset_source_disabled"
    unknown_assertion_event = "unknown_assertion_event"
    missing_threshold = "missing_threshold"
    unknown_threshold_metric = "unknown_threshold_metric"


class SuiteProblem(Model):
    code: SuiteProblemCode
    path: str
    message: str


def _problem(code: SuiteProblemCode, path: str, message: str) -> SuiteProblem:
    return SuiteProblem(code=code, path=path, message=message)


def suite_problems(agent: Agent, suite: EvalSuite | None) -> list[SuiteProblem]:
    """Lo que impide publicar al agente por el gate. Lista vacía = la suite es utilizable.

    Todo agente necesita una suite, aunque solo tenga métricas `monitor`: sin escenarios no se pueden medir
    los guardarraíles de plataforma.
    """
    if suite is None:
        return [_problem(SuiteProblemCode.missing_suite, "/", f"el agente {agent.id} no tiene eval_suite")]
    found: list[SuiteProblem] = []
    if suite.agent_id != agent.id:
        found.append(
            _problem(
                SuiteProblemCode.agent_mismatch,
                "/agent_id",
                f"la suite es de {suite.agent_id} y el agente es {agent.id}",
            )
        )
    seen: set[str] = set()
    for i, scenario in enumerate(suite.scenarios):
        at = f"/scenarios/{i}"
        if scenario.id in seen:
            found.append(
                _problem(
                    SuiteProblemCode.duplicate_scenario, f"{at}/id", f"escenario duplicado: {scenario.id}"
                )
            )
        seen.add(scenario.id)
        if isinstance(scenario.source, DatasetSource):
            found.append(
                _problem(
                    SuiteProblemCode.dataset_source_disabled,
                    f"{at}/source",
                    "la fuente `dataset` está desactivada hasta que exista su ADR",
                )
            )
        for j, assertion in enumerate(scenario.assertions):
            if catalog_fields(assertion.event) is None:
                found.append(
                    _problem(
                        SuiteProblemCode.unknown_assertion_event,
                        f"{at}/assertions/{j}/event",
                        f"evento {assertion.event[:60]} fuera del catálogo",
                    )
                )
    gating = {m.id for m in agent.metrics if m.role in ("gate", "guardrail")}
    declared = {m.id for m in agent.metrics}
    for metric_id in sorted(gating - set(suite.thresholds)):
        found.append(
            _problem(
                SuiteProblemCode.missing_threshold,
                f"/thresholds/{metric_id}",
                f"la métrica {metric_id} no tiene umbral en la suite",
            )
        )
    for metric_id in sorted(set(suite.thresholds) - declared):
        found.append(
            _problem(
                SuiteProblemCode.unknown_threshold_metric,
                f"/thresholds/{metric_id}",
                f"la suite declara un umbral para {metric_id}, que el agente no tiene",
            )
        )
    return sorted(found, key=lambda p: (p.path, p.code.value))
