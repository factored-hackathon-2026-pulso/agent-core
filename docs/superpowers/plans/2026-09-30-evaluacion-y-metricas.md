# Evaluación y métricas por agente — Plan de implementación

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Cada agente declara métricas propias (DSL sobre un catálogo cerrado de eventos) y el gate de evaluación del registry decide con doble vara, N métricas `gate`, guardarraíles de plataforma y marca `yardstick_loosened`.

**Architecture:** M0 gana los tipos del DSL y el catálogo de eventos (`Agent.metrics`, cambio de interfaz). M1 valida el DSL contra el catálogo como chequeo por agente (`MT-01`…`MT-06`). El paquete `agent_core.registry.evaluation` implementa, como funciones puras y sin Postgres, la entidad `EvalSuite`, la comprobación de suite, la clasificación de aflojamientos y el veredicto del gate; el registry completo (servicio, API, roles, persistencia) y la unidad 6 (ejecutor de escenarios, juez, `EvalPort`) quedan en planes aparte.

**Tech Stack:** Python 3.12, Pydantic v2, pytest, mypy strict, ruff, import-linter, `uv`.

**Spec:** `docs/specs/2026-09-30-evaluacion-y-metricas-design.md` · ADR `docs/adr/0020-evaluacion-y-metricas-por-agente.md` (enmienda el ADR 0018 y el registry §5.2)

## Alcance de este plan

Cubre las piezas 1 (M0 y M1) y la parte pura de la 2 (registry) del spec §11. **No cubre** y deja para planes posteriores:

