"""Métricas declaradas por agente (ADR 0020): un DSL declarativo sobre un catálogo cerrado de eventos.

El motor no ejecuta nada de esto en runtime: M1 lo valida contra el catálogo y la evaluación y la analítica
lo calculan sobre eventos.
"""

from decimal import Decimal
from typing import Annotated, Literal

from pydantic import Field, model_validator

from agent_core.domain.base import EntityId, Model
from agent_core.domain.nodes import PositiveTimedelta
from agent_core.domain.refs import EntityRef

FiniteDecimal = Annotated[Decimal, Field(allow_inf_nan=False)]
Scalar = str | bool | int | FiniteDecimal

PredicateOp = Literal["eq", "ne", "in", "lt", "le", "gt", "ge"]
MetricRole = Literal["guardrail", "gate", "monitor"]
Aggregation = Literal["count", "sum", "avg", "percentile", "rate"]
# Una ventana sin cota no se acepta: o es el alcance de un escenario o de un run, o una duración positiva.
MetricWindow = Literal["scenario", "run"] | PositiveTimedelta

# Los ids de esta familia son de la plataforma (guardarraíles universales): ningún agente puede declararlos.
PLATFORM_METRIC_PREFIX = "platform_"


class Predicate(Model):
    """Filtro sobre un campo del catálogo de eventos."""
    field: str = Field(min_length=1, max_length=80)
    op: PredicateOp
    value: Scalar | list[Scalar]

    @model_validator(mode="after")
    def _shape(self) -> "Predicate":
        if (self.op == "in") != isinstance(self.value, list):
            raise ValueError("`in` exige una lista y los demás operadores un escalar")
        if isinstance(self.value, list) and not self.value:
            raise ValueError("`in` exige al menos un valor")
        return self


class MetricExpr(Model):
    """Métrica determinista sobre eventos: evento, filtro, agregación, ventana y agrupación."""
    event: str = Field(min_length=1, max_length=80)
    aggregation: Aggregation
    where: list[Predicate] = Field(default_factory=list, max_length=8)
    field: str | None = Field(default=None, min_length=1, max_length=80)
    percentile: int | None = Field(default=None, ge=1, le=99)
    denominator: "MetricExpr | None" = None
    window: MetricWindow
    group_by: list[Annotated[str, Field(min_length=1, max_length=80)]] = Field(
        default_factory=list, max_length=3
    )

    @model_validator(mode="after")
    def _shape(self) -> "MetricExpr":
        if (self.aggregation in ("sum", "avg", "percentile")) != (self.field is not None):
            raise ValueError("`field` es obligatorio en sum, avg y percentile, y solo en ellas")
        if (self.aggregation == "percentile") != (self.percentile is not None):
            raise ValueError("`percentile` es obligatorio en percentile, y solo en ella")
        if (self.aggregation == "rate") != (self.denominator is not None):
            raise ValueError("`denominator` es obligatorio en rate, y solo en ella")
        den = self.denominator
        if den is not None:
            if den.aggregation != "count":
                raise ValueError("el denominador de rate es un count")
            if den.window != self.window or den.group_by != self.group_by:
                raise ValueError("el denominador comparte ventana y agrupación con el numerador")
        return self


class JudgeExpr(Model):
    """Métrica calificada por un juez LLM: el único tipo no determinista (ADR 0020)."""
    judge_profile: EntityRef
    rubric: str = Field(min_length=1, max_length=2000)
    target_event: str = Field(min_length=1, max_length=80)


class AlertThreshold(Model):
    """Umbral de alerta de producción. No es el umbral del gate."""
    breach_when: Literal["above", "below"]
    value: FiniteDecimal


class MetricDef(Model):
    """Una métrica del agente, con el papel que juega en el gate y en producción."""
    id: EntityId
    description: str = Field(min_length=1, max_length=300)
    role: MetricRole
    higher_is_better: bool
    alert: AlertThreshold | None = None
    expr: MetricExpr | JudgeExpr


MetricExpr.model_rebuild()