- `RegistryService` (proposals, `freeze`, `evaluate`, `publish`), la API REST y la persistencia Postgres de `eval_suite` y de `releases.eval_suite_refs`.
- La verificación por rol en el servidor (`forbidden_role`) y la pantalla de aprobación con tres elementos separados: **T-EVAL-16 y T-EVAL-17**.
- `EvalPort`, el ejecutor de escenarios, el evaluador en memoria del DSL y el juez LLM (unidad 6), y el compilador a SQL (analítica, tema #11): **T-EVAL-04 y T-EVAL-14**.
- **T-EVAL-09** (un aflojamiento publicado solo afecta a propuestas posteriores) queda cubierto de forma estructural: `evaluate_gate` recibe siempre las definiciones de la base como parámetro, así que ninguna propuesta puede cambiarlas. Lo ejercita T-EVAL-07 (tarea 6).

Cobertura de pruebas del spec en este plan: T-EVAL-01, 02, 03 (tareas 1 y 3), 05, 06, 07, 13, 15 (tarea 6), 08 (tarea 5), 10 (`MT-05`, tarea 3), 11 y 12 (tarea 4).

## Global Constraints

- Python 3.12, Pydantic v2, `uv`. Fronteras: `agent_core.domain` y `agent_core.flows` no importan `agent_core.registry`; `agent_core.registry` solo usa `domain`, `ports` y la interfaz pública de `flows` (`.importlinter`).
- Nunca `float` para cifras: `Decimal` finito (`allow_inf_nan=False`). Nunca `datetime.now()`, `time.time()`, `uuid4()`, `random` ni `secrets` (los verifica `ruff`).
- Todo el código, identificadores y nombres de campos en **inglés**; docstrings y mensajes de usuario en español, como el código vecino.
- Datos solo sintéticos en fixtures y pruebas. Ningún campo de PII en el catálogo de eventos.
- Cambiar un tipo de M0 obliga a subir `SCHEMA_VERSION`, regenerar `contracts/` (`uv run agentcore contracts`) y avisar: es un cambio de interfaz para todos los módulos.
- Líneas de 110 caracteres como máximo. `mypy` estricto sobre `agent_core`. Mensajes de violación acotados con `clip`.
- Los modelos de dominio heredan de `agent_core.domain.base.Model` (inmutable, `extra="forbid"`).
- Suites de `ScriptedSource` con datos sintéticos; la fuente `DatasetSource` se rechaza con `dataset_source_disabled`.
- Plantilla de commit: `feat(<módulo>): <resumen>` en español, como el historial del repo.

## Review Focus

Entradas o condiciones que el spec implica pero que ninguna prueba del spec ejercita; cada una tiene su prueba en la tarea que dueña del código:

1. **Métrica sin valor en el reporte** (denominador cero, evento ausente, evaluador que no la calcula): se trata como fallo, nunca como pase. Prueba en la tarea 6.
2. **Métrica con dirección "menor es mejor"** (costo, latencia, escalamientos): el piso es un máximo y el ruido se suma en lugar de restarse. Pruebas en las tareas 5 y 6.
3. **Agente sin ninguna suite**, aunque solo tenga métricas `monitor`: los guardarraíles de plataforma no se pueden medir sin escenarios, así que no es publicable por el gate. Prueba en la tarea 4.
4. **Identificadores duplicados** (métricas dentro de un agente, escenarios dentro de una suite) y umbrales declarados para métricas inexistentes: se rechazan y no se ignoran en silencio. Pruebas en las tareas 3 y 4.
5. **Cadenas enormes o con caracteres raros** en `event`, `field`, valores de `where` y rúbricas: M1 no puede lanzar una excepción ni producir mensajes sin acotar; todo lo que se repite en un mensaje pasa por `clip`. Prueba en la tarea 3.

---

### Task 1: Tipos del DSL de métricas y catálogo de eventos (M0)

**Files:**
- Create: `agent_core/domain/metrics.py`
- Create: `agent_core/domain/metric_catalog.py`
- Modify: `agent_core/domain/__init__.py` (imports y `__all__`)
- Test: `tests/m00/test_metrics.py`
- Test: `tests/m00/test_metric_catalog.py`

**Interfaces:**
- Consumes: `agent_core.domain.base.Model`, `EntityId`; `agent_core.domain.nodes.PositiveTimedelta`; `agent_core.domain.refs.EntityRef`.
- Produces (para las tareas 2 a 6):
  - `Predicate(field: str, op: PredicateOp, value: Scalar | list[Scalar])`
  - `MetricExpr(event: str, aggregation: Aggregation, where: list[Predicate] = [], field: str | None = None, percentile: int | None = None, denominator: MetricExpr | None = None, window: MetricWindow, group_by: list[str] = [])`
  - `JudgeExpr(judge_profile: EntityRef, rubric: str, target_event: str)`
  - `AlertThreshold(breach_when: Literal["above", "below"], value: Decimal)`
  - `MetricDef(id: EntityId, description: str, role: MetricRole, higher_is_better: bool, alert: AlertThreshold | None = None, expr: MetricExpr | JudgeExpr)`
  - `PLATFORM_METRIC_PREFIX = "platform_"`
  - `METRIC_EVENT_CATALOG: Mapping[str, Mapping[str, FieldKind]]` y `catalog_fields(event: str) -> Mapping[str, FieldKind] | None`
  - Alias de tipo: `MetricRole = Literal["guardrail", "gate", "monitor"]`, `FieldKind = Literal["str", "int", "decimal", "bool"]`.

- [ ] **Step 1: Escribir las pruebas del DSL**

Crear `tests/m00/test_metrics.py`:

```python
from typing import Any

import pytest
from pydantic import ValidationError

from agent_core.domain import JudgeExpr, MetricDef, MetricExpr, Predicate


def expr(**over: Any) -> MetricExpr:
    data: dict[str, Any] = {"event": "engine.escalated", "aggregation": "count", "window": "scenario"}
    return MetricExpr.model_validate(data | over)


def test_count_metric_is_valid() -> None:
    value = expr(where=[{"field": "reason_code", "op": "eq", "value": "release_revoked"}], group_by=["release"])
    assert value.aggregation == "count"
    assert value.where[0].value == "release_revoked"


@pytest.mark.parametrize(
    "over",
    [
        {"aggregation": "sum"},  # sum exige field
        {"aggregation": "avg"},
        {"aggregation": "percentile", "field": "duration_ms"},  # percentile exige el percentil
        {"aggregation": "count", "field": "duration_ms"},  # count no lleva field
        {"aggregation": "count", "percentile": 50},
        {"aggregation": "rate"},  # rate exige denominador
        {"aggregation": "sum", "field": "duration_ms", "percentile": 50},
    ],
)
def test_inconsistent_shapes_are_rejected(over: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        expr(**over)


def test_percentile_bounds() -> None:
    assert expr(aggregation="percentile", field="duration_ms", percentile=95).percentile == 95
    for bad in (0, 100):
        with pytest.raises(ValidationError):
            expr(aggregation="percentile", field="duration_ms", percentile=bad)


# T-EVAL-02: la ventana es obligatoria y acotada
def test_window_is_required_and_positive() -> None:
    with pytest.raises(ValidationError):
        MetricExpr.model_validate({"event": "engine.escalated", "aggregation": "count"})
    assert expr(window="PT1H").window.total_seconds() == 3600
    for bad in ("PT0S", "-PT1H", "forever"):
        with pytest.raises(ValidationError):
            expr(window=bad)


# T-EVAL-03: el DSL no tiene funciones de hora ni claves libres
@pytest.mark.parametrize("extra", [{"now": "2026-01-01"}, {"sql": "SELECT 1"}, {"since": "PT1H"}])
def test_free_keys_are_rejected(extra: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        expr(**extra)


def test_rate_needs_an_aligned_count_denominator() -> None:
    denominator = {"event": "engine.turn_completed", "aggregation": "count", "window": "scenario"}
    ok = expr(aggregation="rate", denominator=denominator)
    assert ok.denominator is not None
    with pytest.raises(ValidationError):  # ventana distinta
        expr(aggregation="rate", denominator=denominator | {"window": "run"})
    with pytest.raises(ValidationError):  # el denominador es un count
        expr(aggregation="rate", denominator=denominator | {"aggregation": "sum", "field": "duration_ms"})
    with pytest.raises(ValidationError):  # agrupación distinta
        expr(aggregation="rate", denominator=denominator | {"group_by": ["release"]})
    with pytest.raises(ValidationError):  # un denominador sin rate
        expr(denominator=denominator)


def test_predicate_shape() -> None:
    assert Predicate(field="reason_code", op="in", value=["a", "b"]).op == "in"
    for bad in ({"op": "in", "value": "a"}, {"op": "eq", "value": ["a"]}, {"op": "in", "value": []}):
        with pytest.raises(ValidationError):
            Predicate.model_validate({"field": "reason_code"} | bad)


def test_metric_def_with_judge_expr_and_roundtrip() -> None:
    data: dict[str, Any] = {
        "id": "proposal_quality", "description": "Calidad de la propuesta", "role": "gate",
        "higher_is_better": True,
        "expr": {"judge_profile": "juez@1.0.0", "rubric": "Califica de 0 a 1", "target_event": "registry.evaluated"},
    }
    metric = MetricDef.model_validate(data)
    assert isinstance(metric.expr, JudgeExpr)
    assert MetricDef.model_validate_json(metric.model_dump_json()) == metric


def test_metric_def_with_expr_and_alert() -> None:
    metric = MetricDef.model_validate(
        {"id": "esc_rate", "description": "d", "role": "monitor", "higher_is_better": False,
         "alert": {"breach_when": "above", "value": "0.25"},
         "expr": {"event": "engine.escalated", "aggregation": "count", "window": "scenario"}}
    )
    assert isinstance(metric.expr, MetricExpr)
    assert str(metric.alert.value if metric.alert else None) == "0.25"


@pytest.mark.parametrize("bad", ["Mayúscula", "con espacio", "", "x" * 0])
def test_metric_id_pattern(bad: str) -> None:
    with pytest.raises(ValidationError):
        MetricDef.model_validate(
            {"id": bad, "description": "d", "role": "monitor", "higher_is_better": True,
             "expr": {"event": "engine.escalated", "aggregation": "count", "window": "scenario"}}
        )


def test_alert_value_is_a_finite_decimal() -> None:
    with pytest.raises(ValidationError):
        MetricDef.model_validate(
            {"id": "m", "description": "d", "role": "monitor", "higher_is_better": True,
             "alert": {"breach_when": "above", "value": "NaN"},
             "expr": {"event": "engine.escalated", "aggregation": "count", "window": "scenario"}}
        )
```

- [ ] **Step 2: Escribir las pruebas del catálogo**

Crear `tests/m00/test_metric_catalog.py`:

```python
import types
import typing

import pytest
from pydantic import BaseModel

import agent_core.domain.events as events
from agent_core.domain import METRIC_EVENT_CATALOG, PLATFORM_METRIC_PREFIX, EngineEvent, catalog_fields

# Nombres de campo que nunca pueden ser medibles: son datos de cliente o texto libre (regla 6 de CLAUDE.md).
FORBIDDEN_FIELDS = {"args", "text", "email", "name", "phone", "document_id", "account_number", "message"}


def _engine_event_classes() -> dict[str, type[EngineEvent]]:
    found: dict[str, type[EngineEvent]] = {}
    for obj in vars(events).values():
        if isinstance(obj, type) and issubclass(obj, EngineEvent) and obj is not EngineEvent:
            found[obj.model_fields["type"].default] = obj
    return found


def _unwrap(annotation: object) -> type[BaseModel] | None:
    """El modelo detrás de `X` o `X | None`."""
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return annotation
    if typing.get_origin(annotation) in (typing.Union, types.UnionType):
        for arg in typing.get_args(annotation):
            if isinstance(arg, type) and issubclass(arg, BaseModel):
                return arg
    return None


def _has_field(event_cls: type[EngineEvent], dotted: str) -> bool:
    head, *rest = dotted.split(".")
    if head in EngineEvent.model_fields and head != "payload" and not rest:
        return True
    model = _unwrap(event_cls.model_fields["payload"].annotation)
    for part in [head, *rest]:
        if model is None or part not in model.model_fields:
            return False
        last = model.model_fields[part].annotation
        model = _unwrap(last)
    return True


def test_catalog_is_not_empty_and_namespaced() -> None:
    assert METRIC_EVENT_CATALOG
    assert all(name.startswith(("engine.", "registry.")) for name in METRIC_EVENT_CATALOG)


def test_every_engine_field_exists_in_the_real_event() -> None:
    classes = _engine_event_classes()
    for name, fields in METRIC_EVENT_CATALOG.items():
        if not name.startswith("engine."):
            continue
        event_cls = classes.get(name.removeprefix("engine."))
        assert event_cls is not None, f"{name} no es un evento de M0"
        for dotted in fields:
            assert _has_field(event_cls, dotted), f"{name}: el campo {dotted} no existe en el evento"


def test_no_catalog_field_exposes_customer_data() -> None:
    for name, fields in METRIC_EVENT_CATALOG.items():
        leaves = {dotted.rsplit(".", 1)[-1] for dotted in fields}
        assert not leaves & FORBIDDEN_FIELDS, name


def test_every_event_exposes_run_grouping_fields() -> None:
    for name, fields in METRIC_EVENT_CATALOG.items():
        if name.startswith("engine."):
            assert fields["release"] == "str" and fields["run_id"] == "str", name
        else:
            assert fields["origin"] == "str" and fields["agent_id"] == "str", name


def test_catalog_fields_lookup() -> None:
    assert catalog_fields("engine.turn_completed") is not None
    assert catalog_fields("engine.nope") is None
    assert catalog_fields("") is None
    assert catalog_fields("x" * 10_000) is None


def test_catalog_is_read_only() -> None:
    with pytest.raises(TypeError):
        METRIC_EVENT_CATALOG["engine.nope"] = {}  # type: ignore[index]


def test_platform_prefix_is_reserved_for_the_platform() -> None:
    assert PLATFORM_METRIC_PREFIX == "platform_"
```

- [ ] **Step 3: Verificar que fallan**

Run: `uv run pytest tests/m00/test_metrics.py tests/m00/test_metric_catalog.py -v`
Expected: FAIL (error de importación: `JudgeExpr`, `MetricDef`, `METRIC_EVENT_CATALOG` no existen en `agent_core.domain`).

- [ ] **Step 4: Implementar `domain/metrics.py`**

Crear `agent_core/domain/metrics.py`:

```python
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
    group_by: list[Annotated[str, Field(min_length=1, max_length=80)]] = Field(default_factory=list, max_length=3)

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
```

- [ ] **Step 5: Implementar `domain/metric_catalog.py`**

Crear `agent_core/domain/metric_catalog.py`:

```python
"""Catálogo cerrado de eventos medibles (ADR 0020, spec de evaluación §4).

Solo expone campos que el evento ya lleva en vista `audit`; ningún campo de datos de cliente. Agregar un
evento medible es un cambio de catálogo y sube `SCHEMA_VERSION`.
"""

from collections.abc import Mapping
from types import MappingProxyType
from typing import Literal

FieldKind = Literal["str", "int", "decimal", "bool"]

_ENGINE_COMMON: dict[str, FieldKind] = {"release": "str", "run_id": "str"}
_REGISTRY_COMMON: dict[str, FieldKind] = {
    "origin": "str", "actor_role": "str", "agent_id": "str", "proposal_id": "str",
}


def _engine(**fields: FieldKind) -> Mapping[str, FieldKind]:
    return MappingProxyType(_ENGINE_COMMON | fields)


def _registry(**fields: FieldKind) -> Mapping[str, FieldKind]:
    return MappingProxyType(_REGISTRY_COMMON | fields)


METRIC_EVENT_CATALOG: Mapping[str, Mapping[str, FieldKind]] = MappingProxyType({
    "engine.turn_completed": _engine(entry="str", duration_ms="int", degraded="bool"),
    "engine.escalated": _engine(reason_code="str", target_queue="str", priority="str"),
    "engine.run_closed": _engine(outcome="str", closed_by="str"),
    "engine.tool_called": _engine(status="str", latency_ms="int", attempt="int"),
    "engine.agent_step": _engine(kind="str", status="str", step="int", latency_ms="int"),
    "engine.action_verified": _engine(result="str"),
    "engine.response_emitted": _engine(
        kind="str", fallback_used="bool", **{"validator.ok": "bool", "validator.regenerations": "int",
                                              "llm.calls": "int", "llm.latency_ms": "int",
                                              "llm.tokens_in": "int", "llm.tokens_out": "int",
                                              "llm.cost_usd": "decimal"},
    ),
    "engine.response_failed": _engine(reason_code="str", **{"validator.ok": "bool"}),
    "engine.access_denied": _engine(reason="str"),
    "engine.injection_flagged": _engine(scope="str", ruleset="str"),
    "engine.handoff_resolved": _engine(handoff_quality="str", resolution_code="str", reader_type="str"),
    "registry.proposal_created": _registry(),
    "registry.validated": _registry(),
    "registry.frozen": _registry(),
    "registry.evaluated": _registry(verdict="str"),
    "registry.approved": _registry(),
    "registry.rejected": _registry(),
    "registry.published": _registry(),
    "registry.promoted": _registry(alias="str"),
    "registry.revoked": _registry(),
})


def catalog_fields(event: str) -> Mapping[str, FieldKind] | None:
    """Campos medibles de `event`, o None si no está en el catálogo. Nunca lanza."""
    return METRIC_EVENT_CATALOG.get(event)
```

- [ ] **Step 6: Exportar en `agent_core/domain/__init__.py`**

Añadir los imports (en orden alfabético de módulo, junto a los demás `from agent_core.domain...`) y los nombres en `__all__`:

```python
from agent_core.domain.metric_catalog import METRIC_EVENT_CATALOG, FieldKind, catalog_fields
from agent_core.domain.metrics import (
    PLATFORM_METRIC_PREFIX,
    AlertThreshold,
    FiniteDecimal,
    JudgeExpr,
    MetricDef,
    MetricExpr,
    Predicate,
)
```

En `__all__` añadir (respetando el orden que ya use el archivo): `"METRIC_EVENT_CATALOG"`, `"PLATFORM_METRIC_PREFIX"`, `"AlertThreshold"`, `"JudgeExpr"`, `"MetricDef"`, `"MetricExpr"`, `"Predicate"`, `"catalog_fields"`. Los alias de tipo (`FieldKind`, `FiniteDecimal`) no son clases ni funciones, así que no son obligatorios en `__all__`: no los añadas.

- [ ] **Step 7: Verificar que pasan**

Run: `uv run pytest tests/m00/test_metrics.py tests/m00/test_metric_catalog.py tests/m00/test_public_api.py -v`
Expected: PASS. Si `test_domain_reexports_every_public_definition` reporta un nombre faltante, añadirlo a `__all__`.
Si `test_every_engine_field_exists_in_the_real_event` falla para un campo, el catálogo está mal (no el evento): corregir el catálogo.

- [ ] **Step 8: Commit**

```bash
git add agent_core/domain/metrics.py agent_core/domain/metric_catalog.py agent_core/domain/__init__.py tests/m00/test_metrics.py tests/m00/test_metric_catalog.py
git commit -m "feat(m0): tipos del DSL de metricas y catalogo cerrado de eventos medibles (ADR 0020)"
```

---

### Task 2: `Agent.metrics`, `SCHEMA_VERSION` 0.5.0 y contratos (M0)

**Files:**
- Modify: `agent_core/domain/entities.py` (clase `Agent`, líneas 50-70)
- Modify: `agent_core/domain/version.py`
- Modify: `tests/m00/test_public_api.py` (aserción de versión)
- Modify: `docs/specs/motor/m00-dominio-y-contratos.md` (versión y §2.4)
- Regenerate: `contracts/` (no editar a mano)
- Test: `tests/m00/test_agent_metrics.py`

**Interfaces:**
- Consumes: `MetricDef` (tarea 1).
- Produces: `Agent.metrics: list[MetricDef]` (por defecto `[]`, máximo 32); `SCHEMA_VERSION == "0.5.0"`; esquemas `contracts/schemas/MetricDef.json`, `MetricExpr.json`, `JudgeExpr.json`, `Predicate.json`, `AlertThreshold.json`.

- [ ] **Step 1: Escribir la prueba**

Crear `tests/m00/test_agent_metrics.py`:

```python
import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from agent_core.contracts import render_contracts
from agent_core.domain import SCHEMA_VERSION, Agent
from tests.m01.cases import AGENT

ROOT = Path(__file__).resolve().parents[2]


def metric(mid: str) -> dict[str, Any]:
    return {"id": mid, "description": "d", "role": "monitor", "higher_is_better": False,
            "expr": {"event": "engine.escalated", "aggregation": "count", "window": "scenario"}}


def test_agent_without_metrics_stays_valid() -> None:
    assert Agent.model_validate(deepcopy(AGENT)).metrics == []


def test_agent_carries_its_metrics() -> None:
    agent = Agent.model_validate(deepcopy(AGENT) | {"metrics": [metric("esc_count"), metric("esc_other")]})
    assert [m.id for m in agent.metrics] == ["esc_count", "esc_other"]


def test_agent_rejects_unknown_metric_keys() -> None:
    bad = metric("esc_count") | {"query": "SELECT 1"}
    with pytest.raises(ValidationError):
        Agent.model_validate(deepcopy(AGENT) | {"metrics": [bad]})


def test_agent_caps_metric_count() -> None:
    many = [metric(f"m{i}") for i in range(33)]
    with pytest.raises(ValidationError):
        Agent.model_validate(deepcopy(AGENT) | {"metrics": many})


def test_schema_version_bumped_and_contracts_publish_the_new_types() -> None:
    assert SCHEMA_VERSION == "0.5.0"
    files = render_contracts()
    for name in ("MetricDef", "MetricExpr", "JudgeExpr", "Predicate", "AlertThreshold"):
        json.loads(files[f"schemas/{name}.json"])
    assert "metrics" in json.loads(files["schemas/Agent.json"])["properties"]


def test_committed_contracts_match_the_generated_ones() -> None:
    assert (ROOT / "contracts" / "VERSION").read_text(encoding="utf-8").strip() == SCHEMA_VERSION
```

- [ ] **Step 2: Verificar que falla**

Run: `uv run pytest tests/m00/test_agent_metrics.py -v`
Expected: FAIL (`Agent` rechaza `metrics` por `extra="forbid"`; la versión sigue en 0.4.0).

- [ ] **Step 3: Añadir el campo a `Agent`**

En `agent_core/domain/entities.py` añadir `from agent_core.domain.metrics import MetricDef` junto a los demás imports de `agent_core.domain`, y al final de los campos de `Agent` (después de `max_repair_turns_per_run`):

```python
    metrics: list[MetricDef] = Field(default_factory=list, max_length=32)  # ADR 0020; el motor no las ejecuta
```

- [ ] **Step 4: Subir la versión y actualizar las referencias**

En `agent_core/domain/version.py` cambiar `SCHEMA_VERSION = "0.4.0"` por `SCHEMA_VERSION = "0.5.0"`.
En `tests/m00/test_public_api.py` cambiar `assert domain.SCHEMA_VERSION == "0.4.0"` por `== "0.5.0"`.
En `docs/specs/motor/m00-dominio-y-contratos.md` reemplazar cada mención de `0.4.0` por `0.5.0` y añadir al final de la §2.4 este párrafo:

```markdown
**`Agent.metrics` (ADR 0020, `SCHEMA_VERSION` 0.5.0).** Lista opcional de `MetricDef` (máximo 32): las métricas que el agente declara para el gate de evaluación y el monitoreo. El motor las ignora en runtime. Los tipos del DSL están en `domain/metrics.py` y el catálogo cerrado de eventos medibles en `domain/metric_catalog.py`. Spec: `docs/specs/2026-09-30-evaluacion-y-metricas-design.md`.
```

- [ ] **Step 5: Regenerar `contracts/`**

Run: `uv run agentcore contracts`
Expected: se reescriben `contracts/VERSION` (0.5.0), `contracts/schemas/Agent.json` y los esquemas nuevos de la tarea 1.

Run: `uv run agentcore contracts --check`
Expected: sale con código 0.

- [ ] **Step 6: Verificar que pasan**

Run: `uv run pytest tests/m00 -v`
Expected: PASS. Si algún otro test fija `0.4.0`, cambiarlo; no lo dejes pasar.

- [ ] **Step 7: Commit (cambio de interfaz: avisar)**

```bash
git add agent_core/domain/entities.py agent_core/domain/version.py tests/m00 docs/specs/motor/m00-dominio-y-contratos.md contracts
git commit -m "feat(m0): Agent.metrics y SCHEMA_VERSION 0.5.0 con contracts regenerado (ADR 0020)"
```

Avisar en la descripción del PR: es un cambio de interfaz para todos los módulos (regla 7 de CLAUDE.md); `Agent` gana un campo opcional.

---

### Task 3: Validación del DSL en M1 (`MT-01`…`MT-06`)

**Files:**
- Create: `agent_core/flows/metrics.py`
- Modify: `agent_core/flows/agent.py` (`validate_agent`)
- Modify: `docs/specs/motor/m01-validacion-estatica.md` (catálogo de reglas, §3.8)
- Test: `tests/m01/test_agent_metrics.py`

**Interfaces:**
- Consumes: `Agent.metrics`, `MetricDef`, `MetricExpr`, `JudgeExpr`, `catalog_fields`, `PLATFORM_METRIC_PREFIX` (tareas 1 y 2); `Violation`, `clip` (M1); `RegistryView.resolve`.
- Produces: `validate_agent_metrics(agent: Agent, where: str, reg: RegistryView) -> list[Violation]`, invocada desde `validate_agent`. Reglas:
  - `MT-01`: evento fuera del catálogo.
  - `MT-02`: campo (de `where`, `group_by` o `field`) inexistente en el evento, `field` no numérico, valor de un tipo que no corresponde, u operador de orden sobre un campo no numérico.
  - `MT-03`: id de métrica duplicado dentro del agente.
  - `MT-04`: `target_event` de un juez fuera del catálogo.
  - `MT-05`: id con el prefijo reservado `platform_`.
  - `MT-06`: `judge_profile` que no resuelve a un `model_profile` del registro.

- [ ] **Step 1: Escribir las pruebas**

Crear `tests/m01/test_agent_metrics.py`:

```python
from copy import deepcopy
from typing import Any

from agent_core.domain import EntityKind, ModelProfile
from agent_core.flows.agent import validate_agent
from agent_core.flows.violations import Violation
from tests.m01.cases import ENTITIES, agent, registry


# Origen que `validate_agent` pone en las rutas: el archivo del agente, o la ruta por defecto en memoria.
WHERE = registry().source(EntityKind.agent, "atencion", "1.0.0") or "agents/atencion@1.0.0.yaml"


def _profile_ref() -> str:
    profile = next(e for e in ENTITIES if isinstance(e, ModelProfile))
    return f"{profile.id}@{profile.version}"


def metric(mid: str = "esc_count", **expr_over: Any) -> dict[str, Any]:
    expr = {"event": "engine.escalated", "aggregation": "count", "window": "scenario"} | expr_over
    return {"id": mid, "description": "d", "role": "monitor", "higher_is_better": False, "expr": expr}


def judge(mid: str = "quality", **over: Any) -> dict[str, Any]:
    expr = {"judge_profile": _profile_ref(), "rubric": "r", "target_event": "registry.evaluated"} | over
    return {"id": mid, "description": "d", "role": "gate", "higher_is_better": True, "expr": expr}


def mt(metrics: list[dict[str, Any]]) -> list[Violation]:
    found = validate_agent(agent(metrics=deepcopy(metrics)), registry())
    return [v for v in found if v.rule.startswith("MT-")]


def test_valid_metrics_have_no_mt_violations() -> None:
    assert mt([
        metric("esc_count"),
        metric("p95_turn", aggregation="percentile", field="duration_ms", percentile=95, event="engine.turn_completed"),
        metric("cost", aggregation="sum", field="llm.cost_usd", event="engine.response_emitted",
               where=[{"field": "fallback_used", "op": "eq", "value": False}], group_by=["release"]),
        metric("proposals", event="registry.proposal_created",
               where=[{"field": "origin", "op": "in", "value": ["auto_detect", "builder_chat"]}]),
        judge(),
    ]) == []


def test_agent_without_metrics_is_unaffected() -> None:
    assert mt([]) == []


# T-EVAL-01
def test_mt_01_event_outside_the_catalog() -> None:
    found = mt([metric(event="engine.nope")])
    assert [(v.rule, v.path) for v in found] == [("MT-01", f"{WHERE}#/metrics/0/expr/event")]


def test_mt_02_unknown_fields() -> None:
    found = mt([metric(where=[{"field": "nope", "op": "eq", "value": "x"}], group_by=["also_nope"])])
    assert sorted(v.path or "" for v in found) == [
        f"{WHERE}#/metrics/0/expr/group_by/0",
        f"{WHERE}#/metrics/0/expr/where/0/field",
    ]
    assert {v.rule for v in found} == {"MT-02"}


def test_mt_02_aggregated_field_must_be_numeric() -> None:
    found = mt([metric(aggregation="sum", field="reason_code")])
    assert [(v.rule, (v.path or "").rsplit("/", 1)[-1]) for v in found] == [("MT-02", "field")]


def test_mt_02_value_type_and_ordering() -> None:
    wrong_type = mt([metric(where=[{"field": "reason_code", "op": "eq", "value": 3}])])
    ordering_on_text = mt([metric(where=[{"field": "reason_code", "op": "gt", "value": "a"}])])
    bad_in = mt([metric(where=[{"field": "reason_code", "op": "in", "value": ["a", 1]}])])
    assert [v.rule for v in wrong_type + ordering_on_text + bad_in] == ["MT-02", "MT-02", "MT-02"]


def test_mt_02_numeric_comparison_is_accepted_on_numeric_fields() -> None:
    assert mt([metric(event="engine.turn_completed", where=[{"field": "duration_ms", "op": "gt", "value": 100}])]) == []


def test_mt_02_checks_the_rate_denominator() -> None:
    denominator = {"event": "engine.turn_completed", "aggregation": "count", "window": "scenario",
                   "where": [{"field": "nope", "op": "eq", "value": "x"}]}
    found = mt([metric(aggregation="rate", denominator=denominator)])
    assert [(v.rule, (v.path or "").endswith("/denominator/where/0/field")) for v in found] == [("MT-02", True)]


# MT-03 y Review Focus 4
def test_mt_03_duplicate_metric_ids() -> None:
    found = mt([metric("same"), metric("same", event="engine.run_closed")])
    assert [(v.rule, v.path) for v in found] == [("MT-03", f"{WHERE}#/metrics/1/id")]


def test_mt_04_judge_target_event_outside_the_catalog() -> None:
    found = mt([judge(target_event="engine.nope")])
    assert [v.rule for v in found] == ["MT-04"]


# T-EVAL-10 (parte estructural): los ids de plataforma están reservados
def test_mt_05_platform_ids_are_reserved() -> None:
    found = mt([metric("platform_pii_leak")])
    assert [(v.rule, v.path) for v in found] == [("MT-05", f"{WHERE}#/metrics/0/id")]


def test_mt_06_judge_profile_must_exist() -> None:
    found = mt([judge(judge_profile="no_existe@1.0.0")])
    assert [v.rule for v in found] == ["MT-06"]


# Review Focus 5: entradas enormes o con caracteres raros no rompen M1 ni producen mensajes sin acotar
def test_huge_and_odd_strings_are_clipped() -> None:
    odd = "ñ‮\x00" + "x" * 5000
    found = mt([metric(event=odd[:80], where=[{"field": odd[:80], "op": "eq", "value": odd}])])
    assert found
    assert all(len(v.message) <= 240 and len(v.path or "") <= 240 for v in found)


def test_validate_agent_still_reports_other_rules() -> None:
    found = validate_agent(agent(entry_flow="no_existe@1", metrics=[metric(event="engine.nope")]), registry())
    assert {"G0-02", "MT-01"} <= {v.rule for v in found}
```

- [ ] **Step 2: Verificar que fallan**

Run: `uv run pytest tests/m01/test_agent_metrics.py -v`
Expected: FAIL (`MT-01` etc. no se producen todavía: las listas devueltas están vacías).

- [ ] **Step 3: Implementar `flows/metrics.py`**

Crear `agent_core/flows/metrics.py`:

```python
"""Validación del DSL de métricas de un agente contra el catálogo cerrado de eventos (ADR 0020).

Reglas MT-01 a MT-06. Total y determinista: ninguna entrada hace que lance. Todo lo que se repite en un
mensaje o en una ruta se acota con `clip`.
"""

from decimal import Decimal

from agent_core.domain import (
    PLATFORM_METRIC_PREFIX,
    Agent,
    EntityKind,
    JudgeExpr,
    MetricDef,
    MetricExpr,
    ModelProfile,
    Predicate,
    RefSpec,
    catalog_fields,
)
from agent_core.flows.view import RegistryView
from agent_core.flows.violations import Violation, clip

_ORDER_OPS = frozenset({"lt", "le", "gt", "ge"})
_NUMERIC = frozenset({"int", "decimal"})


def _scalar_matches(kind: str, value: object) -> bool:
    if kind == "str":
        return isinstance(value, str)
    if kind == "bool":
        return isinstance(value, bool)
    return isinstance(value, int | Decimal) and not isinstance(value, bool)


def _predicate_problems(pred: Predicate, kind: str, at: str) -> list[tuple[str, str, str]]:
    values = pred.value if isinstance(pred.value, list) else [pred.value]
    if pred.op in _ORDER_OPS and kind not in _NUMERIC:
        return [("MT-02", f"{at}/field", clip(f"el operador {pred.op} exige un campo numérico y {clip(pred.field, 40)} es {kind}", 200))]
    if not all(_scalar_matches(kind, v) for v in values):
        return [("MT-02", f"{at}/value", clip(f"el valor no corresponde al tipo {kind} de {clip(pred.field, 40)}", 200))]
    return []


def _expr_problems(expr: MetricExpr, at: str) -> list[tuple[str, str, str]]:
    fields = catalog_fields(expr.event)
    if fields is None:
        return [("MT-01", f"{at}/event", clip(f"evento {clip(expr.event, 60)} fuera del catálogo", 200))]
    found: list[tuple[str, str, str]] = []
    for i, pred in enumerate(expr.where):
        kind = fields.get(pred.field)
        if kind is None:
            found.append(("MT-02", f"{at}/where/{i}/field",
                          clip(f"campo {clip(pred.field, 60)} no existe en {expr.event}", 200)))
        else:
            found += _predicate_problems(pred, kind, f"{at}/where/{i}")
    for i, name in enumerate(expr.group_by):
        if name not in fields:
            found.append(("MT-02", f"{at}/group_by/{i}", clip(f"campo {clip(name, 60)} no existe en {expr.event}", 200)))
    if expr.field is not None:
        kind = fields.get(expr.field)
        if kind is None:
            found.append(("MT-02", f"{at}/field", clip(f"campo {clip(expr.field, 60)} no existe en {expr.event}", 200)))
        elif kind not in _NUMERIC:
            found.append(("MT-02", f"{at}/field", clip(f"{clip(expr.field, 60)} no es numérico y {expr.aggregation} lo exige", 200)))
    if expr.denominator is not None:
        found += _expr_problems(expr.denominator, f"{at}/denominator")
    return found


def _judge_problems(expr: JudgeExpr, at: str, reg: RegistryView) -> list[tuple[str, str, str]]:
    found: list[tuple[str, str, str]] = []
    if catalog_fields(expr.target_event) is None:
        found.append(("MT-04", f"{at}/target_event", clip(f"evento {clip(expr.target_event, 60)} fuera del catálogo", 200)))
    ref = RefSpec.model_validate(f"{expr.judge_profile.id}@{expr.judge_profile.version}")
    if not isinstance(reg.resolve(EntityKind.model_profile, ref), ModelProfile):
        found.append(("MT-06", f"{at}/judge_profile", clip(f"model_profile {ref} no existe en el registro", 200)))
    return found


def _metric_problems(metric: MetricDef, at: str, reg: RegistryView) -> list[tuple[str, str, str]]:
    found: list[tuple[str, str, str]] = []
    if metric.id.startswith(PLATFORM_METRIC_PREFIX):
        found.append(("MT-05", f"{at}/id", clip(f"el prefijo {PLATFORM_METRIC_PREFIX} está reservado a la plataforma", 200)))
    if isinstance(metric.expr, MetricExpr):
        found += _expr_problems(metric.expr, f"{at}/expr")
    else:
        found += _judge_problems(metric.expr, f"{at}/expr", reg)
    return found


def validate_agent_metrics(agent: Agent, where: str, reg: RegistryView) -> list[Violation]:
    """MT-01 a MT-06 sobre `agent.metrics`. `where` es el origen del agente, como en `validate_agent`."""
    found: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    for index, metric in enumerate(agent.metrics):
        at = f"/metrics/{index}"
        if metric.id in seen:
            found.append(("MT-03", f"{at}/id", clip(f"id de métrica duplicado: {clip(metric.id, 60)}", 200)))
        seen.add(metric.id)
        found += _metric_problems(metric, at, reg)
    return [Violation(rule=rule, path=clip(f"{where}#{pointer}", 240), message=message)
            for rule, pointer, message in found]
```

Nota: si `RefSpec.model_validate` con un texto `id@versión` no es la forma que usa el repo, copiar la construcción de `RefSpec` que hace `agent_ref_sites` en `agent_core/flows/refs.py`.

- [ ] **Step 4: Engancharlo en `validate_agent`**

En `agent_core/flows/agent.py` añadir `from agent_core.flows.metrics import validate_agent_metrics` junto a los demás imports de `agent_core.flows`, y justo antes de `return sort_violations(found)` de `validate_agent` (línea 61):

```python
    found += validate_agent_metrics(agent, where, reg)
```

Actualizar el docstring de `validate_agent`: `"""G0-02 sobre las referencias del agente, G0-12 sobre sus plantillas del motor y MT-01 a MT-06 sobre sus métricas."""`

- [ ] **Step 5: Verificar que pasan**

Run: `uv run pytest tests/m01 -v`
Expected: PASS, incluidas `tests/m01/test_agent.py` y `test_public_api.py` sin cambios.
Si `test_huge_and_odd_strings_are_clipped` falla por una ruta mayor a 240, el `clip` del `where` ya cubre la ruta: revisar que `Violation.path` pase por `clip(..., 240)`.

- [ ] **Step 6: Documentar las reglas en el spec de M1**

En `docs/specs/motor/m01-validacion-estatica.md`, en la sección de chequeos por agente (§3.8), añadir:

```markdown
**Métricas del agente (ADR 0020).** `validate_agent` valida `Agent.metrics` contra el catálogo cerrado de eventos de M0:

| Regla | Qué rechaza |
|---|---|
| MT-01 | `event` fuera del catálogo |
| MT-02 | campo de `where`, `group_by` o `field` inexistente en el evento; `field` no numérico; valor de tipo distinto al del campo; operador de orden sobre un campo no numérico |
| MT-03 | id de métrica duplicado dentro del agente |
| MT-04 | `target_event` de un juez fuera del catálogo |
| MT-05 | id con el prefijo reservado `platform_` |
| MT-06 | `judge_profile` que no resuelve a un `model_profile` del registro |

Las formas inconsistentes (`field` en un `count`, `rate` sin denominador, ventana ausente o claves libres) las rechaza el esquema de M0, antes de llegar aquí.
```

- [ ] **Step 7: Commit**

```bash
git add agent_core/flows/metrics.py agent_core/flows/agent.py tests/m01/test_agent_metrics.py docs/specs/motor/m01-validacion-estatica.md
git commit -m "feat(m1): reglas MT-01 a MT-06 validan las metricas del agente contra el catalogo (ADR 0020)"
```

---

### Task 4: `EvalSuite` y comprobación de suite (registry)

**Files:**
- Create: `agent_core/registry/evaluation/__init__.py`
- Create: `agent_core/registry/evaluation/suite.py`
- Create: `agent_core/registry/evaluation/platform.py`
- Create: `tests/registry/__init__.py`
- Create: `tests/registry/support.py`
- Test: `tests/registry/test_suite.py`

**Interfaces:**
- Consumes: `Agent`, `MetricDef`, `Predicate`, `catalog_fields`, `EntityId`, `ExactVersion`, `Sha256Hex`, `UtcDatetime`, `JsonValue` (M0).
- Produces:
  - `EvalSuite(id, version, agent_id, scenarios: list[Scenario], thresholds: dict[str, MetricThreshold])`
  - `Scenario(id, source: ScriptedSource | DatasetSource, assertions: list[Assertion] = [], repetitions: int = 1)`
  - `ScriptedSource(kind="scripted", user_turns | signal, tool_fixtures, clock_start)`, `DatasetSource(kind="dataset", dataset_id, dataset_hash, reference_outcome=False)`
  - `Assertion(event, where: list[Predicate] = [], expect: Literal["at_least_one", "none"] = "at_least_one")`
  - `MetricThreshold(noise_margin: Decimal, floor: Decimal | None = None)`
  - `SuiteProblemCode` (StrEnum) y `SuiteProblem(code, path, message)`
  - `suite_problems(agent: Agent, suite: EvalSuite | None) -> list[SuiteProblem]`
  - `PLATFORM_GUARDRAILS: tuple[str, ...]` (en `platform.py`)
  - Helpers de test en `tests/registry/support.py`: `metric(...)`, `scenario(...)`, `suite(...)`, `thr(...)`, `yardstick(...)` (esta última se completa en la tarea 5).

- [ ] **Step 1: Crear los helpers de prueba**

Crear `tests/registry/__init__.py` vacío y `tests/registry/support.py`:

```python
from typing import Any

from agent_core.domain import MetricDef
from agent_core.registry.evaluation import EvalSuite


def metric(mid: str, role: str = "gate", higher: bool = True, event: str = "engine.turn_completed",
           **expr: Any) -> MetricDef:
    body: dict[str, Any] = {"event": event, "aggregation": "count", "window": "scenario"} | expr
    return MetricDef.model_validate(
        {"id": mid, "description": "d", "role": role, "higher_is_better": higher, "expr": body}
    )


def scenario(sid: str, repetitions: int = 1, **over: Any) -> dict[str, Any]:
    source = {"kind": "scripted", "user_turns": ["hola"], "tool_fixtures": {},
              "clock_start": "2026-01-01T00:00:00Z"}
    return {"id": sid, "source": source, "repetitions": repetitions} | over


def thr(noise: str = "0", floor: str | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {"noise_margin": noise}
    if floor is not None:
        out["floor"] = floor
    return out


def suite(scenarios: list[dict[str, Any]], thresholds: dict[str, dict[str, Any]] | None = None,
          agent_id: str = "atencion") -> EvalSuite:
    return EvalSuite.model_validate({"id": "suite", "version": "1.0.0", "agent_id": agent_id,
                                     "scenarios": scenarios, "thresholds": thresholds or {}})
```

- [ ] **Step 2: Escribir las pruebas de suite**

Crear `tests/registry/test_suite.py`:

```python
from copy import deepcopy
from typing import Any

import pytest
from pydantic import ValidationError

from agent_core.domain import Agent
from agent_core.registry.evaluation import (
    PLATFORM_GUARDRAILS,
    EvalSuite,
    SuiteProblemCode,
    suite_problems,
)
from tests.m01.cases import AGENT
from tests.registry.support import metric, scenario, suite, thr


def make_agent(*metrics: Any) -> Agent:
    data = deepcopy(AGENT) | {"metrics": [m.model_dump(mode="json") for m in metrics]}
    return Agent.model_validate(data)


def codes(problems: list[Any]) -> list[str]:
    return [p.code.value for p in problems]


def test_valid_suite_has_no_problems() -> None:
    agent = make_agent(metric("m_gate"), metric("m_mon", role="monitor"))
    assert suite_problems(agent, suite([scenario("s1")], {"m_gate": thr("0.02")})) == []


def test_scripted_source_needs_exactly_one_input() -> None:
    both = scenario("s1")
    both["source"] = both["source"] | {"signal": {"x": 1}}
    neither = scenario("s2")
    neither["source"] = neither["source"] | {"user_turns": None}
    for bad in (both, neither):
        with pytest.raises(ValidationError):
            suite([bad])


def test_suite_needs_scenarios_and_positive_repetitions() -> None:
    with pytest.raises(ValidationError):
        suite([])
    with pytest.raises(ValidationError):
        suite([scenario("s1", repetitions=0)])


def test_thresholds_are_finite_decimals() -> None:
    with pytest.raises(ValidationError):
        suite([scenario("s1")], {"m": thr("NaN")})
    with pytest.raises(ValidationError):
        suite([scenario("s1")], {"m": thr("-0.1")})


# T-EVAL-11
def test_agent_without_suite_is_not_publishable() -> None:
    assert codes(suite_problems(make_agent(metric("m_gate")), None)) == ["missing_suite"]


# Review Focus 3: sin suite no se pueden medir los guardarraíles de plataforma, aunque solo haya monitor
def test_agent_with_only_monitor_metrics_or_none_still_needs_a_suite() -> None:
    assert codes(suite_problems(make_agent(metric("m_mon", role="monitor")), None)) == ["missing_suite"]
    assert codes(suite_problems(make_agent(), None)) == ["missing_suite"]


def test_gate_and_guardrail_metrics_need_thresholds() -> None:
    agent = make_agent(metric("m_gate"), metric("m_guard", role="guardrail"), metric("m_mon", role="monitor"))
    problems = suite_problems(agent, suite([scenario("s1")], {"m_gate": thr()}))
    assert [(p.code.value, p.path) for p in problems] == [("missing_threshold", "/thresholds/m_guard")]


# Review Focus 4
def test_thresholds_for_unknown_metrics_and_duplicate_scenarios_are_rejected() -> None:
    agent = make_agent(metric("m_gate"))
    problems = suite_problems(
        agent, suite([scenario("s1"), scenario("s1")], {"m_gate": thr(), "ghost": thr()})
    )
    assert sorted(codes(problems)) == ["duplicate_scenario", "unknown_threshold_metric"]


def test_assertion_events_must_be_in_the_catalog() -> None:
    agent = make_agent(metric("m_gate"))
    bad = scenario("s1", assertions=[{"event": "engine.nope"}])
    good = scenario("s2", assertions=[{"event": "engine.escalated",
                                       "where": [{"field": "reason_code", "op": "eq", "value": "x"}]}])
    problems = suite_problems(agent, suite([bad, good], {"m_gate": thr()}))
    assert [(p.code.value, p.path) for p in problems] == [
        ("unknown_assertion_event", "/scenarios/0/assertions/0/event")
    ]


# T-EVAL-12
def test_dataset_source_is_disabled() -> None:
    agent = make_agent(metric("m_gate"))
    dataset = scenario("s1")
    dataset["source"] = {"kind": "dataset", "dataset_id": "casos_reales", "dataset_hash": "a" * 64}
    problems = suite_problems(agent, suite([dataset], {"m_gate": thr()}))
    assert [(p.code, p.path) for p in problems] == [
        (SuiteProblemCode.dataset_source_disabled, "/scenarios/0/source")
    ]


def test_suite_must_belong_to_the_agent() -> None:
    agent = make_agent(metric("m_gate"))
    other = suite([scenario("s1")], {"m_gate": thr()}, agent_id="otro")
    assert codes(suite_problems(agent, other)) == ["agent_mismatch"]


def test_problems_are_sorted_and_deterministic() -> None:
    agent = make_agent(metric("m_gate"), metric("m_guard", role="guardrail"))
    s = suite([scenario("s1"), scenario("s1")], {"ghost": thr()})
    first, second = suite_problems(agent, s), suite_problems(agent, s)
    assert first == second
    assert first == sorted(first, key=lambda p: (p.path, p.code.value))


def test_platform_guardrails_are_reserved_names() -> None:
    assert len(PLATFORM_GUARDRAILS) == len(set(PLATFORM_GUARDRAILS)) == 4
    assert all(name.startswith("platform_") for name in PLATFORM_GUARDRAILS)
    assert "platform_pii_leak" in PLATFORM_GUARDRAILS
    assert isinstance(EvalSuite, type)
```

- [ ] **Step 3: Verificar que fallan**

Run: `uv run pytest tests/registry/test_suite.py -v`
Expected: FAIL (`agent_core.registry.evaluation` no existe).

- [ ] **Step 4: Implementar `platform.py`**

Crear `agent_core/registry/evaluation/platform.py`:

```python
"""Guardarraíles de plataforma (ADR 0020, spec de evaluación §7).

Son universales: los define la plataforma y no cada agente, y ninguna propuesta puede editarlos. Sus ids
llevan el prefijo reservado `platform_` (M1 `MT-05` impide que un agente los declare). Se miden en todo
escenario y cualquier valor distinto de cero falla el gate.
"""

PLATFORM_GUARDRAILS: tuple[str, ...] = (
    "platform_pii_leak",
    "platform_unverified_success_claim",
    "platform_unverified_write",
    "platform_unapproved_knowledge_citation",
)
```

- [ ] **Step 5: Implementar `suite.py`**

Crear `agent_core/registry/evaluation/suite.py`:

```python
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
    Sha256Hex,
    UtcDatetime,
    catalog_fields,
)

NonNegativeDecimal = Annotated[Decimal, Field(ge=0, allow_inf_nan=False)]
FiniteDecimal = Annotated[Decimal, Field(allow_inf_nan=False)]


class MetricThreshold(Model):
    """Tolerancia frente a la base y piso mínimo (este último solo cuenta si no hay release base)."""
    noise_margin: NonNegativeDecimal
    floor: FiniteDecimal | None = None


class Assertion(Model):
    """Afirmación sobre los eventos de un escenario: aparece (o no) al menos un evento que cumple el filtro."""
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
            raise ValueError("un escenario guionado lleva `user_turns` (conversacional) o `signal` (task), no ambos")
        return self


class DatasetSource(Model):
    """Casos de un dataset real, referenciado por id y hash. DESACTIVADA (ADR 0020, tema #19)."""
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
        found.append(_problem(SuiteProblemCode.agent_mismatch, "/agent_id",
                              f"la suite es de {suite.agent_id} y el agente es {agent.id}"))
    seen: set[str] = set()
    for i, scenario in enumerate(suite.scenarios):
        at = f"/scenarios/{i}"
        if scenario.id in seen:
            found.append(_problem(SuiteProblemCode.duplicate_scenario, f"{at}/id",
                                  f"escenario duplicado: {scenario.id}"))
        seen.add(scenario.id)
        if isinstance(scenario.source, DatasetSource):
            found.append(_problem(SuiteProblemCode.dataset_source_disabled, f"{at}/source",
                                  "la fuente `dataset` está desactivada hasta que exista su ADR"))
        for j, assertion in enumerate(scenario.assertions):
            if catalog_fields(assertion.event) is None:
                found.append(_problem(SuiteProblemCode.unknown_assertion_event, f"{at}/assertions/{j}/event",
                                      f"evento {assertion.event[:60]} fuera del catálogo"))
    gating = {m.id for m in agent.metrics if m.role in ("gate", "guardrail")}
    declared = {m.id for m in agent.metrics}
    for metric_id in sorted(gating - set(suite.thresholds)):
        found.append(_problem(SuiteProblemCode.missing_threshold, f"/thresholds/{metric_id}",
                              f"la métrica {metric_id} no tiene umbral en la suite"))
    for metric_id in sorted(set(suite.thresholds) - declared):
        found.append(_problem(SuiteProblemCode.unknown_threshold_metric, f"/thresholds/{metric_id}",
                              f"la suite declara un umbral para {metric_id}, que el agente no tiene"))
    return sorted(found, key=lambda p: (p.path, p.code.value))
```

Verificar con `grep -n "JsonValue\|Sha256Hex\|UtcDatetime" agent_core/domain/__init__.py` que los tres se exportan desde `agent_core.domain`; si alguno no, importarlo de su submódulo (`agent_core.domain.json`, `agent_core.domain.base`) como hace `entities.py`.

- [ ] **Step 6: Exportar la interfaz del paquete**

Crear `agent_core/registry/evaluation/__init__.py`:

```python
"""Evaluación del registry (ADR 0020): suite, aflojamientos de la vara y veredicto del gate.

Funciones puras y sin Postgres; el servicio del registry y la unidad 6 (`EvalPort`) las consumen.
"""

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

__all__ = [
    "PLATFORM_GUARDRAILS",
    "Assertion",
    "DatasetSource",
    "EvalSuite",
    "MetricThreshold",
    "Scenario",
    "ScriptedSource",
    "SuiteProblem",
    "SuiteProblemCode",
    "suite_problems",
]
```

- [ ] **Step 7: Verificar que pasan, y las fronteras**

Run: `uv run pytest tests/registry/test_suite.py -v`
Expected: PASS.

Run: `uv run lint-imports`
Expected: todos los contratos en verde (`agent_core.registry` solo importa `domain`, `ports` y `flows`).

- [ ] **Step 8: Commit**

```bash
git add agent_core/registry tests/registry
git commit -m "feat(registry): EvalSuite, guardarrailes de plataforma y comprobacion de suite (ADR 0020)"
```

---

### Task 5: Clasificación de aflojamientos de la vara (`yardstick_loosened`)

**Files:**
- Create: `agent_core/registry/evaluation/yardstick.py`
- Modify: `agent_core/registry/evaluation/__init__.py` (exports)
- Test: `tests/registry/test_yardstick.py`
- Modify: `tests/registry/support.py` (añadir `yardstick`)

**Interfaces:**
- Consumes: `MetricDef`, `EvalSuite`, `Scenario`, `MetricThreshold`.
- Produces:
  - `Yardstick(metrics: list[MetricDef], suite: EvalSuite)`: definiciones y suite de una release.
  - `YardstickChange(kind: YardstickChangeKind, target: str, message: str)`
  - `classify_yardstick_change(base: Yardstick | None, cand: Yardstick) -> list[YardstickChange]`: solo los aflojamientos; lista vacía si es solo endurecimiento o si no hay base.
  - `metric_identity(metric: MetricDef) -> tuple[str, bool, object]`: la parte de una métrica que cuenta como "vara" (`role`, `higher_is_better`, `expr`); la usa también el gate.
  - Solo las métricas `gate` y `guardrail` forman parte de la vara: borrar o cambiar una métrica `monitor` no se marca.

- [ ] **Step 1: Añadir el helper `yardstick` a los soportes de prueba**

Añadir al final de `tests/registry/support.py`:

```python
from agent_core.registry.evaluation import Yardstick  # noqa: E402


def yardstick(metrics: list[MetricDef], scenarios: list[dict[str, Any]],
              thresholds: dict[str, dict[str, Any]] | None = None) -> Yardstick:
    return Yardstick(metrics=metrics, suite=suite(scenarios, thresholds))
```

(Si el orden de imports molesta a `ruff`, mover ese `import` al bloque de imports del principio del archivo.)

- [ ] **Step 2: Escribir las pruebas**

Crear `tests/registry/test_yardstick.py`:

```python
from typing import Any

import pytest

from agent_core.registry.evaluation import classify_yardstick_change
from tests.registry.support import metric, scenario, thr, yardstick


def kinds(base: Any, cand: Any) -> list[str]:
    return [c.kind for c in classify_yardstick_change(base, cand)]


def base_yardstick() -> Any:
    return yardstick(
        [metric("m_gate"), metric("m_guard", role="guardrail", higher=False)],
        [scenario("s1", repetitions=3), scenario("s2")],
        {"m_gate": thr("0.05", "0.6"), "m_guard": thr("0", "0")},
    )


def test_no_base_means_nothing_to_loosen() -> None:
    assert classify_yardstick_change(None, base_yardstick()) == []


def test_identical_yardstick_is_not_loosened() -> None:
    assert kinds(base_yardstick(), base_yardstick()) == []


# T-EVAL-08: solo endurecer no se marca
def test_tightening_is_not_flagged() -> None:
    cand = yardstick(
        [metric("m_gate", role="guardrail"), metric("m_guard", role="guardrail", higher=False),
         metric("m_new"), metric("m_mon", role="monitor")],
        [scenario("s1", repetitions=5), scenario("s2"), scenario("s3")],
        {"m_gate": thr("0.01", "0.8"), "m_guard": thr("0", "0"), "m_new": thr("0.1", "0.3")},
    )
    assert kinds(base_yardstick(), cand) == []


# T-EVAL-08: cada tipo de aflojamiento
def test_removing_a_gate_metric_is_flagged() -> None:
    cand = yardstick([metric("m_guard", role="guardrail", higher=False)], [scenario("s1", 3), scenario("s2")],
                     {"m_guard": thr("0", "0")})
    assert kinds(base_yardstick(), cand) == ["metric_removed"]


def test_degrading_a_role_is_flagged() -> None:
    cand = yardstick([metric("m_gate", role="monitor"), metric("m_guard", role="gate", higher=False)],
                     [scenario("s1", 3), scenario("s2")],
                     {"m_gate": thr("0.05", "0.6"), "m_guard": thr("0", "0")})
    assert sorted(kinds(base_yardstick(), cand)) == ["role_degraded", "role_degraded"]


def test_changing_an_expression_direction_or_judge_is_flagged() -> None:
    cand = yardstick([metric("m_gate", event="engine.run_closed"), metric("m_guard", role="guardrail", higher=True)],
                     [scenario("s1", 3), scenario("s2")],
                     {"m_gate": thr("0.05", "0.6"), "m_guard": thr("0", "0")})
    assert kinds(base_yardstick(), cand) == ["metric_changed", "metric_changed"]


def test_description_or_alert_changes_are_not_flagged() -> None:
    changed = metric("m_gate").model_copy(update={"description": "otra descripcion"})
    cand = yardstick([changed, metric("m_guard", role="guardrail", higher=False)],
                     [scenario("s1", 3), scenario("s2")],
                     {"m_gate": thr("0.05", "0.6"), "m_guard": thr("0", "0")})
    assert kinds(base_yardstick(), cand) == []


def test_removing_a_threshold_is_flagged() -> None:
    cand = yardstick([metric("m_gate"), metric("m_guard", role="guardrail", higher=False)],
                     [scenario("s1", 3), scenario("s2")], {"m_guard": thr("0", "0")})
    assert kinds(base_yardstick(), cand) == ["threshold_removed"]


@pytest.mark.parametrize(
    ("floor", "expected"),
    [("0.5", ["floor_loosened"]), (None, ["floor_loosened"]), ("0.6", []), ("0.9", [])],
)
def test_floor_loosening_when_higher_is_better(floor: str | None, expected: list[str]) -> None:
    cand = yardstick([metric("m_gate"), metric("m_guard", role="guardrail", higher=False)],
                     [scenario("s1", 3), scenario("s2")],
                     {"m_gate": thr("0.05", floor), "m_guard": thr("0", "0")})
    assert kinds(base_yardstick(), cand) == expected


# Review Focus 2: con "menor es mejor" el piso es un máximo
@pytest.mark.parametrize(("floor", "expected"), [("1", ["floor_loosened"]), ("0", []), ("-1", [])])
def test_floor_loosening_when_lower_is_better(floor: str, expected: list[str]) -> None:
    cand = yardstick([metric("m_gate"), metric("m_guard", role="guardrail", higher=False)],
                     [scenario("s1", 3), scenario("s2")],
                     {"m_gate": thr("0.05", "0.6"), "m_guard": thr("0", floor)})
    assert kinds(base_yardstick(), cand) == expected


def test_widening_the_noise_margin_is_flagged() -> None:
    cand = yardstick([metric("m_gate"), metric("m_guard", role="guardrail", higher=False)],
                     [scenario("s1", 3), scenario("s2")],
                     {"m_gate": thr("0.2", "0.6"), "m_guard": thr("0", "0")})
    assert kinds(base_yardstick(), cand) == ["noise_widened"]


def test_removing_changing_or_weakening_scenarios_is_flagged() -> None:
    metrics = [metric("m_gate"), metric("m_guard", role="guardrail", higher=False)]
    thresholds = {"m_gate": thr("0.05", "0.6"), "m_guard": thr("0", "0")}
    removed = yardstick(metrics, [scenario("s1", 3)], thresholds)
    changed_turns = scenario("s2")
    changed_turns["source"] = changed_turns["source"] | {"user_turns": ["otra cosa"]}
    changed = yardstick(metrics, [scenario("s1", 3), changed_turns], thresholds)
    fewer_runs = yardstick(metrics, [scenario("s1", 1), scenario("s2")], thresholds)
    assert kinds(base_yardstick(), removed) == ["scenario_removed"]
    assert kinds(base_yardstick(), changed) == ["scenario_changed"]
    assert kinds(base_yardstick(), fewer_runs) == ["repetitions_lowered"]


def test_monitor_metrics_are_not_part_of_the_yardstick() -> None:
    base = yardstick([metric("m_mon", role="monitor")], [scenario("s1")])
    cand_removed = yardstick([], [scenario("s1")])
    cand_changed = yardstick([metric("m_mon", role="monitor", event="engine.run_closed")], [scenario("s1")])
    assert kinds(base, cand_removed) == [] and kinds(base, cand_changed) == []


def test_output_is_sorted_and_deterministic() -> None:
    cand = yardstick([metric("m_gate", role="monitor")], [scenario("s2")], {})
    first = classify_yardstick_change(base_yardstick(), cand)
    assert first == classify_yardstick_change(base_yardstick(), cand)
    assert first == sorted(first, key=lambda c: (c.kind, c.target))
    assert all(c.target and c.message for c in first)
```

- [ ] **Step 3: Verificar que fallan**

Run: `uv run pytest tests/registry/test_yardstick.py -v`
Expected: FAIL (`classify_yardstick_change` no existe).

- [ ] **Step 4: Implementar `yardstick.py`**

Crear `agent_core/registry/evaluation/yardstick.py`:

```python
"""Clasificación de cambios en la vara de evaluación (ADR 0020, spec de evaluación §6.2).

Solo se marca lo que afloja la vara. Endurecer (añadir escenarios, subir pisos, promover roles, añadir
métricas) no se marca. Solo las métricas `gate` y `guardrail` forman parte de la vara.
"""

from decimal import Decimal
from enum import StrEnum

from agent_core.domain import MetricDef, Model
from agent_core.registry.evaluation.suite import EvalSuite, MetricThreshold

_RANK = {"monitor": 1, "gate": 2, "guardrail": 3}


class Yardstick(Model):
    """Las definiciones de métricas y la suite de una release: con lo que se mide a la siguiente."""
    metrics: list[MetricDef]
    suite: EvalSuite


class YardstickChangeKind(StrEnum):
    metric_removed = "metric_removed"
    role_degraded = "role_degraded"
    metric_changed = "metric_changed"
    threshold_removed = "threshold_removed"
    floor_loosened = "floor_loosened"
    noise_widened = "noise_widened"
    scenario_removed = "scenario_removed"
    scenario_changed = "scenario_changed"
    repetitions_lowered = "repetitions_lowered"


class YardstickChange(Model):
    kind: YardstickChangeKind
    target: str
    message: str


def metric_identity(metric: MetricDef) -> tuple[str, bool, object]:
    """Lo que de una métrica cuenta como vara: papel, dirección y expresión (no descripción ni alerta)."""
    return (metric.role, metric.higher_is_better, metric.expr)


def _gating(metrics: list[MetricDef]) -> dict[str, MetricDef]:
    return {m.id: m for m in metrics if m.role in ("gate", "guardrail")}


def _floor_loosened(base: MetricThreshold, cand: MetricThreshold, higher_is_better: bool) -> bool:
    if base.floor is None:
        return False
    if cand.floor is None:
        return True
    return cand.floor < base.floor if higher_is_better else cand.floor > base.floor


def _metric_changes(base: Yardstick, cand: Yardstick) -> list[YardstickChange]:
    found: list[YardstickChange] = []
    cand_all = {m.id: m for m in cand.metrics}
    for metric_id, base_metric in sorted(_gating(base.metrics).items()):
        cand_metric = cand_all.get(metric_id)
        if cand_metric is None:
            found.append(YardstickChange(kind=YardstickChangeKind.metric_removed, target=metric_id,
                                         message=f"la métrica {metric_id} ya no está declarada"))
            continue
        if _RANK[cand_metric.role] < _RANK[base_metric.role]:
            found.append(YardstickChange(kind=YardstickChangeKind.role_degraded, target=metric_id,
                                         message=f"{metric_id} pasa de {base_metric.role} a {cand_metric.role}"))
            continue
        if metric_identity(cand_metric) != metric_identity(base_metric) and cand_metric.role == base_metric.role:
            found.append(YardstickChange(kind=YardstickChangeKind.metric_changed, target=metric_id,
                                         message=f"cambió la expresión o la dirección de {metric_id}"))
            continue
        base_thr = base.suite.thresholds.get(metric_id)
        cand_thr = cand.suite.thresholds.get(metric_id)
        if base_thr is None:
            continue
        if cand_thr is None:
            found.append(YardstickChange(kind=YardstickChangeKind.threshold_removed, target=metric_id,
                                         message=f"{metric_id} ya no tiene umbral en la suite"))
            continue
        if _floor_loosened(base_thr, cand_thr, base_metric.higher_is_better):
            found.append(YardstickChange(kind=YardstickChangeKind.floor_loosened, target=metric_id,
                                         message=f"el piso de {metric_id} se aflojó"))
        if cand_thr.noise_margin > base_thr.noise_margin:
            found.append(YardstickChange(kind=YardstickChangeKind.noise_widened, target=metric_id,
                                         message=f"el margen de ruido de {metric_id} aumentó"))
    return found


def _scenario_changes(base: Yardstick, cand: Yardstick) -> list[YardstickChange]:
    found: list[YardstickChange] = []
    cand_by_id = {s.id: s for s in cand.suite.scenarios}
    for scenario in sorted(base.suite.scenarios, key=lambda s: s.id):
        other = cand_by_id.get(scenario.id)
        if other is None:
            found.append(YardstickChange(kind=YardstickChangeKind.scenario_removed, target=scenario.id,
                                         message=f"el escenario {scenario.id} ya no está en la suite"))
        elif other != scenario:
            same_but_runs = other.model_copy(update={"repetitions": scenario.repetitions}) == scenario
            if same_but_runs and other.repetitions >= scenario.repetitions:
                continue  # solo más repeticiones: endurece
            kind = YardstickChangeKind.repetitions_lowered if same_but_runs else YardstickChangeKind.scenario_changed
            text = "bajaron las repeticiones" if same_but_runs else "cambió el escenario"
            found.append(YardstickChange(kind=kind, target=scenario.id, message=f"{text}: {scenario.id}"))
    return found


def classify_yardstick_change(base: Yardstick | None, cand: Yardstick) -> list[YardstickChange]:
    """Los cambios que aflojan la vara de `cand` frente a `base`. Sin base no hay nada que aflojar."""
    if base is None:
        return []
    found = [*_metric_changes(base, cand), *_scenario_changes(base, cand)]
    return sorted(found, key=lambda c: (c.kind.value, c.target))
```

Quitar el import de `Decimal` si `ruff` lo marca como no usado.

- [ ] **Step 5: Exportar**

En `agent_core/registry/evaluation/__init__.py` añadir:

```python
from agent_core.registry.evaluation.yardstick import (
    Yardstick,
    YardstickChange,
    YardstickChangeKind,
    classify_yardstick_change,
    metric_identity,
)
```

y los cinco nombres en `__all__` (manteniendo el orden alfabético que `ruff` exige para `RUF022`).

- [ ] **Step 6: Verificar que pasan**

Run: `uv run pytest tests/registry -v`
Expected: PASS.
Si `test_tightening_is_not_flagged` falla en `m_gate`, confirmar que la promoción `gate → guardrail` se trata como endurecimiento: `_RANK` sube, así que no entra en `role_degraded`, y el cambio de `role` no debe contar como `metric_changed` (la condición exige el mismo rol).

- [ ] **Step 7: Commit**

```bash
git add agent_core/registry/evaluation tests/registry
git commit -m "feat(registry): clasificacion de aflojamientos de la vara de evaluacion (ADR 0020)"
```

---

### Task 6: Veredicto del gate con doble vara

**Files:**
- Create: `agent_core/registry/evaluation/gate.py`
- Modify: `agent_core/registry/evaluation/__init__.py` (exports)
- Test: `tests/registry/test_gate.py`

**Interfaces:**
- Consumes: `Yardstick`, `metric_identity` (tarea 5); `PLATFORM_GUARDRAILS` (tarea 4); `MetricDef`.
- Produces:
  - `EvalReport(status: Literal["ok", "failed_infra"] = "ok", metrics: dict[str, Decimal], scenarios: dict[str, bool])`: lo que devuelve `EvalPort.run` (la unidad 6 lo implementa).
  - `GateRuns(base_on_old: EvalReport | None, cand_on_old: EvalReport | None, cand_on_new: EvalReport)`: la suite y las definiciones de la base corridas sobre base y candidata, y la suite de la candidata corrida sobre la candidata.
  - `GateItem(metric_id, phase, role, value, base_value, threshold, passed, reason)` y `Verdict(status: Literal["passed", "failed", "failed_infra"], items: list[GateItem])`.
  - `evaluate_gate(base: Yardstick | None, cand: Yardstick, runs: GateRuns) -> Verdict`.
  - `not_worse(candidate, base, margin, higher_is_better) -> bool` y `meets_floor(value, floor, higher_is_better) -> bool`.
  - Reglas: spec de evaluación §6.1. Los guardarraíles de plataforma deben valer 0 en la candidata, y si hay base además no pueden empeorar frente a ella en la suite vieja.

- [ ] **Step 1: Escribir las pruebas**

Crear `tests/registry/test_gate.py`:

```python
from decimal import Decimal
from typing import Any

import pytest

from agent_core.registry.evaluation import (
    PLATFORM_GUARDRAILS,
    EvalReport,
    GateRuns,
    evaluate_gate,
    meets_floor,
    not_worse,
)
from tests.registry.support import metric, scenario, thr, yardstick

ZEROS = {pid: "0" for pid in PLATFORM_GUARDRAILS}


def report(metrics: dict[str, str], scenarios: dict[str, bool] | None = None, status: str = "ok",
           platform: dict[str, str] | None = None) -> EvalReport:
    values = {**(ZEROS if platform is None else platform), **metrics}
    return EvalReport.model_validate(
        {"status": status, "metrics": values, "scenarios": scenarios if scenarios is not None else {"s1": True}}
    )


def base_yardstick() -> Any:
    return yardstick(
        [metric("quality"), metric("speed"), metric("leaks", role="guardrail", higher=False)],
        [scenario("s1")],
        {"quality": thr("0.05", "0.5"), "speed": thr("0.1", "0.4"), "leaks": thr("0", "0")},
    )


def failed(verdict: Any) -> list[str]:
    return sorted(i.metric_id for i in verdict.items if not i.passed)


def test_direction_helpers() -> None:
    assert not_worse(Decimal("0.96"), Decimal("1"), Decimal("0.05"), True)
    assert not not_worse(Decimal("0.9"), Decimal("1"), Decimal("0.05"), True)
    assert not_worse(Decimal("1.04"), Decimal("1"), Decimal("0.05"), False)
    assert not not_worse(Decimal("1.1"), Decimal("1"), Decimal("0.05"), False)
    assert meets_floor(Decimal("0.5"), Decimal("0.5"), True) and not meets_floor(Decimal("0.4"), Decimal("0.5"), True)
    assert meets_floor(Decimal("3"), Decimal("3"), False) and not meets_floor(Decimal("4"), Decimal("3"), False)


# Sin base: solo vara nueva contra los pisos
def test_no_base_passes_when_every_metric_meets_its_floor() -> None:
    cand = base_yardstick()
    runs = GateRuns(base_on_old=None, cand_on_old=None,
                    cand_on_new=report({"quality": "0.7", "speed": "0.5", "leaks": "0"}))
    verdict = evaluate_gate(None, cand, runs)
    assert verdict.status == "passed"
    assert {i.metric_id for i in verdict.items} >= {"quality", "speed", "leaks", *PLATFORM_GUARDRAILS}


# T-EVAL-13
def test_no_base_fails_when_a_floor_is_missing_or_not_met() -> None:
    no_floor = yardstick([metric("quality")], [scenario("s1")], {"quality": thr("0.05")})
    runs = GateRuns(base_on_old=None, cand_on_old=None, cand_on_new=report({"quality": "0.9"}))
    verdict = evaluate_gate(None, no_floor, runs)
    assert verdict.status == "failed" and failed(verdict) == ["quality"]
    below = GateRuns(base_on_old=None, cand_on_old=None,
                     cand_on_new=report({"quality": "0.1", "speed": "0.5", "leaks": "0"}))
    assert failed(evaluate_gate(None, base_yardstick(), below)) == ["quality"]


def test_lower_is_better_floor_is_a_ceiling() -> None:
    cand = yardstick([metric("leaks", role="guardrail", higher=False)], [scenario("s1")],
                     {"leaks": thr("0", "2")})
    over = GateRuns(base_on_old=None, cand_on_old=None, cand_on_new=report({"leaks": "3"}))
    under = GateRuns(base_on_old=None, cand_on_old=None, cand_on_new=report({"leaks": "2"}))
    assert failed(evaluate_gate(None, cand, over)) == ["leaks"]
    assert evaluate_gate(None, cand, under).status == "passed"


# T-EVAL-05: un guardarraíl que empeora falla el gate aunque una métrica gate mejore
def test_a_worse_guardrail_fails_even_if_a_gate_metric_improves() -> None:
    base = base_yardstick()
    runs = GateRuns(
        base_on_old=report({"quality": "0.6", "speed": "0.5", "leaks": "0"}),
        cand_on_old=report({"quality": "0.9", "speed": "0.5", "leaks": "1"}),
        cand_on_new=report({"quality": "0.9", "speed": "0.5", "leaks": "1"}),
    )
    verdict = evaluate_gate(base, base, runs)
    # La métrica `leaks` no cambió respecto de la base: solo la juzga la vara vieja, así que falla una vez.
    assert verdict.status == "failed" and failed(verdict) == ["leaks"]


# T-EVAL-06: cada métrica gate por separado; el ruido se tolera
def test_each_gate_metric_is_judged_on_its_own() -> None:
    base = base_yardstick()
    old_base = report({"quality": "0.80", "speed": "0.50", "leaks": "0"})
    within_noise = report({"quality": "0.76", "speed": "0.45", "leaks": "0"})
    regressed = report({"quality": "0.95", "speed": "0.20", "leaks": "0"})
    ok = evaluate_gate(base, base, GateRuns(base_on_old=old_base, cand_on_old=within_noise, cand_on_new=within_noise))
    bad = evaluate_gate(base, base, GateRuns(base_on_old=old_base, cand_on_old=regressed, cand_on_new=regressed))
    assert ok.status == "passed"
    assert bad.status == "failed" and "speed" in failed(bad) and "quality" not in failed(bad)


# T-EVAL-07: la vara vieja sigue vigente aunque la propuesta cambie o borre métricas
def test_the_base_yardstick_still_applies_when_the_candidate_drops_or_loosens_it() -> None:
    base = base_yardstick()
    cand = yardstick([metric("quality", role="monitor")], [scenario("s1")], {})  # borra speed y leaks, degrada quality
    old_base = report({"quality": "0.80", "speed": "0.50", "leaks": "0"})
    cand_old = report({"quality": "0.30", "speed": "0.10", "leaks": "0"})
    verdict = evaluate_gate(base, cand, GateRuns(base_on_old=old_base, cand_on_old=cand_old, cand_on_new=cand_old))
    assert verdict.status == "failed"
    assert {"quality", "speed"} <= set(failed(verdict))


# Review Focus 1: una métrica sin valor es un fallo, no un pase
def test_a_metric_without_value_fails() -> None:
    base = base_yardstick()
    old_base = report({"quality": "0.8", "speed": "0.5", "leaks": "0"})
    cand_old = EvalReport(metrics={pid: Decimal(0) for pid in PLATFORM_GUARDRAILS} | {"leaks": Decimal(0)},
                          scenarios={"s1": True})  # faltan quality y speed
    verdict = evaluate_gate(base, base, GateRuns(base_on_old=old_base, cand_on_old=cand_old, cand_on_new=cand_old))
    assert verdict.status == "failed" and {"quality", "speed"} <= set(failed(verdict))
    missing_base = EvalReport(metrics={"leaks": Decimal(0)}, scenarios={"s1": True})
    v2 = evaluate_gate(base, base, GateRuns(base_on_old=missing_base, cand_on_old=old_base, cand_on_new=old_base))
    assert "quality" in failed(v2)


def test_a_scenario_that_passed_in_the_base_and_fails_in_the_candidate_fails() -> None:
    base = base_yardstick()
    good = report({"quality": "0.8", "speed": "0.5", "leaks": "0"}, {"s1": True})
    broken = report({"quality": "0.8", "speed": "0.5", "leaks": "0"}, {"s1": False})
    verdict = evaluate_gate(base, base, GateRuns(base_on_old=good, cand_on_old=broken, cand_on_new=broken))
    assert "scenario/s1" in failed(verdict)
    assert verdict.status == "failed"


# Vara nueva: métricas y escenarios nuevos o modificados
def test_new_metrics_and_scenarios_must_meet_the_new_yardstick() -> None:
    base = base_yardstick()
    cand = yardstick(
        [metric("quality"), metric("speed"), metric("leaks", role="guardrail", higher=False), metric("fresh")],
        [scenario("s1"), scenario("s_new")],
        {"quality": thr("0.05", "0.5"), "speed": thr("0.1", "0.4"), "leaks": thr("0", "0"), "fresh": thr("0", "0.5")},
    )
    old = report({"quality": "0.8", "speed": "0.5", "leaks": "0"})
    ok_new = report({"quality": "0.8", "speed": "0.5", "leaks": "0", "fresh": "0.6"}, {"s1": True, "s_new": True})
    bad_new = report({"quality": "0.8", "speed": "0.5", "leaks": "0", "fresh": "0.1"}, {"s1": True, "s_new": False})
    assert evaluate_gate(base, cand, GateRuns(base_on_old=old, cand_on_old=old, cand_on_new=ok_new)).status == "passed"
    verdict = evaluate_gate(base, cand, GateRuns(base_on_old=old, cand_on_old=old, cand_on_new=bad_new))
    assert verdict.status == "failed" and {"fresh", "scenario/s_new"} <= set(failed(verdict))


def test_a_new_metric_without_floor_fails() -> None:
    base = base_yardstick()
    cand = yardstick([metric("quality"), metric("speed"), metric("leaks", role="guardrail", higher=False),
                      metric("fresh")], [scenario("s1")],
                     {"quality": thr("0.05", "0.5"), "speed": thr("0.1", "0.4"), "leaks": thr("0", "0"),
                      "fresh": thr("0")})
    old = report({"quality": "0.8", "speed": "0.5", "leaks": "0", "fresh": "1"})
    assert failed(evaluate_gate(base, cand, GateRuns(base_on_old=old, cand_on_old=old, cand_on_new=old))) == ["fresh"]


# Guardarraíles de plataforma
def test_platform_guardrails_must_be_zero() -> None:
    runs = GateRuns(base_on_old=None, cand_on_old=None,
                    cand_on_new=report({"quality": "0.9", "speed": "0.9", "leaks": "0"},
                                       platform=ZEROS | {"platform_pii_leak": "1"}))
    verdict = evaluate_gate(None, base_yardstick(), runs)
    assert verdict.status == "failed" and failed(verdict) == ["platform_pii_leak"]


def test_a_platform_guardrail_that_was_not_measured_fails() -> None:
    partial = {pid: "0" for pid in PLATFORM_GUARDRAILS[:-1]}
    runs = GateRuns(base_on_old=None, cand_on_old=None,
                    cand_on_new=report({"quality": "0.9", "speed": "0.9", "leaks": "0"}, platform=partial))
    assert failed(evaluate_gate(None, base_yardstick(), runs)) == [PLATFORM_GUARDRAILS[-1]]


# T-EVAL-15
@pytest.mark.parametrize("which", ["base_on_old", "cand_on_old", "cand_on_new"])
def test_failed_infra_is_not_a_verdict(which: str) -> None:
    base = base_yardstick()
    ok = report({"quality": "0.8", "speed": "0.5", "leaks": "0"})
    broken = EvalReport(status="failed_infra", metrics={}, scenarios={})
    runs = {"base_on_old": ok, "cand_on_old": ok, "cand_on_new": ok} | {which: broken}
    verdict = evaluate_gate(base, base, GateRuns(**runs))
    assert verdict.status == "failed_infra" and verdict.items == []


def test_a_base_without_its_runs_is_a_programming_error() -> None:
    with pytest.raises(ValueError):
        evaluate_gate(base_yardstick(), base_yardstick(),
                      GateRuns(base_on_old=None, cand_on_old=None, cand_on_new=report({})))


def test_items_carry_value_base_and_threshold() -> None:
    base = base_yardstick()
    old_base = report({"quality": "0.80", "speed": "0.50", "leaks": "0"})
    cand_old = report({"quality": "0.85", "speed": "0.50", "leaks": "0"})
    verdict = evaluate_gate(base, base, GateRuns(base_on_old=old_base, cand_on_old=cand_old, cand_on_new=cand_old))
    item = next(i for i in verdict.items if i.metric_id == "quality" and i.phase == "base_yardstick")
    assert (item.value, item.base_value, item.threshold, item.role) == (
        Decimal("0.85"), Decimal("0.80"), Decimal("0.05"), "gate")
```

- [ ] **Step 2: Verificar que fallan**

Run: `uv run pytest tests/registry/test_gate.py -v`
Expected: FAIL (`evaluate_gate` no existe).

- [ ] **Step 3: Implementar `gate.py`**

Crear `agent_core/registry/evaluation/gate.py`:

```python
"""Veredicto del gate de evaluación con doble vara (ADR 0020, spec de evaluación §6).

Función pura: recibe las definiciones y los reportes ya medidos (los produce `EvalPort`, unidad 6) y decide.
Sin puntaje compuesto: cada métrica se juzga por separado y basta una que falle.
"""

from decimal import Decimal
from typing import Literal

from pydantic import Field

from agent_core.domain import MetricDef, Model
from agent_core.domain.metrics import MetricRole
from agent_core.registry.evaluation.platform import PLATFORM_GUARDRAILS
from agent_core.registry.evaluation.yardstick import Yardstick, metric_identity

Phase = Literal["base_yardstick", "new_yardstick", "platform"]


class EvalReport(Model):
    """Lo que devuelve `EvalPort.run`: valor por métrica (incluidos los `platform_*`) y pase por escenario."""
    status: Literal["ok", "failed_infra"] = "ok"
    metrics: dict[str, Decimal] = Field(default_factory=dict)
    scenarios: dict[str, bool] = Field(default_factory=dict)


class GateRuns(Model):
    """Las mediciones que necesita el gate. `base_on_old` y `cand_on_old` son obligatorias si hay base."""
    base_on_old: EvalReport | None = None   # vara vieja medida sobre la base
    cand_on_old: EvalReport | None = None   # vara vieja medida sobre la candidata
    cand_on_new: EvalReport                 # vara nueva medida sobre la candidata


class GateItem(Model):
    metric_id: str
    phase: Phase
    role: MetricRole | None = None
    value: Decimal | None = None
    base_value: Decimal | None = None
    threshold: Decimal | None = None
    passed: bool
    reason: str = ""


class Verdict(Model):
    status: Literal["passed", "failed", "failed_infra"]
    items: list[GateItem]


def not_worse(candidate: Decimal, base: Decimal, margin: Decimal, higher_is_better: bool) -> bool:
    """La candidata no es peor que la base, con `margin` de tolerancia en la dirección de la métrica."""
    return candidate >= base - margin if higher_is_better else candidate <= base + margin


def meets_floor(value: Decimal, floor: Decimal, higher_is_better: bool) -> bool:
    """El piso es un mínimo si mayor es mejor y un máximo si menor es mejor."""
    return value >= floor if higher_is_better else value <= floor


def _gating(metrics: list[MetricDef]) -> list[MetricDef]:
    return sorted((m for m in metrics if m.role in ("gate", "guardrail")), key=lambda m: m.id)


def _base_items(base: Yardstick, base_run: EvalReport, cand_run: EvalReport) -> list[GateItem]:
    items: list[GateItem] = []
    for metric in _gating(base.metrics):
        threshold = base.suite.thresholds.get(metric.id)
        margin = Decimal(0) if metric.role == "guardrail" or threshold is None else threshold.noise_margin
        base_value, value = base_run.metrics.get(metric.id), cand_run.metrics.get(metric.id)
        if base_value is None or value is None:
            items.append(GateItem(metric_id=metric.id, phase="base_yardstick", role=metric.role, value=value,
                                  base_value=base_value, threshold=margin, passed=False,
                                  reason="la métrica no se pudo calcular en la base o en la candidata"))
            continue
        ok = not_worse(value, base_value, margin, metric.higher_is_better)
        items.append(GateItem(metric_id=metric.id, phase="base_yardstick", role=metric.role, value=value,
                              base_value=base_value, threshold=margin, passed=ok,
                              reason="" if ok else "empeora frente a la base"))
    for scenario in sorted(base.suite.scenarios, key=lambda s: s.id):
        if base_run.scenarios.get(scenario.id) is True and cand_run.scenarios.get(scenario.id) is not True:
            items.append(GateItem(metric_id=f"scenario/{scenario.id}", phase="base_yardstick", passed=False,
                                  reason="el escenario pasaba en la base y falla en la candidata"))
    return items


def _new_items(base: Yardstick | None, cand: Yardstick, run: EvalReport) -> list[GateItem]:
    items: list[GateItem] = []
    base_metrics = {m.id: m for m in _gating(base.metrics)} if base else {}
    base_scenarios = {s.id: s for s in base.suite.scenarios} if base else {}
    for metric in _gating(cand.metrics):
        previous = base_metrics.get(metric.id)
        if previous is not None and metric_identity(previous) == metric_identity(metric):
            continue  # sin cambios: ya lo juzgó la vara vieja
        threshold = cand.suite.thresholds.get(metric.id)
        floor = threshold.floor if threshold else None
        value = run.metrics.get(metric.id)
        if floor is None:
            ok, reason = False, "la métrica es nueva o cambió y no declara piso"
        elif value is None:
            ok, reason = False, "la métrica no se pudo calcular"
        else:
            ok = meets_floor(value, floor, metric.higher_is_better)
            reason = "" if ok else "no alcanza el piso"
        items.append(GateItem(metric_id=metric.id, phase="new_yardstick", role=metric.role, value=value,
                              threshold=floor, passed=ok, reason=reason))
    for scenario in sorted(cand.suite.scenarios, key=lambda s: s.id):
        if base_scenarios.get(scenario.id) == scenario:
            continue
        ok = run.scenarios.get(scenario.id) is True
        items.append(GateItem(metric_id=f"scenario/{scenario.id}", phase="new_yardstick", passed=ok,
                              reason="" if ok else "alguna aserción del escenario no se cumple"))
    return items


def _platform_items(runs: GateRuns) -> list[GateItem]:
    items: list[GateItem] = []
    for guardrail in PLATFORM_GUARDRAILS:
        value = runs.cand_on_new.metrics.get(guardrail)
        base_value = runs.base_on_old.metrics.get(guardrail) if runs.base_on_old else None
        old_value = runs.cand_on_old.metrics.get(guardrail) if runs.cand_on_old else None
        if value is None:
            ok, reason = False, "el guardarraíl de plataforma no se midió"
        elif value != 0:
            ok, reason = False, "el guardarraíl de plataforma debe valer 0"
        elif base_value is not None and old_value is not None and old_value > base_value:
            ok, reason = False, "el guardarraíl de plataforma empeora frente a la base"
        else:
            ok, reason = True, ""
        items.append(GateItem(metric_id=guardrail, phase="platform", role="guardrail", value=value,
                              base_value=base_value, threshold=Decimal(0), passed=ok, reason=reason))
    return items


def evaluate_gate(base: Yardstick | None, cand: Yardstick, runs: GateRuns) -> Verdict:
    """Veredicto de una candidata frente a su base (si existe). `failed_infra` no es pase ni fallo."""
    reports = [r for r in (runs.base_on_old, runs.cand_on_old, runs.cand_on_new) if r is not None]
    if any(report.status == "failed_infra" for report in reports):
        return Verdict(status="failed_infra", items=[])
    items: list[GateItem] = []
    if base is not None:
        if runs.base_on_old is None or runs.cand_on_old is None:
            raise ValueError("con release base se necesitan `base_on_old` y `cand_on_old`")
        items += _base_items(base, runs.base_on_old, runs.cand_on_old)
    items += _new_items(base, cand, runs.cand_on_new)
    items += _platform_items(runs)
    return Verdict(status="passed" if all(item.passed for item in items) else "failed", items=items)
```

- [ ] **Step 4: Exportar**

En `agent_core/registry/evaluation/__init__.py` añadir:

```python
from agent_core.registry.evaluation.gate import (
    EvalReport,
    GateItem,
    GateRuns,
    Verdict,
    evaluate_gate,
    meets_floor,
    not_worse,
)
```

y los siete nombres en `__all__` (orden alfabético para `RUF022`).

- [ ] **Step 5: Verificar que pasan**

Run: `uv run pytest tests/registry -v`
Expected: PASS (todas las pruebas de las tareas 4, 5 y 6).
Si `test_a_scenario_that_passed_in_the_base...` falla porque `EvalReport` valida `scenarios` como `dict[str, bool]`, comprobar que el reporte de la prueba usa claves `str` y valores `bool`.

- [ ] **Step 6: Commit**

```bash
git add agent_core/registry/evaluation tests/registry
git commit -m "feat(registry): evaluate_gate con doble vara, N metricas gate y guardarrailes de plataforma (ADR 0020)"
```

---

### Task 7: Documentación y verificación final

**Files:**
- Modify: `docs/specs/2026-09-30-evaluacion-y-metricas-design.md` (decisiones tomadas al implementar)
- Modify: `docs/specs/motor/00-indice.md` (enlace)
- Modify: `docs/specs/2026-09-29-registry-design.md` (§15 y §16)

**Interfaces:**
- Consumes: todo lo anterior.
- Produces: documentación consistente con el código y "Definición de terminado" marcada punto por punto.

- [ ] **Step 1: Comprobar que el spec refleja lo implementado**

El spec ya se alineó con este plan al escribirlo (suite obligatoria para todo agente, guardarraíles de plataforma en 0, solo `gate`/`guardrail` forman la vara, reglas `MT-01` a `MT-06`, prefijo `platform_`). Releerlo contra el código final y corregir cualquier diferencia que haya aparecido al implementar:

Run: `grep -n "MT-05\|platform_" docs/specs/2026-09-30-evaluacion-y-metricas-design.md`
Expected: aparecen las menciones de `MT-05` y del prefijo `platform_` en §7 y §12. Si el código se desvió del spec, cambiar el spec en este mismo commit (CLAUDE.md: «actualiza el spec del módulo en el mismo cambio»).

- [ ] **Step 2: Actualizar el índice y el spec del registry**

En `docs/specs/motor/00-indice.md` añadir, en la sección de documentos relacionados, una línea con enlace a `../2026-09-30-evaluacion-y-metricas-design.md` y al ADR 0020.

En `docs/specs/2026-09-29-registry-design.md`:

- §15 (M0): añadir el punto "5. `Agent.metrics` y los tipos del DSL (`SCHEMA_VERSION` 0.5.0, ADR 0020)".
- §15 (M1): añadir el punto "6. Reglas `MT-01` a `MT-06` en `validate_agent`".
- §16: añadir "`agent_core.registry.evaluation` con `EvalSuite`, `suite_problems`, `classify_yardstick_change` y `evaluate_gate` (T-EVAL-01 a 03, 05 a 08, 10 a 13 y 15)".

- [ ] **Step 3: Ejecutar todas las comprobaciones**

Run: `uv run pytest`
Expected: PASS (sin tests de integración si no hay Postgres).

Run: `uv run lint-imports`
Expected: todos los contratos en verde.

Run: `uv run mypy`
Expected: `Success: no issues found`.

Run: `uv run ruff check .`
Expected: `All checks passed!` Si reporta `E501` (líneas de más de 110 caracteres, frecuentes en los mensajes largos de `flows/metrics.py` y en las pruebas), partir la línea sin cambiar el comportamiento y volver a ejecutar `pytest tests/m00 tests/m01 tests/registry`.

Run: `uv run agentcore contracts --check`
Expected: código de salida 0.

- [ ] **Step 4: Marcar la "Definición de terminado" del spec (§15)**

Verificar punto por punto contra la salida de la tarea anterior y escribirlo en la descripción del PR:

- [ ] `Agent.metrics` y los tipos asociados en M0, `SCHEMA_VERSION` 0.5.0 y `contracts/` regenerado (`--check` en verde).
- [ ] Reglas del DSL en M1 (`MT-01` a `MT-06`) con T-EVAL-01 a T-EVAL-03 en verde.
- [ ] `EvalSuite`, `suite_problems`, `classify_yardstick_change` y `evaluate_gate` en `agent_core.registry.evaluation` con T-EVAL-05 a T-EVAL-08, T-EVAL-10 a T-EVAL-13 y T-EVAL-15 en verde.
- [ ] **Pendiente, por planes posteriores:** T-EVAL-04 y T-EVAL-14 (unidad 6 y analítica); T-EVAL-16 y T-EVAL-17 (servicio del registry); persistencia de `eval_suite` y de `releases.eval_suite_refs`.
- [ ] `lint-imports`, `mypy` y `ruff` en verde.
- [ ] Sin TODO sin issue.

- [ ] **Step 5: Commit**

```bash
git add docs
git commit -m "docs(eval): spec alineado con la implementacion, indice y registry enlazan el ADR 0020"
```

---

## Self-Review

**1. Spec coverage** (`docs/specs/2026-09-30-evaluacion-y-metricas-design.md`):

| Spec | Tarea |
|---|---|
| §3 `Agent.metrics`, `MetricDef`, `MetricExpr`, `JudgeExpr` | 1, 2 |
| §4 catálogo de eventos (`engine.*`, `registry.*`, sin PII) | 1 |
| §5 `EvalSuite`, `Scenario`, `ScriptedSource`, `DatasetSource`, `MetricThreshold` | 4 |
| §6.1 gate con doble vara, N métricas `gate`, `failed_infra` | 6 |
| §6.2 clasificación `yardstick_loosened` | 5 |
| §7 guardarraíles de plataforma e ids reservados | 3 (`MT-05`), 4, 6 |
| §8 flujo del constructor y roles | **Fuera de este plan**: servicio y roles del registry (T-EVAL-16, T-EVAL-17); la parte de `validated` está en las tareas 3 y 4 |
| §9 `dataset` desactivada | 4 (`dataset_source_disabled`) |
| §10 producción y alertas | Fuera: analítica (`AlertThreshold` solo se declara, tarea 1) |
| §12 cambios en M0, M1 y documentos | 2, 3, 7 |
| Persistencia `releases.eval_suite_refs`, `RegistryService`, API | Fuera de este plan, declarado en "Alcance" |

**2. Placeholders:** ninguno. Dos pasos piden confirmar un detalle del repo en lugar de asumirlo: la construcción de `RefSpec` en la tarea 3, y los exports de `JsonValue`, `Sha256Hex` y `UtcDatetime` en la tarea 4. Cada uno trae la alternativa concreta.

**3. Type consistency:** `MetricDef`, `MetricExpr`, `JudgeExpr`, `Predicate`, `AlertThreshold` (tarea 1) se usan con los mismos nombres y campos en las tareas 2 a 6. `MetricRole` es un alias de tipo y no entra en `__all__` de `agent_core.domain`, así que `gate.py` lo importa de `agent_core.domain.metrics`. `metric_identity` se define en la tarea 5 y se usa en la 6. `PLATFORM_GUARDRAILS` se define en la 4 y se usa en la 6. `suite_problems` usa `catalog_fields` de la tarea 1.

**4. Review Focus:** cada una de las cinco líneas tiene su prueba: 1 y 2 en las tareas 5 y 6, 3 y 4 en las tareas 3 y 4, 5 en la tarea 3.
