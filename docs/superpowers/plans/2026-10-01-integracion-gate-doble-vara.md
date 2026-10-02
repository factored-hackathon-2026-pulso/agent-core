# Integración del gate con doble vara en el registry — Plan de implementación

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** El gate de ADR 0020 (doble vara, N métricas `gate` por separado, guardarraíles de plataforma con tolerancia cero, marca `yardstick_loosened` aprobada aparte) vive dentro del registry de `main` (servicio, formato de suite, `ScenarioEvaluator`, calificación, gate, almacén en memoria y Postgres, HTTP y CLI), reemplaza el `decide` de métrica principal única y no deja conceptos duplicados: un solo `EvalSuite`, un solo `EvalReport`, un solo `Verdict`.

**Architecture:** Se extiende el formato de suite de `main` (`agent_core/registry/suite.py`) con umbrales por métrica, aserciones, repeticiones por escenario y escenarios `dataset` desactivados, y se le muda `suite_problems`. Un evaluador en memoria del DSL (`evaluation/metric_eval.py`) calcula `Agent.metrics` sobre los eventos que el `ScenarioHarness` ya devuelve. `scoring.py` califica cada corrida (guardarraíles de plataforma con los ids `platform_*`, aserciones) y mide una suite (`SuiteMeasurement`). `gate.py` reemplaza `decide` por `evaluate_gate` (la lógica de la rama). `ScenarioEvaluator` recibe un `EvalRequest` con la vara nueva (candidata) y la vieja (base) y corre hasta tres mediciones. El servicio arma ambas varas (la vieja desde `releases.eval_suite_refs`, tabla nueva `reg_release_eval_suites`), clasifica el aflojamiento y exige aprobarlo aparte. Los módulos interinos de la rama se borran cuando su lógica y sus pruebas ya viven en los de `main`.

**Tech Stack:** Python 3.12, Pydantic v2 (2.13), FastAPI, psycopg 3, Postgres 16, pytest, mypy strict, ruff, import-linter, `uv`.

**Spec:** `docs/specs/2026-09-30-evaluacion-y-metricas-design.md` (§5, §6, §7, §8, §13, §14) · `docs/specs/2026-09-29-registry-design.md` (§3.2, §5.2, §6, §7, §13–§16) · ADR `0020` (enmienda `0018`), `0017`, `0019`.

## Punto de partida (verificado en `feat/eval-metrics` @ 9583777)

- **Hecho y se queda:** M0 (`Agent.metrics`, DSL, catálogo, `predicate_problems`, `SCHEMA_VERSION` 1.2.0) y M1 (`MT-01`…`MT-06`, que `validate_candidate` ya corre vía `validate_registry`).
- **Registry de `main`:** `RegistryService.evaluate(actor, proposal_id, suite_id, suite_version)` elige la suite (borrador o publicada), arma `EvalTarget` de candidata y base (`SnapshotRegistry`) y llama `EvalPort.run(suite, candidate, base)`. `ScenarioEvaluator` corre la **misma** suite sobre ambas, califica con `score_run` (`expect` + 3 guardarraíles `unverified_writes`, `unsupported_success`, `sensitive_leaks`), agrega una métrica principal (tasa de pase) y decide con `gate.decide` (guardarraíl ≤ base, principal ≥ base − `noise_margin` o ≥ `floor`). `EvalReport` se guarda como JSON en `reg_eval_runs.report`. `releases` no guarda suites.
- **Interinos de la rama (se borran):** `agent_core/registry/evaluation/{suite,platform,double_gate}.py` y `tests/registry/yardstick/`. `evaluation/yardstick.py` se reescribe en su sitio (decisión D10).
- **El harness ya devuelve lo que el DSL necesita:** `EngineScenarioHarness.run` devuelve `storage.audit.read(run_id)`, la lista completa de `EngineEvent` del run (envelope con `release` y `run_id`, payload en vista `audit`). Todos los eventos `engine.*` del catálogo existen ahí. Los `registry.*` **no** ocurren dentro de un escenario (las tools del constructor van al sandbox) y las métricas `judge` no tienen juez en el gate (decisión D5).

## Decisiones que necesito del dueño

El plan avanza con la recomendación de cada una; si el dueño elige otra opción, se indica qué tarea cambia.

| # | Decisión | Opciones | Recomendación (lo que hace el plan) | Costo si es la equivocada |
|---|---|---|---|---|
| D1 | **Formato único de suite** | (a) el de `main` extendido: escenario `scripted` plano (`principal`, `steps`, `seed`, `sensitive_values`, `expect`) + `assertions` + `repetitions` por escenario + `DatasetScenario` elegido por `source` (por defecto `scripted`) + `thresholds` por métrica; (b) el del spec (`source: ScriptedSource{user_turns | signal, tool_fixtures, clock_start}`) | **(a)**: es lo que el harness sabe correr y las suites de `main` siguen siendo válidas sin tocarlas. Se reescribe el spec §5. Se pierden `signal` (agentes `task`) y `clock_start`, que el harness de `main` tampoco soporta: quedan como abierto nuevo | Si se quiere (b), reescribir harness, `LocalSandbox` y las suites de demo (≈2 tareas). Ninguna suite real lo usa hoy |
| D2 | **Campos de la métrica principal y datos ya guardados** | (a) borrar `EvalSuite.noise_margin`/`floor` y cambiar la forma de `EvalReport` sin migración: una BD de desarrollo con suites o reportes viejos se recrea; (b) validador "legacy" que descarte esos campos al leer y lectura tolerante de reportes viejos | **(a)**: R1 retira la métrica principal; no hay registry productivo antes de la entrega; `extra="forbid"` hace que una suite YAML vieja falle con un error claro | Con datos que conservar: `get_proposal`/`lineage` fallan al leer un `eval_run` viejo y un blob de suite viejo da `IntegrityError`. Arreglo: una tarea extra con validadores `mode="before"` |
| D3 | **Base sin suite registrada** (releases importadas sin `eval_suites/` o publicadas antes de este cambio: `eval_suite_refs` vacío) | (a) vara vieja = solo las métricas de la base, sin suite: el gate aplica los pisos de la vara nueva a todo; la clasificación sigue marcando métricas `gate`/`guardrail` borradas o cambiadas; (b) fail-closed: ninguna propuesta pasa hasta que un admin reimporte | **(a)**: con (b) la demo y toda release importada sin suite quedan bloqueadas para siempre; con (a) no hay vara vieja que aflojar y lo que sí existía (las métricas) sigue protegido | Si se elige (b): `evaluate_gate` y `_old_yardstick` fallan cuando `old.suite is None` (1 tarea pequeña) y T-REG-27 necesita sembrar una suite |
| D4 | **Semántica del evaluador en el gate** (spec §13.9 y §13.10, abiertos) | Población, repeticiones, `group_by`, vacíos y redondeo | Los eventos de **toda** la corrida de la suite (escenarios × repeticiones) se calculan juntos; la ventana no recorta en el gate. Un escenario pasa solo si pasa en **todas** sus repeticiones. Métrica con `group_by` → sin medir. `count`/`sum` sin filas = 0; `avg`/`percentile` sin valores y `rate` con denominador 0 → sin medir. `avg` y `rate` a 4 decimales `ROUND_HALF_EVEN`; percentil discreto (`percentile_disc`); campo ausente = NULL (no cumple filtros, ni `ne`) | "Todas las repeticiones" con un LLM ruidoso puede bloquear: la alternativa es mayoría. Gating por grupo exige otra forma de reporte. Cambiar luego cambia valores medidos (no tipos) |
| D5 | **Métricas no observables en sandbox** (`registry.*`, `judge`) | (a) quedan sin medir y una `gate`/`guardrail` así falla siempre el gate (fail-closed, spec §13.9); (b) `suite_problems` las rechaza antes con un código nuevo (`unmeasurable_metric`) | **(a)** sin código nuevo; se documenta como abierto | Bloquea los ejemplos `gate`/`guardrail` del constructor del spec §10 (`first_pass_validation_rate`, `proposal_quality`, `invariant_breaking_proposals`) hasta que haya juez y eventos del registry en el gate |
| D6 | **Guardarraíles de plataforma** | Unificar los 3 de `main` con los 4 ids de la rama | `sensitive_leaks → platform_pii_leak`, `unsupported_success → platform_unverified_success_claim`, `unverified_writes → platform_unverified_write`, y `platform_unapproved_knowledge_citation` = respuestas `generated` cuyo `validator` no es `ok` o falla `page_citations`/`page_audience` (M8 no las emite por construcción: es defensa en profundidad). La regla pasa de "no peor que la base" (`main`) a "0 en la candidata, medido en todas las corridas" (spec §6.1.3) | Una base que ya filtraba bloquea toda propuesta hasta corregirse: es la intención del spec. Si el dueño quiere otra definición de "cita no aprobada", solo cambia `_unapproved_citations` |
| D7 | **Cómo se aprueba el aflojamiento** (spec §8.5, R2) | (a) `approve(..., accept_yardstick_loosened=True)` + código nuevo `loosening_not_accepted` (409) + `Approval.yardstick_loosened` guardado (columna nueva); (b) operación y fila de aprobación aparte | **(a)**: aditivo en API, HTTP y CLI; la marca queda atada al mismo `candidate_hash` | (b) es otra tabla y otro endpoint (1 tarea). Con (a) un cliente viejo que no manda el campo recibe 409 solo cuando hay aflojamiento |
| D8 | **`GateItem.threshold` ambiguo** (spec §13.11) | Dos campos | `noise_margin` (vara vieja) y `floor` (vara nueva y plataforma) por separado, como sugiere el spec | Bajo: cambia la forma del reporte |
| D9 | **Qué suite mide la vara vieja y cuál la nueva** | (a) se conserva `evaluate(suite_id, suite_version)` de `main` para la nueva; la vieja sale de `releases.eval_suite_refs` (tabla `reg_release_eval_suites`, una suite por agente y release); elegir otra suite cuenta como aflojamiento (`scenario_removed`); (b) una suite fija por agente | **(a)** | (b) cambia la API pública de `evaluate` y la CLI |
| D10 | **`evaluation/yardstick.py`** | R5 lo lista para borrar; no tiene par en `main` | Reescribirlo en su sitio sobre el `EvalSuite` de `main` (sin duplicados); se borran `suite.py`, `platform.py`, `double_gate.py` y `tests/registry/yardstick/` | Si se exige borrarlo: mover `Yardstick` y la clasificación a `gate.py` (renombrado mecánico) |
| D11 | **ADR 0018, enmienda de entrega, punto 2** ("métrica principal = tasa de pase") contradice al ADR 0020 | Cuál manda | ADR 0020 (lo pide R1); se añade una nota en el ADR 0018 | Si manda el 0018, R1 no se cumple |
| D12 | **`SCHEMA_VERSION`** | Subir o no | **Se queda en 1.2.0**: este plan no cambia ningún tipo de M0 (todo vive en `agent_core.registry`, que no entra en `contracts/`); `uv run agentcore contracts --check` debe seguir en verde sin regenerar | Si al implementar aparece un cambio de M0, se queda en 1.2.0 (versión aún no publicada de este PR) y se regenera `contracts/` |

## Global Constraints

- Python 3.12, Pydantic v2, `uv`. `agent_core.registry` solo importa `domain`, `ports` y la interfaz pública de `flows` (`.importlinter`); `composition` importa `registry`, nunca al revés.
- Código, identificadores y nombres de campos en **inglés**; docstrings, mensajes y specs en **español**, como el código vecino.
- Nunca `float`: `Decimal` finito (`allow_inf_nan=False`). Nunca `datetime.now()`, `time.time()`, `uuid4()`, `random` ni `secrets` (`ruff` TID251).
- Solo datos sintéticos en pruebas y fixtures (regla 5); ningún campo de PII en mensajes (los errores de esquema van sin `input`).
- Líneas ≤ 110 caracteres; `mypy` strict sobre `agent_core` y `testing`; `ruff` con `E,F,W,I,B,UP,TID,DTZ,RUF` (nada de `case "x": return y` en una línea: E701).
- Fail-closed en todo el gate: lo que no se midió falla; `failed_infra` no es veredicto.
- Commits: `feat(registry): <resumen en español>` con trailer `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`.
- El árbol queda verde después de **cada** tarea (`uv run pytest tests/registry tests/composition -q`, `uv run mypy`, `uv run ruff check .`, `uv run lint-imports`).

## Review Focus

1. **Fail-closed conservado de la rama:** métrica, escenario o guardarraíl ausente en cualquiera de las corridas viejas falla; solo un `False` explícito en la base exime un escenario; promoción de rol + cambio de `expr`/dirección es `metric_changed`. Pruebas portadas en la tarea 3.
2. **Corrida compartida:** si la suite vieja es igual a la nueva, `cand_on_old` y `cand_on_new` salen de las mismas corridas pero con las definiciones de métricas de cada vara (tarea 5).
3. **Repeticiones efectivas:** `repetitions` del escenario o, si es `None`, la de la suite. Bajar la de la suite afloja todos los escenarios que la heredan; explicitar el mismo número no es un cambio (tarea 3).
4. **NULL y tipos en el evaluador:** `True` no es igual a `1`; `ne` no cumple sobre un campo ausente; `in` exige lista (tarea 2).
5. **Transacciones:** `approve` que rechaza por aflojamiento no escribe nada; `publish` inserta la versión de la suite antes de la fila que la referencia (FK en Postgres) (tareas 6 y 8).
6. **IDs de escenario duplicados:** `main` los rechaza al parsear; `duplicate_scenario` se queda en `suite_problems` como defensa para instancias construidas sin validar (tarea 1).

## Pruebas existentes de `main` que cambian, y por qué

| Archivo | Cambio | Por qué |
|---|---|---|
| `tests/registry/test_gate.py` | Los 4 tests de `decide` (T-REG-08/09/10 y campos del check) se reemplazan por los de `evaluate_gate` (T-EVAL-05/06/13 cubren lo mismo con N métricas) | R1: la métrica principal desaparece |
| `tests/registry/test_scoring.py` | Claves de guardarraíles renombradas a `platform_*` (+1 nuevo); el test de `aggregate` se reemplaza por los de `measure` | D6 y fin de la métrica principal |
| `tests/registry/test_evaluator.py` | Mismas intenciones (T-REG-11, 23, 24, sin base), pero con `EvalRequest`, `report.items` y `report.runs`; +3 pruebas de varas y métricas | Nueva firma de `EvalPort` |
| `tests/registry/service_world.py` | `FakeEvaluator.run(request)` guarda los pedidos; sin `ZERO`; helpers `publish_cycle`, `loosening_evaluated`, `agent_with_metrics` | Nueva firma de `EvalPort` |
| `tests/registry/test_service_decide.py` | `_report` sin `SuiteMetrics` | `SuiteMetrics` desaparece |
| `tests/registry/helpers.py` · `testing/registry_demo.py` | La suite de prueba y la de demo pierden `noise_margin`/`floor` | D2 |
| `tests/registry/test_models.py` | Umbrales por métrica en lugar de los de la suite | D2 |
| `tests/registry/test_validation.py` | +1 prueba (`missing_threshold` como `REG-SUITE`); la de agente ajeno sigue igual | `suite_problems` en la validación |
| `tests/registry/test_http.py` · `tests/composition/test_registry_cli.py` | +1 prueba cada uno (aprobación del aflojamiento) | D7 |
| `tests/integration/test_registry_postgres.py` | Sin cambios en las existentes (T-REG-27 corre el evaluador real con la vara nueva); +3 nuevas | Tabla y columna nuevas |

---

## File Structure

**Crear**

| Archivo | Responsabilidad |
|---|---|
| `agent_core/registry/evaluation/metric_eval.py` | Evaluador en memoria del DSL y de las aserciones sobre `EngineEvent` (tarea 2) |
| `tests/registry/eval_support.py` | Constructores sintéticos de métricas, escenarios, suites y varas (reemplaza `tests/registry/yardstick/support.py`) |
| `tests/registry/test_suite.py` | Formato unificado y `suite_problems` (portado de `tests/registry/yardstick/test_suite.py`) |
| `tests/registry/test_metric_eval.py` | Evaluador del DSL |
| `tests/registry/test_yardstick.py` | Clasificación del aflojamiento (portado de `tests/registry/yardstick/test_yardstick.py`) |
| `tests/registry/test_service_yardstick.py` | Doble vara en el servicio: refs, problemas de suite, aflojamiento, aprobación, T-EVAL-09/10/16/17 |

**Modificar**

| Archivo | Cambio |
|---|---|
| `agent_core/registry/suite.py` | Formato unificado + `suite_problems` (tarea 1); sin `noise_margin`/`floor` (tarea 5) |
| `agent_core/registry/evaluation/yardstick.py` | Reescrito sobre el `EvalSuite` de `main`; `Yardstick.suite` opcional; repeticiones efectivas (tarea 3) |
| `agent_core/registry/evaluation/report.py` | `GateItem`, `SuiteMeasurement`, `GateRuns` (tarea 3); `EvalReport` unificado, sin `MetricCheck`/`SuiteMetrics` (tarea 5) |
| `agent_core/registry/evaluation/gate.py` | `evaluate_gate` (tarea 3); sin `decide` (tarea 5) |
| `agent_core/registry/evaluation/scoring.py` | `PLATFORM_GUARDRAILS` (tarea 3); guardarraíles renombrados, aserciones, `measure` (tarea 4); sin `aggregate` (tarea 5) |
| `agent_core/registry/evaluation/evaluator.py` | Repeticiones por escenario (tarea 1); `run(EvalRequest)` con hasta tres mediciones (tarea 5) |
| `agent_core/registry/evaluation/ports.py` | `EvalRequest`, `EvalPort.run(request)` (tarea 5) |
| `agent_core/registry/evaluation/__init__.py`, `agent_core/registry/__init__.py` | Exportaciones (tareas 1 y 5) |
| `agent_core/registry/models.py` | `StoredRelease.eval_suite_refs`, `ReleaseDetail.eval_suite_refs` (tarea 6); `Approval.yardstick_loosened` (tarea 8) |
| `agent_core/registry/service.py` | Varas en `evaluate` (5, 6, 7), refs al publicar e importar (6), `approve` y `ApprovalReview` (8), `put_draft` sin `platform_*` (9) |
| `agent_core/registry/validation.py` | `suite_violations` y `suite_problems` en `validate_candidate` (7); `platform_edits` (9) |
| `agent_core/registry/errors.py` | `loosening_not_accepted` → 409 (8) |
| `agent_core/registry/http.py` | `accept_yardstick_loosened` en `approve` (8) |
| `agent_core/registry/postgres/schema.sql`, `agent_core/registry/postgres/store.py` | Tabla `reg_release_eval_suites` (6); columna `reg_approvals.yardstick_loosened` (8) |
| `agent_core/composition/registry.py` | `approve --accept-yardstick-loosened` (8) |
| `testing/registry_demo.py` | Suite de demo sin `noise_margin`/`floor` (5) |
| Pruebas listadas arriba | Ver la tabla anterior |
| `docs/specs/2026-09-30-evaluacion-y-metricas-design.md`, `docs/specs/2026-09-29-registry-design.md`, `docs/adr/0018-propuestas-y-gate-de-publicacion.md`, `docs/specs/TEMAS-ABIERTOS-PENDIENTES.md` | Tarea 11 |

**Sin cambios:** `agent_core/registry/memory.py` (guarda `StoredRelease` y `Approval` enteros), `agent_core/registry/store.py` (el protocolo no cambia), `agent_core/registry/candidate.py`, `agent_core/composition/evaluation.py` (el harness recibe el mismo `Scenario`), M0, M1, `contracts/`.

**Borrar** (tarea 3, cuando su lógica y sus pruebas ya están portadas)

- `agent_core/registry/evaluation/suite.py`, `agent_core/registry/evaluation/platform.py`, `agent_core/registry/evaluation/double_gate.py`
- `tests/registry/yardstick/__init__.py`, `support.py`, `test_double_gate.py`, `test_yardstick.py` (y `test_suite.py` ya en la tarea 1)

---

### Task 1: Formato unificado de `eval_suite` y `suite_problems` sobre el de `main`

**Files:**
- Modify: `agent_core/registry/suite.py` (contenido completo abajo)
- Modify: `agent_core/registry/evaluation/evaluator.py` (una línea)
- Modify: `agent_core/registry/__init__.py` (exportaciones)
- Create: `tests/registry/eval_support.py`, `tests/registry/test_suite.py`
- Delete: `tests/registry/yardstick/test_suite.py` (portado aquí; el módulo interino `evaluation/suite.py` sigue vivo hasta la tarea 3 porque `yardstick.py` y `double_gate.py` lo importan)

**Interfaces producidas:** `Assertion`, `Scenario` (con `source`, `assertions`, `repetitions`), `DatasetScenario`, `AnyScenario`, `MetricThreshold`, `EvalSuite.thresholds`, `EvalSuite.repetitions_of(scenario) -> int`, `EvalSuite.scripted() -> list[Scenario]`, `SuiteProblemCode`, `SuiteProblem`, `suite_problems(agent, suite) -> list[SuiteProblem]`.

- [ ] **Step 1: Escribir los constructores de prueba**

Crear `tests/registry/eval_support.py`:

```python
"""Constructores sintéticos para las pruebas de la suite, la vara y el gate (ADR 0020)."""

from typing import Any

from agent_core.domain import MetricDef
from agent_core.registry.suite import EvalSuite


def metric(mid: str, role: str = "gate", higher: bool = True, event: str = "engine.turn_completed",
           **expr: Any) -> MetricDef:
    body: dict[str, Any] = {"event": event, "aggregation": "count", "window": "scenario"} | expr
    return MetricDef.model_validate(
        {"id": mid, "description": "d", "role": role, "higher_is_better": higher, "expr": body})


def scenario(sid: str, repetitions: int | None = 1, **over: Any) -> dict[str, Any]:
    """Escenario `scripted` en el formato de `main`; `repetitions=None` hereda las de la suite."""
    body: dict[str, Any] = {"id": sid, "principal": {"id": "cust-001"},
                            "steps": [{"op": "start"}, {"op": "turn", "text": "hola"}]}
    if repetitions is not None:
        body["repetitions"] = repetitions
    return body | over


def thr(noise: str = "0", floor: str | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {"noise_margin": noise}
    if floor is not None:
        out["floor"] = floor
    return out


def suite(scenarios: list[dict[str, Any]], thresholds: dict[str, dict[str, Any]] | None = None,
          agent_id: str = "atencion", **over: Any) -> EvalSuite:
    data: dict[str, Any] = {"id": "suite", "version": "1.0.0", "agent_id": agent_id, "repetitions": 1,
                            "scenarios": scenarios, "thresholds": thresholds or {}}
    return EvalSuite.model_validate(data | over)
```

- [ ] **Step 2: Escribir las pruebas (portadas de `tests/registry/yardstick/test_suite.py` al formato de `main`)**

Crear `tests/registry/test_suite.py`:

```python
"""`eval_suite` unificada (registry §6.1, spec de evaluación §5): formato y `suite_problems`."""

from copy import deepcopy
from typing import Any

import pytest
from pydantic import ValidationError

from agent_core.domain import Agent
from agent_core.registry.suite import DatasetScenario, EvalSuite, Scenario, SuiteProblemCode, suite_problems
from tests.m01.cases import AGENT
from tests.registry.eval_support import metric, scenario, suite, thr
from tests.registry.helpers import suite_content

DATASET: dict[str, Any] = {"id": "s1", "source": "dataset", "dataset_id": "casos_reales",
                           "dataset_hash": "a" * 64}


def make_agent(*metrics: Any) -> Agent:
    data = deepcopy(AGENT) | {"metrics": [m.model_dump(mode="json") for m in metrics]}
    return Agent.model_validate(data)


def codes(problems: list[Any]) -> list[str]:
    return [p.code.value for p in problems]


def test_main_format_suite_is_still_valid() -> None:  # compatibilidad con las suites de `main` (D1)
    parsed = EvalSuite.model_validate(suite_content())
    [only] = parsed.scenarios
    assert isinstance(only, Scenario) and only.source == "scripted"
    assert parsed.thresholds == {} and only.assertions == [] and only.repetitions is None
    assert parsed.repetitions_of(only) == parsed.repetitions


def test_valid_suite_has_no_problems() -> None:
    agent = make_agent(metric("m_gate"), metric("m_mon", role="monitor"))
    assert suite_problems(agent, suite([scenario("s1")], {"m_gate": thr("0.02")})) == []


def test_scenario_repetitions_override_the_suite_default() -> None:
    parsed = suite([scenario("s1", repetitions=None), scenario("s2", repetitions=5)], repetitions=2)
    first, second = parsed.scenarios
    assert (parsed.repetitions_of(first), parsed.repetitions_of(second)) == (2, 5)


@pytest.mark.parametrize("bad", [0, 11])
def test_repetitions_are_bounded(bad: int) -> None:
    with pytest.raises(ValidationError):
        suite([scenario("s1", repetitions=bad)])


def test_dataset_scenario_is_parsed_but_cannot_be_run() -> None:
    parsed = suite([DATASET])
    assert isinstance(parsed.scenarios[0], DatasetScenario)
    with pytest.raises(ValueError):
        parsed.scripted()


@pytest.mark.parametrize("bad", [
    DATASET | {"steps": [{"op": "start"}]},  # un dataset no lleva guion
    DATASET | {"principal": {"id": "cust-001"}},
    {"id": "s1", "source": "otra"},  # fuente desconocida
    {"id": "s1", "source": "dataset"},  # falta el dataset
])
def test_bad_scenario_sources_are_rejected(bad: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        suite([bad])


def test_assertion_filters_must_match_the_catalog() -> None:
    agent = make_agent(metric("m_gate"))
    typo = scenario("s1", assertions=[
        {"event": "engine.escalated", "expect": "none",
         "where": [{"field": "reason_cod", "op": "eq", "value": "x"}]}])
    bad_type = scenario("s2", assertions=[
        {"event": "engine.agent_step", "where": [{"field": "step", "op": "eq", "value": "uno"}]}])
    bad_op = scenario("s3", assertions=[
        {"event": "engine.escalated", "where": [{"field": "priority", "op": "gt", "value": 1}]}])
    good = scenario("s4", assertions=[
        {"event": "engine.agent_step", "where": [{"field": "step", "op": "ge", "value": 2}]}])
    problems = suite_problems(agent, suite([typo, bad_type, bad_op, good], {"m_gate": thr()}))
    assert [(p.code.value, p.path) for p in problems] == [
        ("invalid_assertion_filter", "/scenarios/0/assertions/0/where/0/field"),
        ("invalid_assertion_filter", "/scenarios/1/assertions/0/where/0/value"),
        ("invalid_assertion_filter", "/scenarios/2/assertions/0/where/0/field"),
    ]


def test_suite_needs_scenarios() -> None:
    with pytest.raises(ValidationError):
        suite([])


def test_duplicate_scenario_ids_are_rejected_when_parsing() -> None:  # `main` ya lo hacía (Review Focus 6)
    with pytest.raises(ValidationError):
        suite([scenario("s1"), scenario("s1")])


def test_thresholds_are_finite_non_negative_decimals() -> None:
    for bad in ({"m": thr("NaN")}, {"m": thr("-0.1")}, {"m": {"noise_margin": "0", "floor": "NaN"}}):
        with pytest.raises(ValidationError):
            suite([scenario("s1")], bad)


def test_agent_without_suite_is_not_publishable() -> None:  # T-EVAL-11
    assert codes(suite_problems(make_agent(metric("m_gate")), None)) == ["missing_suite"]


def test_agent_with_only_monitor_metrics_or_none_still_needs_a_suite() -> None:
    assert codes(suite_problems(make_agent(metric("m_mon", role="monitor")), None)) == ["missing_suite"]
    assert codes(suite_problems(make_agent(), None)) == ["missing_suite"]


def test_gate_and_guardrail_metrics_need_thresholds() -> None:  # T-EVAL-11
    agent = make_agent(metric("m_gate"), metric("m_guard", role="guardrail"), metric("m_mon", role="monitor"))
    problems = suite_problems(agent, suite([scenario("s1")], {"m_gate": thr()}))
    assert [(p.code.value, p.path) for p in problems] == [("missing_threshold", "/thresholds/m_guard")]


def test_thresholds_for_unknown_metrics_are_rejected() -> None:
    agent = make_agent(metric("m_gate"))
    problems = suite_problems(agent, suite([scenario("s1")], {"m_gate": thr(), "ghost": thr()}))
    assert [(p.code.value, p.path) for p in problems] == [("unknown_threshold_metric", "/thresholds/ghost")]


def test_assertion_events_must_be_in_the_catalog() -> None:
    agent = make_agent(metric("m_gate"))
    bad = scenario("s1", assertions=[{"event": "engine.nope"}])
    good = scenario("s2", assertions=[{"event": "engine.escalated",
                                       "where": [{"field": "reason_code", "op": "eq", "value": "x"}]}])
    problems = suite_problems(agent, suite([bad, good], {"m_gate": thr()}))
    assert [(p.code.value, p.path) for p in problems] == [
        ("unknown_assertion_event", "/scenarios/0/assertions/0/event")]


def test_dataset_source_is_disabled() -> None:  # T-EVAL-12
    agent = make_agent(metric("m_gate"))
    problems = suite_problems(agent, suite([DATASET], {"m_gate": thr()}))
    assert [(p.code, p.path) for p in problems] == [
        (SuiteProblemCode.dataset_source_disabled, "/scenarios/0/source")]


def test_suite_must_belong_to_the_agent() -> None:
    agent = make_agent(metric("m_gate"))
    other = suite([scenario("s1")], {"m_gate": thr()}, agent_id="otro")
    assert codes(suite_problems(agent, other)) == ["agent_mismatch"]


def test_problems_are_sorted_and_deterministic() -> None:
    agent = make_agent(metric("m_gate"), metric("m_guard", role="guardrail"))
    s = suite([scenario("s1"), DATASET | {"id": "s2"}], {"ghost": thr()})
    first, second = suite_problems(agent, s), suite_problems(agent, s)
    assert first == second == sorted(first, key=lambda p: (p.path, p.code.value))
    assert len(first) == 4  # dos umbrales faltantes, uno desconocido y el dataset
```

- [ ] **Step 3: Verificar que fallan**

Run: `uv run pytest tests/registry/test_suite.py -q`
Expected: error de colección `ImportError: cannot import name 'DatasetScenario' from 'agent_core.registry.suite'`.

- [ ] **Step 4: Reescribir `agent_core/registry/suite.py`**

Contenido completo (los campos marcados `T5` se borran en la tarea 5):

```python
"""Formato de `eval_suite` (registry §6.1, ADR 0020 §5): escenarios ligados a un agente.

Vive en el registry y no en M0: no entra en la release del motor. Un escenario es `scripted` (guion sintético
sobre el motor real con las tools en sandbox; habilitado) o `dataset` (casos reales por id y hash; diseñado y
DESACTIVADO hasta un ADR aparte, tema #19). Los umbrales del gate son por métrica (`thresholds`).
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

Fraction = Annotated[Decimal, Field(ge=0, le=1, allow_inf_nan=False)]  # T5: se borra con la métrica principal
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
    """Afirmación sobre los eventos de una corrida: aparece (o no) un evento que cumple el filtro."""

    event: str = Field(min_length=1, max_length=80)
    where: list[Predicate] = Field(default_factory=list, max_length=8)
    expect: Literal["at_least_one", "none"] = "at_least_one"


class Scenario(_M):
    """Escenario `scripted`: datos sintéticos, tools sembradas en el sandbox y el motor real."""

    id: ScenarioId
    source: Literal["scripted"] = "scripted"
    principal: ScenarioPrincipal
    steps: list[Step] = Field(min_length=1, max_length=50)
    seed: SandboxSeed = Field(default_factory=SandboxSeed)
    sensitive_values: list[str] = Field(default_factory=list)
    expect: Expect = Field(default_factory=Expect)
    assertions: list[Assertion] = Field(default_factory=list, max_length=20)
    repetitions: Repetitions | None = None  # None: las de la suite

    @model_validator(mode="after")
    def _starts(self) -> "Scenario":
        if self.steps[0].op != "start" or any(s.op == "start" for s in self.steps[1:]):
            raise ValueError("un escenario empieza con un único paso `start`")
        return self


class DatasetScenario(_M):
    """Escenario `dataset`: casos reales por id y hash. DESACTIVADO (`dataset_source_disabled`, tema #19).

    Los datos reales nunca viven en el repo ni en el registry: solo su id y su hash."""

    id: ScenarioId
    source: Literal["dataset"]
    dataset_id: str = Field(min_length=1, max_length=120)
    dataset_hash: Sha256Hex
    reference_outcome: bool = False
    expect: Expect = Field(default_factory=Expect)
    assertions: list[Assertion] = Field(default_factory=list, max_length=20)
    repetitions: Repetitions | None = None


def _scenario_kind(value: Any) -> str:
    """`source` decide el tipo; sin `source` es `scripted` (formato anterior al ADR 0020)."""
    if isinstance(value, dict):
        return str(value.get("source", "scripted"))
    return str(getattr(value, "source", "scripted"))


AnyScenario = Annotated[
    Annotated[Scenario, Tag("scripted")] | Annotated[DatasetScenario, Tag("dataset")],
    Discriminator(_scenario_kind),
]


class MetricThreshold(_M):
    """Umbral del gate para una métrica `gate` o `guardrail` del agente (spec de evaluación §5).

    `noise_margin` es la tolerancia frente a la base, en la dirección de la métrica. `floor` se exige a toda
    métrica nueva o cambiada, con o sin base: un mínimo si mayor es mejor y un máximo si menor es mejor."""

    noise_margin: NonNegativeDecimal
    floor: FiniteDecimal | None = None


class EvalSuite(_M):
    id: EntityId
    version: ExactVersion
    agent_id: EntityId
    repetitions: PositiveInt = Field(default=3, le=10)  # las de cada escenario que no declare las suyas
    noise_margin: Fraction = Decimal("0.05")  # T5: se borra (métrica principal, ADR 0020 §6)
    floor: Fraction = Decimal("0.7")  # T5: se borra
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
        """Los escenarios que corre el evaluador. Un `dataset` aquí es un error de quien llama:
        `suite_problems` lo rechaza antes de evaluar."""
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
    """Lo que impide publicar al agente por el gate (spec de evaluación §5). Lista vacía = suite utilizable.

    Todo agente necesita una suite, aunque solo tenga métricas `monitor`: sin escenarios no se pueden medir
    los guardarraíles de plataforma. `duplicate_scenario` solo aparece con instancias construidas sin
    validar: `EvalSuite` ya rechaza ids repetidos al parsear."""
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
```

- [ ] **Step 5: El evaluador corre solo escenarios `scripted` y respeta sus repeticiones**

En `agent_core/registry/evaluation/evaluator.py`, reemplazar la línea de `jobs` de `ScenarioEvaluator.run`:

```python
        jobs = [_Job(t, s, r) for t in targets for s in suite.scripted()
                for r in range(suite.repetitions_of(s))]
```

- [ ] **Step 6: Exportar desde `agent_core/registry/__init__.py`**

Reemplazar la línea `from agent_core.registry.suite import EvalSuite, SandboxSeed, Scenario, Step` por:

```python
from agent_core.registry.suite import (
    Assertion,
    DatasetScenario,
    EvalSuite,
    MetricThreshold,
    SandboxSeed,
    Scenario,
    Step,
    SuiteProblem,
    SuiteProblemCode,
    suite_problems,
)
```

y añadir a `__all__`: `"Assertion"`, `"DatasetScenario"`, `"MetricThreshold"`, `"SuiteProblem"`, `"SuiteProblemCode"`, `"suite_problems"`.

- [ ] **Step 7: Borrar la prueba interina ya portada y verificar**

```bash
git rm tests/registry/yardstick/test_suite.py
uv run pytest tests/registry tests/composition -q
uv run mypy
uv run ruff check .
uv run lint-imports
```
Expected: todas las pruebas pasan (0 failed); `mypy`, `ruff` y `lint-imports` sin errores. Las pruebas interinas `tests/registry/yardstick/test_double_gate.py` y `test_yardstick.py` siguen pasando (usan el `EvalSuite` interino).

- [ ] **Step 8: Commit**

```bash
git add agent_core/registry/suite.py agent_core/registry/evaluation/evaluator.py agent_core/registry/__init__.py tests/registry/eval_support.py tests/registry/test_suite.py
git commit -m "feat(registry): suite unica con umbrales por metrica, aserciones y escenarios dataset desactivados (ADR 0020)" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Evaluador en memoria del DSL de métricas y de las aserciones

**Files:**
- Create: `agent_core/registry/evaluation/metric_eval.py`
- Create: `tests/registry/test_metric_eval.py`

**Interfaces producidas:** `catalog_record(event) -> tuple[str, dict[str, Value]] | None`, `evaluate_metric(expr, events) -> Decimal | None`, `assertion_holds(assertion, events) -> bool`, `OBSERVABLE_PREFIX`, `QUANTUM`.

- [ ] **Step 1: Escribir las pruebas**

Crear `tests/registry/test_metric_eval.py`:

```python
"""Evaluador en memoria del DSL (ADR 0020; semántica de la decisión D4)."""

from decimal import Decimal
from typing import Any

from agent_core.domain import (
    ActionDispatched,
    EngineEvent,
    Escalated,
    JudgeExpr,
    MetricExpr,
    ResponseEmitted,
    ToolCalled,
    TurnCompleted,
)
from agent_core.registry.evaluation.metric_eval import assertion_holds, catalog_record, evaluate_metric
from agent_core.registry.suite import Assertion
from testing.builders import NOW

ENV: dict[str, Any] = {"run_id": "run-1", "release": "rel-1", "ts": NOW}


def turn(ms: int, degraded: bool = False) -> EngineEvent:
    return TurnCompleted.model_validate({**ENV, "event_id": f"t-{ms}", "payload": {
        "entry": "turn", "duration_ms": ms, "stages": {}, "degraded": degraded, "awaiting": "none"}})


def escalated(reason: str = "tool_failure") -> EngineEvent:
    return Escalated.model_validate({**ENV, "event_id": "x", "payload": {
        "reason_code": reason, "target_queue": "q", "priority": "high", "handoff_ref": "h"}})


def tool(status: str = "ok") -> EngineEvent:
    return ToolCalled.model_validate({**ENV, "event_id": "c", "payload": {
        "node_id": "n1", "tool": "radicar_pqr@1.0.0", "call_id": "c1", "status": status, "args": {},
        "latency_ms": 10}})


def emitted(llm: bool = True) -> EngineEvent:
    payload: dict[str, Any] = {"kind": "generated", "validator": {"ok": True}, "fallback_used": False}
    if llm:
        payload["llm"] = {"calls": 1, "latency_ms": 5, "tokens_in": 3, "tokens_out": 4, "cost_usd": "0.0025",
                          "cost_known": True}
    return ResponseEmitted.model_validate({**ENV, "event_id": "r", "payload": payload})


def expr(**over: Any) -> MetricExpr:
    base: dict[str, Any] = {"event": "engine.turn_completed", "aggregation": "count", "window": "scenario"}
    return MetricExpr.model_validate(base | over)


TURNS = [turn(100), turn(200, degraded=True), turn(300), turn(400)]


def test_record_projects_envelope_and_nested_fields() -> None:
    found = catalog_record(emitted())
    assert found == ("engine.response_emitted", {
        "release": "rel-1", "run_id": "run-1", "validator.ok": True, "validator.regenerations": 0,
        "llm.calls": 1, "llm.latency_ms": 5, "llm.tokens_in": 3, "llm.tokens_out": 4,
        "llm.cost_usd": Decimal("0.0025"), "kind": "generated", "fallback_used": False})
    without_llm = catalog_record(emitted(llm=False))
    assert without_llm is not None and "llm.calls" not in without_llm[1]


def test_enums_become_their_text_and_unknown_events_are_not_measurable() -> None:
    found = catalog_record(tool("timeout"))
    assert found is not None and type(found[1]["status"]) is str and found[1]["status"] == "timeout"
    dispatched = ActionDispatched.model_validate(
        {**ENV, "event_id": "d", "payload": {"action_id": "a1", "tool": "radicar_pqr@1.0.0"}})
    assert catalog_record(dispatched) is None


def test_count_sum_avg_and_discrete_percentile() -> None:
    assert evaluate_metric(expr(), TURNS) == Decimal(4)
    assert evaluate_metric(expr(aggregation="sum", field="duration_ms"), TURNS) == Decimal(1000)
    assert str(evaluate_metric(expr(aggregation="avg", field="duration_ms"), TURNS)) == "250.0000"
    for pct, value in ((1, 100), (50, 200), (95, 400)):
        p = expr(aggregation="percentile", field="duration_ms", percentile=pct)
        assert evaluate_metric(p, TURNS) == Decimal(value)


def test_empty_inputs() -> None:
    assert evaluate_metric(expr(), []) == Decimal(0)
    assert evaluate_metric(expr(aggregation="sum", field="duration_ms"), []) == Decimal(0)
    assert evaluate_metric(expr(aggregation="avg", field="duration_ms"), []) is None
    assert evaluate_metric(expr(aggregation="percentile", field="duration_ms", percentile=50), []) is None


def test_rate_is_a_rounded_ratio_and_zero_denominator_is_unmeasured() -> None:
    denominator = {"event": "engine.turn_completed", "aggregation": "count", "window": "scenario"}
    rate = expr(aggregation="rate", where=[{"field": "degraded", "op": "eq", "value": True}],
                denominator=denominator)
    assert str(evaluate_metric(rate, [turn(1, True), turn(2), turn(3)])) == "0.3333"
    assert evaluate_metric(rate, []) is None


def test_missing_fields_are_null() -> None:
    events = [emitted(llm=False), emitted(llm=True)]
    calls = expr(event="engine.response_emitted", aggregation="sum", field="llm.calls")
    assert evaluate_metric(calls, events) == Decimal(1)
    not_five = expr(event="engine.response_emitted", where=[{"field": "llm.calls", "op": "ne", "value": 5}])
    assert evaluate_metric(not_five, events) == Decimal(1)  # el que no trae `llm` no cuenta


def test_filters_are_type_aware() -> None:
    assert evaluate_metric(expr(where=[{"field": "duration_ms", "op": "ge", "value": 200}]), TURNS) == 3
    assert evaluate_metric(expr(where=[{"field": "degraded", "op": "eq", "value": True}]), TURNS) == 1
    assert evaluate_metric(expr(where=[{"field": "degraded", "op": "eq", "value": 1}]), TURNS) == 0
    among = expr(event="engine.escalated",
                 where=[{"field": "reason_code", "op": "in", "value": ["tool_failure", "low_confidence"]}])
    assert evaluate_metric(among, [escalated(), escalated("customer_request")]) == 1


def test_unobservable_metrics_are_unmeasured() -> None:  # decisión D5
    judge = JudgeExpr.model_validate({"judge_profile": "perfil-juez@1.0.0", "rubric": "r",
                                      "target_event": "engine.response_emitted"})
    assert evaluate_metric(judge, TURNS) is None
    assert evaluate_metric(expr(event="registry.proposal_created"), TURNS) is None  # nunca 0
    assert evaluate_metric(expr(group_by=["release"]), TURNS) is None  # spec §13.10


def test_assertions() -> None:
    must = Assertion(event="engine.escalated")
    never = Assertion(event="engine.escalated", expect="none")
    assert assertion_holds(must, [escalated()]) and not assertion_holds(must, [])
    assert assertion_holds(never, []) and not assertion_holds(never, [escalated()])
    filtered = Assertion.model_validate({
        "event": "engine.escalated",
        "where": [{"field": "reason_code", "op": "eq", "value": "low_confidence"}]})
    assert not assertion_holds(filtered, [escalated()])
    blind = Assertion(event="registry.proposal_created", expect="none")
    assert not assertion_holds(blind, [])  # no observable: falla cerrada, aunque espere "none"
```

- [ ] **Step 2: Verificar que fallan**

Run: `uv run pytest tests/registry/test_metric_eval.py -q`
Expected: `ModuleNotFoundError: No module named 'agent_core.registry.evaluation.metric_eval'`.

- [ ] **Step 3: Implementar `agent_core/registry/evaluation/metric_eval.py`**

```python
"""Evaluador en memoria del DSL de métricas y de las aserciones de escenario (ADR 0020; `EvalPort`).

Puro y determinista, sobre los eventos del motor de una o varias corridas. Semántica (decisión D4 del plan de
integración; el compilador a SQL de la analítica debe reproducirla, T-EVAL-14):

- Solo se observan eventos `engine.*`: los `registry.*` no ocurren dentro de un escenario, así que una
  métrica o una aserción sobre ellos queda sin medir (nunca en cero).
- Un campo ausente es NULL: no cumple ningún filtro (tampoco `ne`) y no entra en `sum`, `avg` ni `percentile`.
- `count` y `sum` sin filas valen 0; `avg` y `percentile` sin valores, y `rate` con denominador 0, quedan sin
  medir. `avg` y `rate` se redondean a 4 decimales (`ROUND_HALF_EVEN`); el percentil es discreto
  (`percentile_disc`: el menor valor cuya frecuencia acumulada alcanza p).
- La ventana no recorta nada aquí: el gate mide la corrida completa de la suite. Una métrica con
  `group_by` queda sin medir (no tiene forma escalar, spec §13.10) y una `judge` también (no hay juez en el
  gate, spec §13.4).
"""

from collections.abc import Iterable, Sequence
from decimal import ROUND_HALF_EVEN, Decimal
from enum import Enum

from agent_core.domain import EngineEvent, JudgeExpr, MetricExpr, Predicate, catalog_fields
from agent_core.registry.suite import Assertion

OBSERVABLE_PREFIX = "engine."
QUANTUM = Decimal("0.0001")
_ENVELOPE = frozenset({"release", "run_id"})

Value = str | bool | int | Decimal
Record = dict[str, Value]


def _scalar(value: object) -> Value | None:
    if isinstance(value, Enum):
        value = value.value
    if isinstance(value, bool | int | str | Decimal):
        return value
    return None


def _lookup(event: EngineEvent, field: str) -> Value | None:
    if field in _ENVELOPE:
        return _scalar(getattr(event, field, None))
    current: object = getattr(event, "payload", None)
    for part in field.split("."):
        if current is None:
            return None
        current = getattr(current, part, None)
    return _scalar(current)


def catalog_record(event: EngineEvent) -> tuple[str, Record] | None:
    """El evento como fila del catálogo: `engine.<type>` y los campos que trae. None si no es medible."""
    kind = getattr(event, "type", None)
    if not isinstance(kind, str):
        return None
    name = OBSERVABLE_PREFIX + kind
    fields = catalog_fields(name)
    if fields is None:
        return None
    record: Record = {}
    for field in fields:
        value = _lookup(event, field)
        if value is not None:
            record[field] = value
    return name, record


def _number(value: Value | None) -> Decimal | None:
    if isinstance(value, bool) or not isinstance(value, int | Decimal):
        return None
    return Decimal(value)


def _equal(left: Value, right: Value) -> bool:
    if isinstance(left, bool) or isinstance(right, bool):
        return isinstance(left, bool) and isinstance(right, bool) and left == right
    a, b = _number(left), _number(right)
    if a is not None and b is not None:
        return a == b
    return isinstance(left, str) and isinstance(right, str) and left == right


def _holds(record: Record, predicate: Predicate) -> bool:
    value = record.get(predicate.field)
    if value is None:
        return False
    target = predicate.value
    if isinstance(target, list):
        return predicate.op == "in" and any(_equal(value, item) for item in target)
    if predicate.op == "eq":
        return _equal(value, target)
    if predicate.op == "ne":
        return not _equal(value, target)
    a, b = _number(value), _number(target)
    if a is None or b is None:
        return False
    if predicate.op == "lt":
        return a < b
    if predicate.op == "le":
        return a <= b
    if predicate.op == "gt":
        return a > b
    return predicate.op == "ge" and a >= b


def _rows(events: Iterable[EngineEvent], event: str, where: Sequence[Predicate]) -> list[Record]:
    rows: list[Record] = []
    for item in events:
        found = catalog_record(item)
        if found is not None and found[0] == event and all(_holds(found[1], p) for p in where):
            rows.append(found[1])
    return rows


def _observable(event: str) -> bool:
    return event.startswith(OBSERVABLE_PREFIX) and catalog_fields(event) is not None


def _quantize(value: Decimal) -> Decimal:
    return value.quantize(QUANTUM, ROUND_HALF_EVEN)


def evaluate_metric(expr: MetricExpr | JudgeExpr, events: Sequence[EngineEvent]) -> Decimal | None:
    """Valor de la métrica sobre `events`, o None si no se puede medir (ver el docstring del módulo)."""
    if isinstance(expr, JudgeExpr) or expr.group_by or not _observable(expr.event):
        return None
    rows = _rows(events, expr.event, expr.where)
    if expr.aggregation == "count":
        return Decimal(len(rows))
    if expr.aggregation == "rate":
        denominator = expr.denominator
        if denominator is None or not _observable(denominator.event):
            return None
        total = len(_rows(events, denominator.event, denominator.where))
        return None if total == 0 else _quantize(Decimal(len(rows)) / Decimal(total))
    values = sorted(v for v in (_number(row.get(expr.field or "")) for row in rows) if v is not None)
    if expr.aggregation == "sum":
        return sum(values, Decimal(0))
    if not values:
        return None
    if expr.aggregation == "avg":
        return _quantize(sum(values, Decimal(0)) / Decimal(len(values)))
    if expr.percentile is None:
        return None
    rank = (expr.percentile * len(values) + 99) // 100
    return values[max(rank, 1) - 1]


def assertion_holds(assertion: Assertion, events: Sequence[EngineEvent]) -> bool:
    """`at_least_one`: algún evento cumple el filtro; `none`: ninguno. Una aserción sobre un evento que la
    evaluación no observa no se cumple nunca (falla cerrada)."""
    if not _observable(assertion.event):
        return False
    found = bool(_rows(events, assertion.event, assertion.where))
    return found if assertion.expect == "at_least_one" else not found
```

- [ ] **Step 4: Verificar**

```bash
uv run pytest tests/registry/test_metric_eval.py -q
uv run mypy
uv run ruff check .
uv run lint-imports
```
Expected: 10 passed; `mypy`, `ruff` y `lint-imports` sin errores.

- [ ] **Step 5: Commit**

```bash
git add agent_core/registry/evaluation/metric_eval.py tests/registry/test_metric_eval.py
git commit -m "feat(registry): evaluador en memoria del DSL de metricas y de las aserciones sobre eventos del motor (ADR 0020)" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 3: La vara y el gate de la rama sobre los tipos de `main`; borrar los interinos

**Files:**
- Modify: `agent_core/registry/evaluation/yardstick.py` (contenido completo)
- Modify: `agent_core/registry/evaluation/report.py` (añadir `Phase`, `GateItem`, `SuiteMeasurement`, `GateRuns`)
- Modify: `agent_core/registry/evaluation/gate.py` (contenido completo: `decide` de `main` + `evaluate_gate`)
- Modify: `agent_core/registry/evaluation/scoring.py` (añadir `PLATFORM_GUARDRAILS`)
- Modify: `tests/registry/eval_support.py` (añadir `yardstick`)
- Modify: `tests/registry/test_gate.py` (contenido completo: los 4 de `decide` + los portados)
- Create: `tests/registry/test_yardstick.py`
- Delete: `agent_core/registry/evaluation/{suite,platform,double_gate}.py`, `tests/registry/yardstick/`

- [ ] **Step 1: `yardstick` en los constructores de prueba**

En `tests/registry/eval_support.py`, añadir el import `from agent_core.registry.evaluation.yardstick import Yardstick` (después del de `agent_core.domain`) y al final:

```python
def yardstick(metrics: list[MetricDef], scenarios: list[dict[str, Any]],
              thresholds: dict[str, dict[str, Any]] | None = None) -> Yardstick:
    return Yardstick(metrics=metrics, suite=suite(scenarios, thresholds))
```

- [ ] **Step 2: Portar las pruebas de la clasificación**

Crear `tests/registry/test_yardstick.py`:

```python
"""Clasificación del aflojamiento de la vara (spec de evaluación §6.2, T-EVAL-08)."""

from typing import Any

import pytest

from agent_core.registry.evaluation.yardstick import Yardstick, classify_yardstick_change
from tests.registry.eval_support import metric, scenario, suite, thr, yardstick


def kinds(base: Any, cand: Any) -> list[str]:
    return [c.kind for c in classify_yardstick_change(base, cand)]


def base_yardstick() -> Yardstick:
    return yardstick(
        [metric("m_gate"), metric("m_guard", role="guardrail", higher=False)],
        [scenario("s1", repetitions=3), scenario("s2")],
        {"m_gate": thr("0.05", "0.6"), "m_guard": thr("0", "0")})


def test_no_base_means_nothing_to_loosen() -> None:
    assert classify_yardstick_change(None, base_yardstick()) == []


def test_identical_yardstick_is_not_loosened() -> None:
    assert kinds(base_yardstick(), base_yardstick()) == []


def test_tightening_is_not_flagged() -> None:  # T-EVAL-08
    cand = yardstick(
        [metric("m_gate", role="guardrail"), metric("m_guard", role="guardrail", higher=False),
         metric("m_new"), metric("m_mon", role="monitor")],
        [scenario("s1", repetitions=5), scenario("s2"), scenario("s3")],
        {"m_gate": thr("0.01", "0.8"), "m_guard": thr("0", "0"), "m_new": thr("0.1", "0.3")})
    assert kinds(base_yardstick(), cand) == []


def test_removing_a_gate_metric_is_flagged() -> None:
    cand = yardstick([metric("m_guard", role="guardrail", higher=False)],
                     [scenario("s1", 3), scenario("s2")], {"m_guard": thr("0", "0")})
    assert kinds(base_yardstick(), cand) == ["metric_removed"]


def test_degrading_a_role_is_flagged() -> None:
    cand = yardstick([metric("m_gate", role="monitor"), metric("m_guard", role="gate", higher=False)],
                     [scenario("s1", 3), scenario("s2")],
                     {"m_gate": thr("0.05", "0.6"), "m_guard": thr("0", "0")})
    assert sorted(kinds(base_yardstick(), cand)) == ["role_degraded", "role_degraded"]


def test_changing_an_expression_or_direction_is_flagged() -> None:
    cand = yardstick(
        [metric("m_gate", event="engine.run_closed"), metric("m_guard", role="guardrail", higher=True)],
        [scenario("s1", 3), scenario("s2")],
        {"m_gate": thr("0.05", "0.6"), "m_guard": thr("0", "0")})
    assert kinds(base_yardstick(), cand) == ["metric_changed", "metric_changed"]


def _promoted(**over: Any) -> Yardstick:
    """`m_gate` promovida a guardarraíl en la propuesta, con `over` cambiando su definición."""
    higher = over.pop("higher", True)
    return yardstick(
        [metric("m_gate", role="guardrail", higher=higher, **over),
         metric("m_guard", role="guardrail", higher=False)],
        [scenario("s1", 3), scenario("s2")],
        {"m_gate": thr("0.05", "0.6"), "m_guard": thr("0", "0")})


def test_a_pure_promotion_is_not_flagged() -> None:
    assert kinds(base_yardstick(), _promoted()) == []


def test_promotion_with_an_expression_change_is_flagged() -> None:
    assert kinds(base_yardstick(), _promoted(event="engine.run_closed")) == ["metric_changed"]


def test_promotion_with_a_direction_flip_is_flagged() -> None:
    assert kinds(base_yardstick(), _promoted(higher=False)) == ["metric_changed"]


def test_promotion_that_changes_expression_and_direction_is_flagged() -> None:
    base = yardstick([metric("q")], [scenario("s1")], {"q": thr("0", "0.5")})
    cand = yardstick([metric("q", role="guardrail", higher=False, event="engine.escalated")],
                     [scenario("s1")], {"q": thr("0", "1000")})
    assert kinds(base, cand) == ["metric_changed"]


def test_degraded_role_with_changed_expression_reports_only_role_degraded() -> None:
    cand = yardstick(
        [metric("m_gate", role="monitor", event="engine.run_closed"),
         metric("m_guard", role="guardrail", higher=False)],
        [scenario("s1", 3), scenario("s2")],
        {"m_gate": thr("0.05", "0.6"), "m_guard": thr("0", "0")})
    assert kinds(base_yardstick(), cand) == ["role_degraded"]


def test_adding_a_threshold_where_the_base_had_none_widens_the_noise() -> None:
    base = yardstick([metric("q")], [scenario("s1")])
    assert kinds(base, yardstick([metric("q")], [scenario("s1")], {"q": thr("100")})) == ["noise_widened"]
    assert kinds(base, yardstick([metric("q")], [scenario("s1")], {"q": thr("0", "0.5")})) == []


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


@pytest.mark.parametrize(("floor", "expected"),
                         [("0.5", ["floor_loosened"]), (None, ["floor_loosened"]), ("0.6", []), ("0.9", [])])
def test_floor_loosening_when_higher_is_better(floor: str | None, expected: list[str]) -> None:
    cand = yardstick([metric("m_gate"), metric("m_guard", role="guardrail", higher=False)],
                     [scenario("s1", 3), scenario("s2")],
                     {"m_gate": thr("0.05", floor), "m_guard": thr("0", "0")})
    assert kinds(base_yardstick(), cand) == expected


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
    changed_steps = scenario("s2", steps=[{"op": "start"}, {"op": "turn", "text": "otra cosa"}])
    changed = yardstick(metrics, [scenario("s1", 3), changed_steps], thresholds)
    fewer_runs = yardstick(metrics, [scenario("s1", 1), scenario("s2")], thresholds)
    assert kinds(base_yardstick(), removed) == ["scenario_removed"]
    assert kinds(base_yardstick(), changed) == ["scenario_changed"]
    assert kinds(base_yardstick(), fewer_runs) == ["repetitions_lowered"]


def test_lowering_the_suite_default_repetitions_is_flagged() -> None:  # Review Focus 3
    base = Yardstick(metrics=[], suite=suite([scenario("s1", repetitions=None)], repetitions=3))
    lowered = Yardstick(metrics=[], suite=suite([scenario("s1", repetitions=None)], repetitions=2))
    explicit = Yardstick(metrics=[], suite=suite([scenario("s1", repetitions=3)], repetitions=1))
    assert kinds(base, lowered) == ["repetitions_lowered"]
    assert kinds(base, explicit) == []  # heredadas o explícitas, son las mismas corridas


def test_monitor_metrics_are_not_part_of_the_yardstick() -> None:
    base = yardstick([metric("m_mon", role="monitor")], [scenario("s1")])
    assert kinds(base, yardstick([], [scenario("s1")])) == []
    assert kinds(base, yardstick([metric("m_mon", role="monitor", event="engine.run_closed")],
                                 [scenario("s1")])) == []


def test_a_base_without_a_recorded_suite_only_compares_metrics() -> None:  # decisión D3
    base = Yardstick(metrics=[metric("m_gate"), metric("m_mon", role="monitor")], suite=None)
    assert kinds(base, yardstick([], [scenario("s1")])) == ["metric_removed"]
    kept = yardstick([metric("m_gate")], [scenario("s1")], {"m_gate": thr("0.3", "0.1")})
    assert kinds(base, kept) == []  # sin suite base no hay umbrales ni escenarios que aflojar


def test_output_is_sorted_and_deterministic() -> None:
    cand = yardstick([metric("m_gate", role="monitor")], [scenario("s2")], {})
    first = classify_yardstick_change(base_yardstick(), cand)
    assert first == classify_yardstick_change(base_yardstick(), cand)
    assert first == sorted(first, key=lambda c: (c.kind, c.target))
    assert all(c.target and c.message for c in first)
```

- [ ] **Step 3: Portar las pruebas del gate**

Reemplazar `tests/registry/test_gate.py` por (la primera sección es la de `main` sin cambios; se borra en la tarea 5):

```python
from decimal import Decimal
from typing import Any

import pytest

from agent_core.registry.evaluation.gate import decide, evaluate_gate, meets_floor, not_worse
from agent_core.registry.evaluation.report import GateItem, GateRuns, SuiteMeasurement, SuiteMetrics
from agent_core.registry.evaluation.scoring import PLATFORM_GUARDRAILS
from agent_core.registry.evaluation.yardstick import Yardstick
from agent_core.registry.suite import EvalSuite
from tests.registry.eval_support import metric, scenario, thr, yardstick
from tests.registry.helpers import suite_content

# --- gate de la métrica principal de `main` (se borra en la tarea 5) -------------------------------------

SUITE = EvalSuite.model_validate(suite_content())  # margen 0.05, piso 0.5
ZERO = {"unverified_writes": 0, "unsupported_success": 0, "sensitive_leaks": 0}


def _m(primary: str, **g: int) -> SuiteMetrics:
    return SuiteMetrics(primary=Decimal(primary), guardrails={**ZERO, **g}, runs=10)


def test_guardrail_regression_fails_even_if_primary_improves() -> None:  # T-REG-08
    verdict, checks = decide(SUITE, _m("0.9", unverified_writes=1), _m("0.5"))
    assert verdict == "fail"
    assert [c.name for c in checks if not c.passed] == ["unverified_writes"]


def test_primary_within_margin_passes_outside_fails() -> None:  # T-REG-09
    assert decide(SUITE, _m("0.76"), _m("0.80"))[0] == "pass"
    assert decide(SUITE, _m("0.74"), _m("0.80"))[0] == "fail"


def test_without_base_uses_floor_and_zero_guardrails() -> None:  # T-REG-10
    assert decide(SUITE, _m("0.5"), None)[0] == "pass"
    assert decide(SUITE, _m("0.49"), None)[0] == "fail"
    assert decide(SUITE, _m("0.9", sensitive_leaks=1), None)[0] == "fail"


def test_every_check_reports_value_base_and_threshold() -> None:
    _, checks = decide(SUITE, _m("0.8"), _m("0.8"))
    primary = next(c for c in checks if c.name == "primary")
    expected = (Decimal("0.8"), Decimal("0.8"), Decimal("0.75"))
    assert (primary.value, primary.base, primary.threshold) == expected


# --- doble vara (ADR 0020, spec de evaluación §6) --------------------------------------------------------

ZEROS = {pid: "0" for pid in PLATFORM_GUARDRAILS}
GOOD = {"quality": "0.8", "speed": "0.5", "leaks": "0"}


def measured(metrics: dict[str, str], scenarios: dict[str, bool] | None = None, status: str = "ok",
             platform: dict[str, str] | None = None) -> SuiteMeasurement:
    values = {**(ZEROS if platform is None else platform), **metrics}
    return SuiteMeasurement.model_validate(
        {"status": status, "metrics": values, "scenarios": {"s1": True} if scenarios is None else scenarios})


def base_yardstick() -> Yardstick:
    return yardstick(
        [metric("quality"), metric("speed"), metric("leaks", role="guardrail", higher=False)],
        [scenario("s1")],
        {"quality": thr("0.05", "0.5"), "speed": thr("0.1", "0.4"), "leaks": thr("0", "0")})


def failed(items: list[GateItem]) -> list[str]:
    return sorted(i.metric_id for i in items if not i.passed)


def only_new(new: SuiteMeasurement) -> GateRuns:
    return GateRuns(cand_on_new=new)


def both(base_old: SuiteMeasurement, cand_old: SuiteMeasurement,
         cand_new: SuiteMeasurement | None = None) -> GateRuns:
    return GateRuns(base_on_old=base_old, cand_on_old=cand_old,
                    cand_on_new=cand_old if cand_new is None else cand_new)


def test_direction_helpers() -> None:
    assert not_worse(Decimal("0.96"), Decimal("1"), Decimal("0.05"), True)
    assert not not_worse(Decimal("0.9"), Decimal("1"), Decimal("0.05"), True)
    assert not_worse(Decimal("1.04"), Decimal("1"), Decimal("0.05"), False)
    assert not not_worse(Decimal("1.1"), Decimal("1"), Decimal("0.05"), False)
    assert meets_floor(Decimal("0.5"), Decimal("0.5"), True)
    assert not meets_floor(Decimal("0.4"), Decimal("0.5"), True)
    assert meets_floor(Decimal("3"), Decimal("3"), False)
    assert not meets_floor(Decimal("4"), Decimal("3"), False)


def test_no_base_passes_when_every_metric_meets_its_floor() -> None:
    verdict, items = evaluate_gate(None, base_yardstick(),
                                   only_new(measured({"quality": "0.7", "speed": "0.5", "leaks": "0"})))
    assert verdict == "pass"
    assert {i.metric_id for i in items} >= {"quality", "speed", "leaks", *PLATFORM_GUARDRAILS}


def test_no_base_fails_when_a_floor_is_missing_or_not_met() -> None:  # T-EVAL-13 (antes T-REG-10)
    no_floor = yardstick([metric("quality")], [scenario("s1")], {"quality": thr("0.05")})
    verdict, items = evaluate_gate(None, no_floor, only_new(measured({"quality": "0.9"})))
    assert verdict == "fail" and failed(items) == ["quality"]
    _, below = evaluate_gate(None, base_yardstick(),
                             only_new(measured({"quality": "0.1", "speed": "0.5", "leaks": "0"})))
    assert failed(below) == ["quality"]


def test_lower_is_better_floor_is_a_ceiling() -> None:
    cand = yardstick([metric("leaks", role="guardrail", higher=False)], [scenario("s1")],
                     {"leaks": thr("0", "2")})
    assert failed(evaluate_gate(None, cand, only_new(measured({"leaks": "3"})))[1]) == ["leaks"]
    assert evaluate_gate(None, cand, only_new(measured({"leaks": "2"})))[0] == "pass"


def test_a_worse_guardrail_fails_even_if_a_gate_metric_improves() -> None:  # T-EVAL-05 (antes T-REG-08)
    base = base_yardstick()
    runs = both(measured({"quality": "0.6", "speed": "0.5", "leaks": "0"}),
                measured({"quality": "0.9", "speed": "0.5", "leaks": "1"}))
    verdict, items = evaluate_gate(base, base, runs)
    assert verdict == "fail" and failed(items) == ["leaks"]  # sin cambios: solo la juzga la vara vieja


def test_each_gate_metric_is_judged_on_its_own() -> None:  # T-EVAL-06 (antes T-REG-09)
    base = base_yardstick()
    old_base = measured({"quality": "0.80", "speed": "0.50", "leaks": "0"})
    within_noise = measured({"quality": "0.76", "speed": "0.45", "leaks": "0"})
    regressed = measured({"quality": "0.95", "speed": "0.20", "leaks": "0"})
    assert evaluate_gate(base, base, both(old_base, within_noise))[0] == "pass"
    verdict, items = evaluate_gate(base, base, both(old_base, regressed))
    assert verdict == "fail" and "speed" in failed(items) and "quality" not in failed(items)


def test_the_base_yardstick_still_applies_when_the_candidate_drops_or_loosens_it() -> None:  # T-EVAL-07
    base = base_yardstick()
    cand = yardstick([metric("quality", role="monitor")], [scenario("s1")], {})
    runs = both(measured({"quality": "0.80", "speed": "0.50", "leaks": "0"}),
                measured({"quality": "0.30", "speed": "0.10", "leaks": "0"}))
    verdict, items = evaluate_gate(base, cand, runs)
    assert verdict == "fail" and {"quality", "speed"} <= set(failed(items))


def test_a_metric_without_value_fails() -> None:  # Review Focus 1
    base = base_yardstick()
    zeros = {pid: Decimal(0) for pid in PLATFORM_GUARDRAILS}
    cand_old = SuiteMeasurement(metrics=zeros | {"leaks": Decimal(0)}, scenarios={"s1": True})
    verdict, items = evaluate_gate(base, base, both(measured(GOOD), cand_old))
    assert verdict == "fail" and {"quality", "speed"} <= set(failed(items))
    missing_base = SuiteMeasurement(metrics={"leaks": Decimal(0)}, scenarios={"s1": True})
    assert "quality" in failed(evaluate_gate(base, base, both(missing_base, measured(GOOD)))[1])


def test_a_scenario_that_passed_in_the_base_and_fails_in_the_candidate_fails() -> None:
    base = base_yardstick()
    runs = both(measured(GOOD, {"s1": True}), measured(GOOD, {"s1": False}))
    verdict, items = evaluate_gate(base, base, runs)
    assert verdict == "fail" and "scenario/s1" in failed(items)


@pytest.mark.parametrize(
    ("base_scenarios", "cand_scenarios", "expected"),
    [
        ({"s1": True}, {"s1": True}, []),
        ({"s1": True}, {"s1": False}, ["scenario/s1"]),
        ({"s1": True}, {}, ["scenario/s1"]),  # la candidata no midió el escenario
        ({}, {"s1": False}, ["scenario/s1"]),  # la base no lo midió: falla cerrado
        ({}, {}, ["scenario/s1"]),
        ({"s1": False}, {"s1": False}, []),  # ya fallaba en la base: no es regresión
        ({"s1": False}, {}, []),  # un False explícito en la base exime
    ],
)
def test_scenario_regression_fails_closed_when_unmeasured(
    base_scenarios: dict[str, bool], cand_scenarios: dict[str, bool], expected: list[str]
) -> None:
    base = base_yardstick()
    runs = both(measured(GOOD, base_scenarios), measured(GOOD, cand_scenarios), measured(GOOD, {"s1": True}))
    assert failed(evaluate_gate(base, base, runs)[1]) == expected


def test_a_metric_whose_identity_changed_is_judged_on_the_new_yardstick() -> None:
    base = base_yardstick()
    old = measured(GOOD)

    def candidate(floor: str | None, **over: Any) -> Yardstick:
        return yardstick(
            [metric("quality", **over), metric("speed"), metric("leaks", role="guardrail", higher=False)],
            [scenario("s1")],
            {"quality": thr("0.05", floor), "speed": thr("0.1", "0.4"), "leaks": thr("0", "0")})

    def judge(cand: Yardstick, quality: str) -> tuple[str, list[GateItem]]:
        return evaluate_gate(base, cand, both(old, old, measured({**GOOD, "quality": quality})))

    for change in ({"event": "engine.run_closed"}, {"higher": False}):
        higher = change.get("higher", True)
        verdict, items = judge(candidate("0.5", **change), "0.6" if higher else "0.3")
        assert verdict == "pass"
        assert ("quality", "new_yardstick") in {(i.metric_id, i.phase) for i in items}
        assert failed(judge(candidate("0.5", **change), "0.4" if higher else "0.9")[1]) == ["quality"]
        assert failed(judge(candidate(None, **change), "0.8")[1]) == ["quality"]


def test_a_guardrail_with_a_declared_noise_margin_still_has_zero_tolerance() -> None:
    base = yardstick([metric("leaks", role="guardrail", higher=False)], [scenario("s1")],
                     {"leaks": thr("0.5", "0")})
    _, items = evaluate_gate(base, base, both(measured({"leaks": "0"}), measured({"leaks": "0.3"})))
    item = next(i for i in items if i.metric_id == "leaks" and i.phase == "base_yardstick")
    assert not item.passed and item.noise_margin == Decimal(0)
    assert failed(items) == ["leaks"]


def _four_metrics() -> list[Any]:
    return [metric("quality"), metric("speed"), metric("leaks", role="guardrail", higher=False),
            metric("fresh")]


def test_new_metrics_and_scenarios_must_meet_the_new_yardstick() -> None:
    cand = yardstick(
        _four_metrics(),
        [scenario("s1"), scenario("s_new")],
        {"quality": thr("0.05", "0.5"), "speed": thr("0.1", "0.4"), "leaks": thr("0", "0"),
         "fresh": thr("0", "0.5")})
    old = measured(GOOD)
    ok_new = measured({**GOOD, "fresh": "0.6"}, {"s1": True, "s_new": True})
    bad_new = measured({**GOOD, "fresh": "0.1"}, {"s1": True, "s_new": False})
    assert evaluate_gate(base_yardstick(), cand, both(old, old, ok_new))[0] == "pass"
    verdict, items = evaluate_gate(base_yardstick(), cand, both(old, old, bad_new))
    assert verdict == "fail" and {"fresh", "scenario/s_new"} <= set(failed(items))


def test_a_new_metric_without_floor_fails() -> None:
    cand = yardstick(
        _four_metrics(),
        [scenario("s1")],
        {"quality": thr("0.05", "0.5"), "speed": thr("0.1", "0.4"), "leaks": thr("0", "0"),
         "fresh": thr("0")})
    old = measured({**GOOD, "fresh": "1"})
    assert failed(evaluate_gate(base_yardstick(), cand, both(old, old))[1]) == ["fresh"]


def test_platform_guardrails_must_be_zero() -> None:
    runs = only_new(measured({"quality": "0.9", "speed": "0.9", "leaks": "0"},
                             platform=ZEROS | {"platform_pii_leak": "1"}))
    verdict, items = evaluate_gate(None, base_yardstick(), runs)
    assert verdict == "fail" and failed(items) == ["platform_pii_leak"]


def test_a_measured_pii_leak_on_the_candidate_old_suite_fails_even_if_not_worse_than_base() -> None:
    base = base_yardstick()
    leaky = measured(GOOD, platform=ZEROS | {"platform_pii_leak": "2"})
    verdict, items = evaluate_gate(base, base, both(leaky, leaky, measured(GOOD)))
    assert verdict == "fail" and failed(items) == ["platform_pii_leak"]
    assert next(i for i in items if i.metric_id == "platform_pii_leak").base_value == Decimal(2)


def test_a_platform_guardrail_that_was_not_measured_fails() -> None:
    partial = {pid: "0" for pid in PLATFORM_GUARDRAILS[:-1]}
    runs = only_new(measured({"quality": "0.9", "speed": "0.9", "leaks": "0"}, platform=partial))
    assert failed(evaluate_gate(None, base_yardstick(), runs)[1]) == [PLATFORM_GUARDRAILS[-1]]


@pytest.mark.parametrize("which", ["base_on_old", "cand_on_old", "cand_on_new"])
def test_failed_infra_is_not_a_verdict(which: str) -> None:  # T-EVAL-15
    base = base_yardstick()
    ok = measured(GOOD)
    runs = {"base_on_old": ok, "cand_on_old": ok, "cand_on_new": ok} | {
        which: SuiteMeasurement(status="failed_infra")}
    assert evaluate_gate(base, base, GateRuns(**runs)) == ("failed_infra", [])


def test_a_base_without_its_runs_is_a_programming_error() -> None:
    with pytest.raises(ValueError):
        evaluate_gate(base_yardstick(), base_yardstick(), only_new(measured({})))


def test_items_carry_value_base_and_margin_apart_from_floor() -> None:  # decisión D8
    base = base_yardstick()
    _, items = evaluate_gate(base, base, both(measured({"quality": "0.80", "speed": "0.50", "leaks": "0"}),
                                              measured({"quality": "0.85", "speed": "0.50", "leaks": "0"})))
    item = next(i for i in items if i.metric_id == "quality" and i.phase == "base_yardstick")
    assert (item.value, item.base_value, item.noise_margin, item.floor, item.role) == (
        Decimal("0.85"), Decimal("0.80"), Decimal("0.05"), None, "gate")


@pytest.mark.parametrize("which", ["base_on_old", "cand_on_old"])
def test_a_platform_guardrail_unmeasured_on_the_old_suite_fails_with_a_base(which: str) -> None:
    base = base_yardstick()
    full = measured(GOOD)
    gap = PLATFORM_GUARDRAILS[-1]
    partial = SuiteMeasurement(metrics={k: v for k, v in full.metrics.items() if k != gap},
                               scenarios={"s1": True})
    reports = {"base_on_old": full, "cand_on_old": full} | {which: partial}
    verdict, items = evaluate_gate(base, base, GateRuns(**reports, cand_on_new=full))
    assert verdict == "fail" and failed(items) == [gap]


def test_a_base_without_a_recorded_suite_is_judged_by_floors_only() -> None:  # decisión D3
    base = Yardstick(metrics=base_yardstick().metrics, suite=None)
    verdict, items = evaluate_gate(base, base_yardstick(),
                                   only_new(measured({"quality": "0.6", "speed": "0.5", "leaks": "0"})))
    assert verdict == "pass" and {i.phase for i in items} == {"new_yardstick", "platform"}
    quality = next(i for i in items if i.metric_id == "quality")
    assert (quality.floor, quality.noise_margin) == (Decimal("0.5"), None)
    _, low = evaluate_gate(base, base_yardstick(),
                           only_new(measured({"quality": "0.4", "speed": "0.5", "leaks": "0"})))
    assert failed(low) == ["quality"]
```

- [ ] **Step 4: Verificar que fallan**

Run: `uv run pytest tests/registry/test_gate.py tests/registry/test_yardstick.py -q`
Expected: error de colección (`ImportError: cannot import name 'evaluate_gate'` y `cannot import name 'Yardstick'` desde un `yardstick.py` que aún importa el `EvalSuite` interino; `test_yardstick` falla al validar escenarios del formato de `main`).

- [ ] **Step 5: `PLATFORM_GUARDRAILS` en `scoring.py`**

En `agent_core/registry/evaluation/scoring.py`, después de `GUARDRAILS = (...)`:

```python
# Guardarraíles de plataforma (ADR 0020 §7): universales, con prefijo reservado (`MT-05`) y tolerancia cero.
PLATFORM_GUARDRAILS: tuple[str, ...] = (
    "platform_pii_leak",
    "platform_unverified_success_claim",
    "platform_unverified_write",
    "platform_unapproved_knowledge_citation",
)
```

- [ ] **Step 6: Tipos del veredicto en `report.py`**

En `agent_core/registry/evaluation/report.py`, añadir el import `from agent_core.domain.metrics import MetricRole` y, después de `Label`:

```python
Phase = Literal["base_yardstick", "new_yardstick", "platform"]
```

y después de `MetricCheck`:

```python
class GateItem(_M):
    """Un elemento del veredicto: una métrica, un escenario (`scenario/<id>`) o un guardarraíl de plataforma.

    `noise_margin` es la tolerancia frente a la base (vara vieja); `floor`, el piso (vara nueva y
    plataforma: un mínimo si mayor es mejor y un máximo si menor es mejor). No hay puntaje compuesto."""

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
    """Lo medido al correr una suite sobre una release: valor por métrica (incluidos los `platform_*`) y si
    cada escenario pasó. Una clave ausente es «sin medir», y el gate la cuenta como fallo."""

    status: Literal["ok", "failed_infra"] = "ok"
    metrics: dict[str, Decimal] = Field(default_factory=dict)
    scenarios: dict[str, bool] = Field(default_factory=dict)


class GateRuns(_M):
    """Las corridas del gate. Las dos de la vara vieja existen solo si la base registró su suite."""

    base_on_old: SuiteMeasurement | None = None
    cand_on_old: SuiteMeasurement | None = None
    cand_on_new: SuiteMeasurement
```

- [ ] **Step 7: Reescribir `agent_core/registry/evaluation/yardstick.py`**

```python
"""La vara de evaluación de una release y lo que la afloja (ADR 0020, spec de evaluación §6.2).

Solo se marca lo que afloja la vara. Endurecer (añadir escenarios o métricas, subir pisos, bajar márgenes,
promover roles, subir repeticiones) no se marca. Solo las métricas `gate` y `guardrail` forman parte de la
vara.
"""

from enum import StrEnum

from agent_core.domain import MetricDef, Model
from agent_core.registry.suite import DatasetScenario, EvalSuite, MetricThreshold, Scenario

_RANK = {"monitor": 1, "gate": 2, "guardrail": 3}


class Yardstick(Model):
    """Definiciones de métricas y suite con que se mide: la de la release base o la de la candidata.

    `suite` es None si la release base no registró ninguna (`eval_suite_refs` vacío): entonces la vara
    vieja no se puede correr y el gate aplica los pisos de la vara nueva (decisión D3)."""

    metrics: list[MetricDef]
    suite: EvalSuite | None = None


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


def _change(kind: YardstickChangeKind, target: str, message: str) -> YardstickChange:
    return YardstickChange(kind=kind, target=target, message=message)


def _metric_changes(base: Yardstick, cand: Yardstick) -> list[YardstickChange]:
    found: list[YardstickChange] = []
    cand_all = {m.id: m for m in cand.metrics}
    cand_thresholds = cand.suite.thresholds if cand.suite is not None else {}
    for metric_id, base_metric in sorted(_gating(base.metrics).items()):
        cand_metric = cand_all.get(metric_id)
        if cand_metric is None:
            found.append(_change(YardstickChangeKind.metric_removed, metric_id,
                                 f"la métrica {metric_id} ya no está declarada"))
            continue
        if _RANK[cand_metric.role] < _RANK[base_metric.role]:
            found.append(_change(YardstickChangeKind.role_degraded, metric_id,
                                 f"{metric_id} pasa de {base_metric.role} a {cand_metric.role}"))
            continue
        # Solo la definición cuenta aquí (dirección y expresión), con o sin promoción de rol.
        if metric_identity(cand_metric)[1:] != metric_identity(base_metric)[1:]:
            found.append(_change(YardstickChangeKind.metric_changed, metric_id,
                                 f"cambió la expresión o la dirección de {metric_id}"))
            continue
        if base.suite is None:
            continue  # la base no midió con ninguna suite: no hay umbral que aflojar (D3)
        base_thr, cand_thr = base.suite.thresholds.get(metric_id), cand_thresholds.get(metric_id)
        if base_thr is None:
            # Sin umbral en la base el gate medía con margen 0 y sin piso.
            if cand_thr is not None and cand_thr.noise_margin > 0:
                found.append(_change(YardstickChangeKind.noise_widened, metric_id,
                                     f"el margen de ruido de {metric_id} aumentó"))
            continue
        if cand_thr is None:
            found.append(_change(YardstickChangeKind.threshold_removed, metric_id,
                                 f"{metric_id} ya no tiene umbral en la suite"))
            continue
        if _floor_loosened(base_thr, cand_thr, base_metric.higher_is_better):
            found.append(_change(YardstickChangeKind.floor_loosened, metric_id,
                                 f"el piso de {metric_id} se aflojó"))
        if cand_thr.noise_margin > base_thr.noise_margin:
            found.append(_change(YardstickChangeKind.noise_widened, metric_id,
                                 f"el margen de ruido de {metric_id} aumentó"))
    return found


def _same_except_runs(a: Scenario | DatasetScenario, b: Scenario | DatasetScenario) -> bool:
    return a.model_copy(update={"repetitions": None}) == b.model_copy(update={"repetitions": None})


def _scenario_changes(base: Yardstick, cand: Yardstick) -> list[YardstickChange]:
    base_suite, cand_suite = base.suite, cand.suite
    if base_suite is None:
        return []
    cand_by_id = {s.id: s for s in cand_suite.scenarios} if cand_suite is not None else {}
    found: list[YardstickChange] = []
    for scenario in sorted(base_suite.scenarios, key=lambda s: s.id):
        other = cand_by_id.get(scenario.id)
        if other is None or cand_suite is None:
            found.append(_change(YardstickChangeKind.scenario_removed, scenario.id,
                                 f"el escenario {scenario.id} ya no está en la suite"))
            continue
        if not _same_except_runs(scenario, other):
            found.append(_change(YardstickChangeKind.scenario_changed, scenario.id,
                                 f"cambió el escenario: {scenario.id}"))
        elif cand_suite.repetitions_of(other) < base_suite.repetitions_of(scenario):
            found.append(_change(YardstickChangeKind.repetitions_lowered, scenario.id,
                                 f"bajaron las repeticiones: {scenario.id}"))
    return found


def classify_yardstick_change(base: Yardstick | None, cand: Yardstick) -> list[YardstickChange]:
    """Los cambios que aflojan la vara de `cand` frente a `base`. Sin base no hay nada que aflojar."""
    if base is None:
        return []
    found = [*_metric_changes(base, cand), *_scenario_changes(base, cand)]
    return sorted(found, key=lambda c: (c.kind.value, c.target))
```

Nota: el `_floor_loosened` interino recibía las dos direcciones para no marcar `floor_loosened` cuando la dirección cambia; aquí no hace falta porque un cambio de dirección ya hace `continue` como `metric_changed` antes de llegar.

- [ ] **Step 8: Reescribir `agent_core/registry/evaluation/gate.py`**

```python
"""Veredicto del gate (ADR 0020, spec de evaluación §6): doble vara, sin puntaje compuesto.

`decide` (métrica principal de `main`) se borra en la tarea 5 del plan de integración.
"""

from decimal import Decimal

from agent_core.domain import MetricDef
from agent_core.registry.evaluation.report import (
    GateItem,
    GateRuns,
    MetricCheck,
    SuiteMeasurement,
    SuiteMetrics,
    Verdict,
)
from agent_core.registry.evaluation.scoring import GUARDRAILS, PLATFORM_GUARDRAILS
from agent_core.registry.evaluation.yardstick import Yardstick, metric_identity
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


def not_worse(candidate: Decimal, base: Decimal, margin: Decimal, higher_is_better: bool) -> bool:
    """La candidata no es peor que la base, con `margin` de tolerancia en la dirección de la métrica."""
    return candidate >= base - margin if higher_is_better else candidate <= base + margin


def meets_floor(value: Decimal, floor: Decimal, higher_is_better: bool) -> bool:
    """El piso es un mínimo si mayor es mejor y un máximo si menor es mejor."""
    return value >= floor if higher_is_better else value <= floor


def _gating(metrics: list[MetricDef]) -> list[MetricDef]:
    return sorted((m for m in metrics if m.role in ("gate", "guardrail")), key=lambda m: m.id)


def _base_items(metrics: list[MetricDef], suite: EvalSuite, base_run: SuiteMeasurement,
                cand_run: SuiteMeasurement) -> list[GateItem]:
    items: list[GateItem] = []
    for metric in _gating(metrics):
        threshold = suite.thresholds.get(metric.id)
        margin = Decimal(0) if metric.role == "guardrail" or threshold is None else threshold.noise_margin
        base_value, value = base_run.metrics.get(metric.id), cand_run.metrics.get(metric.id)
        if base_value is None or value is None:
            items.append(GateItem(metric_id=metric.id, phase="base_yardstick", role=metric.role, value=value,
                                  base_value=base_value, noise_margin=margin, passed=False,
                                  reason="la métrica no se pudo calcular en la base o en la candidata"))
            continue
        ok = not_worse(value, base_value, margin, metric.higher_is_better)
        items.append(GateItem(metric_id=metric.id, phase="base_yardstick", role=metric.role, value=value,
                              base_value=base_value, noise_margin=margin, passed=ok,
                              reason="" if ok else "empeora frente a la base"))
    for scenario in sorted(suite.scenarios, key=lambda s: s.id):
        before, after = base_run.scenarios.get(scenario.id), cand_run.scenarios.get(scenario.id)
        if before is False:
            continue  # ya fallaba en la base: no es una regresión
        if before is None or after is None:
            reason = "el escenario no se midió en la base o en la candidata"
        elif after is not True:
            reason = "el escenario pasaba en la base y falla en la candidata"
        else:
            continue
        items.append(GateItem(metric_id=f"scenario/{scenario.id}", phase="base_yardstick", passed=False,
                              reason=reason))
    return items


def _new_items(old: Yardstick | None, metrics: list[MetricDef], suite: EvalSuite,
               run: SuiteMeasurement) -> list[GateItem]:
    items: list[GateItem] = []
    base_metrics = {m.id: m for m in _gating(old.metrics)} if old is not None else {}
    base_scenarios = {s.id: s for s in old.suite.scenarios} if old is not None and old.suite else {}
    for metric in _gating(metrics):
        previous = base_metrics.get(metric.id)
        if previous is not None and metric_identity(previous) == metric_identity(metric):
            continue  # sin cambios: ya la juzgó la vara vieja
        threshold = suite.thresholds.get(metric.id)
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
                              floor=floor, passed=ok, reason=reason))
    for scenario in sorted(suite.scenarios, key=lambda s: s.id):
        if base_scenarios.get(scenario.id) == scenario:
            continue
        ok = run.scenarios.get(scenario.id) is True
        items.append(GateItem(metric_id=f"scenario/{scenario.id}", phase="new_yardstick", passed=ok,
                              reason="" if ok else "alguna expectativa o aserción no se cumple"))
    return items


def _platform_items(runs: GateRuns) -> list[GateItem]:
    items: list[GateItem] = []
    has_base = runs.base_on_old is not None and runs.cand_on_old is not None
    for guardrail in PLATFORM_GUARDRAILS:
        value = runs.cand_on_new.metrics.get(guardrail)
        base_value = runs.base_on_old.metrics.get(guardrail) if runs.base_on_old else None
        old_value = runs.cand_on_old.metrics.get(guardrail) if runs.cand_on_old else None
        if value is None:
            ok, reason = False, "el guardarraíl de plataforma no se midió"
        elif has_base and (base_value is None or old_value is None):
            ok, reason = False, "el guardarraíl de plataforma no se midió en la suite vieja"
        elif value != 0 or (has_base and old_value != 0):
            ok, reason = False, "el guardarraíl de plataforma debe valer 0"
        else:
            ok, reason = True, ""
        items.append(GateItem(metric_id=guardrail, phase="platform", role="guardrail", value=value,
                              base_value=base_value, floor=Decimal(0), passed=ok, reason=reason))
    return items


def evaluate_gate(base: Yardstick | None, cand: Yardstick, runs: GateRuns) -> tuple[Verdict, list[GateItem]]:
    """Veredicto de la candidata frente a su base (si existe). `failed_infra` no es pase ni fallo.

    Con `base.suite = None` (la base no registró suite, D3) solo se aplican la vara nueva y la plataforma.

    Precondiciones que esta función NO verifica (quien la llama las garantiza): `suite_problems` vacío para la
    suite de `cand`; ids de métrica únicos (`MT-03`); y que cada medición corresponda a su suite y su release:
    `base_on_old` es la base con la suite vieja, `cand_on_old` la candidata con la suite vieja y `cand_on_new`
    la candidata con la suite nueva (`GateRuns` no lleva la suite ni el `candidate_hash`)."""
    measured = [r for r in (runs.base_on_old, runs.cand_on_old, runs.cand_on_new) if r is not None]
    if any(r.status == "failed_infra" for r in measured):
        return "failed_infra", []
    if cand.suite is None:
        raise ValueError("la vara nueva necesita su suite")
    old = base if base is not None and base.suite is not None else None
    items: list[GateItem] = []
    if old is not None and old.suite is not None:
        if runs.base_on_old is None or runs.cand_on_old is None:
            raise ValueError("con vara vieja se necesitan `base_on_old` y `cand_on_old`")
        items += _base_items(old.metrics, old.suite, runs.base_on_old, runs.cand_on_old)
    items += _new_items(old, cand.metrics, cand.suite, runs.cand_on_new)
    items += _platform_items(runs)
    return ("pass" if all(item.passed for item in items) else "fail"), items
```

- [ ] **Step 9: Borrar los interinos**

```bash
git rm agent_core/registry/evaluation/suite.py agent_core/registry/evaluation/platform.py agent_core/registry/evaluation/double_gate.py
git rm -r tests/registry/yardstick
```

Comprobar que nada los importa: `uv run python -c "import agent_core.registry"` y buscar con Grep `evaluation.suite|evaluation.platform|double_gate` en `agent_core`, `testing` y `tests` (solo deben quedar menciones en `docs/`, que se corrigen en la tarea 11).

- [ ] **Step 10: Verificar**

```bash
uv run pytest tests/registry tests/composition -q
uv run mypy
uv run ruff check .
uv run lint-imports
```
Expected: todas pasan; sin errores.

- [ ] **Step 11: Commit**

```bash
git add -A agent_core/registry/evaluation tests/registry
git commit -m "feat(registry): vara y gate con doble vara sobre la suite del registry; se retiran los modulos interinos (ADR 0020)" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Calificación con guardarraíles de plataforma, aserciones y medición de una suite

**Files:**
- Modify: `agent_core/registry/evaluation/scoring.py` (contenido completo; `aggregate` se queda hasta la tarea 5)
- Modify: `agent_core/registry/evaluation/gate.py` (`decide` usa `PLATFORM_GUARDRAILS`)
- Modify: `tests/registry/test_scoring.py` (contenido completo)
- Modify: `tests/registry/service_world.py`, `tests/registry/test_gate.py` (claves renombradas)

- [ ] **Step 1: Escribir las pruebas**

Reemplazar `tests/registry/test_scoring.py`:

```python
from decimal import Decimal
from typing import Any

from agent_core.domain import (
    ActionDispatched,
    ActionDispatchedPayload,
    ActionVerified,
    ActionVerifiedPayload,
    EngineEvent,
    EntityRef,
    Escalated,
    EscalatedPayload,
    Outcome,
    ResponseEmitted,
    RunClosed,
    RunClosedPayload,
)
from agent_core.registry.evaluation.scoring import PLATFORM_GUARDRAILS, aggregate, measure, score_run
from agent_core.registry.suite import Assertion, Expect
from testing.builders import NOW
from tests.registry.eval_support import metric, scenario, suite

_BASE: dict[str, Any] = {"run_id": "run-1", "release": "rel", "ts": NOW}
ZERO = {name: 0 for name in PLATFORM_GUARDRAILS}


def _dispatched(aid: str, tool: str = "radicar_pqr") -> EngineEvent:
    payload = ActionDispatchedPayload(action_id=aid, tool=EntityRef(id=tool, version="1.0.0"))
    return ActionDispatched(event_id="e-d", **_BASE, payload=payload)


def _verified(aid: str, result: str = "verified") -> EngineEvent:
    payload = ActionVerifiedPayload(action_id=aid, result=result, readback_call_id="c")  # type: ignore[arg-type]
    return ActionVerified(event_id=f"e-v-{aid}", **_BASE, payload=payload)


def _closed(outcome: str) -> EngineEvent:
    payload = RunClosedPayload(outcome=Outcome(outcome), closed_by="flow")
    return RunClosed(event_id="e-c", **_BASE, payload=payload)


def _escalated() -> EngineEvent:
    payload = EscalatedPayload(
        reason_code="policy:test", target_queue="q", priority="normal", handoff_ref="h")
    return Escalated(event_id="e-x", **_BASE, payload=payload)


def _emitted(kind: str, ok: bool, failures: list[str]) -> EngineEvent:
    return ResponseEmitted.model_validate({**_BASE, "event_id": "e-r", "payload": {
        "kind": kind, "validator": {"ok": ok, "failures": failures}, "fallback_used": kind == "template"}})


def test_resolved_with_verified_action_passes() -> None:  # T-REG-25
    events = [_dispatched("a1"), _verified("a1"), _closed("resolved")]
    expect = Expect(outcome=Outcome.resolved, actions_verified=["radicar_pqr"], escalated=False)
    s = score_run(events, expect, [])
    assert s.passed and s.guardrails == ZERO


def test_unverified_write_and_unsupported_success_are_counted() -> None:
    events = [_dispatched("a1"), _verified("a1", "failed"), _closed("resolved")]
    s = score_run(events, Expect(outcome=Outcome.resolved), [])
    assert s.guardrails["platform_unverified_write"] == 1
    assert s.guardrails["platform_unverified_success_claim"] == 1


def test_outcome_mismatch_and_missing_action_fail_with_reasons() -> None:
    expect = Expect(outcome=Outcome.resolved, actions_verified=["radicar_pqr"])
    s = score_run([_closed("cancelled")], expect, [])
    assert not s.passed and len(s.failures) == 2


def test_escalation_expectation() -> None:
    assert not score_run([_escalated()], Expect(escalated=False), []).passed
    assert score_run([_escalated()], Expect(escalated=True), []).escalated


def test_sensitive_value_in_any_event_is_a_leak() -> None:
    assert score_run([_dispatched("4111-1111")], Expect(), ["4111-1111"]).guardrails["platform_pii_leak"] == 1


def test_unapproved_page_citation_is_counted_only_on_generated_answers() -> None:  # decisión D6
    events = [_emitted("generated", False, ["page_audience"]), _emitted("template", True, ["page_citations"]),
              _emitted("generated", True, [])]
    assert score_run(events, Expect(), []).guardrails["platform_unapproved_knowledge_citation"] == 1


def test_a_failed_assertion_fails_the_run() -> None:
    must = Assertion(event="engine.escalated")
    never = Assertion(event="engine.escalated", expect="none")
    assert score_run([_escalated()], Expect(), [], [must]).passed
    failing = score_run([_escalated()], Expect(), [], [never])
    assert not failing.passed and "aserción 1" in failing.failures[0]


def test_platform_guardrails_are_reserved_names() -> None:
    assert len(PLATFORM_GUARDRAILS) == len(set(PLATFORM_GUARDRAILS)) == 4
    assert all(name.startswith("platform_") for name in PLATFORM_GUARDRAILS)


RESOLVED = metric("resueltos", event="engine.run_closed",
                  where=[{"field": "outcome", "op": "eq", "value": "resolved"}])


def test_measure_pools_events_and_needs_every_repetition() -> None:  # decisión D4
    ok = score_run([_closed("resolved")], Expect(outcome=Outcome.resolved), [])
    bad = score_run([_closed("failed")], Expect(outcome=Outcome.resolved), [])
    write = score_run([_dispatched("a1")], Expect(), [])
    s = suite([scenario("s1", repetitions=2), scenario("s2")])
    m = measure(s, [RESOLVED], [("s1", ok, [_closed("resolved")]), ("s1", bad, [_closed("failed")]),
                                ("s2", write, [_dispatched("a1")])])
    assert m.scenarios == {"s1": False, "s2": True}
    assert m.metrics["resueltos"] == Decimal(1)
    assert m.metrics["platform_unverified_write"] == Decimal(1)
    assert {k for k in m.metrics if k.startswith("platform_")} == set(PLATFORM_GUARDRAILS)


def test_measure_without_runs_measures_nothing() -> None:
    assert measure(suite([scenario("s1")]), [RESOLVED], []).model_dump() == {
        "status": "ok", "metrics": {}, "scenarios": {}}


def test_unmeasurable_metrics_are_left_out() -> None:  # decisión D5
    funnel = metric("embudo", role="monitor", event="registry.proposal_created")
    ok = score_run([_closed("resolved")], Expect(), [])
    m = measure(suite([scenario("s1")]), [funnel], [("s1", ok, [_closed("resolved")])])
    assert "embudo" not in m.metrics


def test_aggregate_primary_is_decimal_fraction() -> None:  # se borra en la tarea 5
    ok = score_run([_closed("resolved")], Expect(outcome=Outcome.resolved), [])
    bad = score_run([_closed("failed")], Expect(outcome=Outcome.resolved), [])
    m = aggregate([ok, ok, bad])
    assert str(m.primary) == "0.6667" and m.runs == 3
```

- [ ] **Step 2: Verificar que fallan**

Run: `uv run pytest tests/registry/test_scoring.py -q`
Expected: `ImportError: cannot import name 'measure'`.

- [ ] **Step 3: Reescribir `agent_core/registry/evaluation/scoring.py`**

```python
"""Calificación de una corrida y medición de una suite desde los eventos del motor (registry §6.2; spec de
evaluación §6 y §7). Pura y determinista."""

from collections.abc import Sequence
from decimal import ROUND_HALF_EVEN, Decimal

from agent_core.domain import (
    ActionDispatched,
    ActionVerified,
    EngineEvent,
    Escalated,
    MetricDef,
    ResponseEmitted,
    RunClosed,
    dumps,
)
from agent_core.registry.evaluation.metric_eval import assertion_holds, evaluate_metric
from agent_core.registry.evaluation.report import RunScore, SuiteMeasurement, SuiteMetrics
from agent_core.registry.suite import Assertion, EvalSuite, Expect

# Guardarraíles de plataforma (ADR 0020 §7): universales, con prefijo reservado (`MT-05`) y tolerancia cero.
PLATFORM_GUARDRAILS: tuple[str, ...] = (
    "platform_pii_leak",
    "platform_unverified_success_claim",
    "platform_unverified_write",
    "platform_unapproved_knowledge_citation",
)
# Comprobaciones 6 y 7 de M8 (citas a páginas). Una respuesta generada que las incumple cita lo no
# aprobado; M8 no emite ninguna así, de modo que el guardarraíl vigila que eso se mantenga (decisión D6).
_PAGE_CHECKS = frozenset({"page_citations", "page_audience"})
_QUANT = Decimal("0.0001")  # T5: se borra con `aggregate`

RunEvidence = tuple[str, RunScore, Sequence[EngineEvent]]  # (id del escenario, calificación, eventos del run)


def _unapproved_citations(events: Sequence[EngineEvent]) -> int:
    return sum(1 for e in events
               if isinstance(e, ResponseEmitted) and e.payload.kind == "generated"
               and (not e.payload.validator.ok or bool(_PAGE_CHECKS & set(e.payload.validator.failures))))


def score_run(events: Sequence[EngineEvent], expect: Expect, sensitive: Sequence[str],
              assertions: Sequence[Assertion] = ()) -> RunScore:
    dispatched = {e.payload.action_id: e.payload.tool.id for e in events if isinstance(e, ActionDispatched)}
    verified = {e.payload.action_id for e in events
                if isinstance(e, ActionVerified) and e.payload.result == "verified"}
    closed = [e for e in events if isinstance(e, RunClosed)]
    outcome = closed[-1].payload.outcome.value if closed else None
    escalated = any(isinstance(e, Escalated) for e in events)
    unverified = sum(1 for aid in dispatched if aid not in verified)
    serialized = [dumps(e) for e in events]
    leaks = sum(text.count(value) for value in sensitive if value for text in serialized)
    guardrails = {
        "platform_pii_leak": leaks,
        "platform_unverified_success_claim": 1 if outcome == "resolved" and unverified else 0,
        "platform_unverified_write": unverified,
        "platform_unapproved_knowledge_citation": _unapproved_citations(events),
    }
    failures: list[str] = []
    if expect.outcome is not None and outcome != expect.outcome.value:
        failures.append(f"se esperaba outcome {expect.outcome.value} y fue {outcome}")
    done = {dispatched[aid] for aid in verified if aid in dispatched}
    for tool in expect.actions_verified:
        if tool not in done:
            failures.append(f"la acción {tool} no quedó verificada")
    if expect.escalated is not None and escalated != expect.escalated:
        failures.append("se esperaba escalamiento" if expect.escalated else "escaló sin esperarlo")
    for i, assertion in enumerate(assertions, start=1):
        if not assertion_holds(assertion, events):
            failures.append(f"la aserción {i} ({assertion.event[:60]}) no se cumple")
    return RunScore(passed=not failures, failures=failures, guardrails=guardrails, outcome=outcome,
                    escalated=escalated)


def measure(suite: EvalSuite, metrics: Sequence[MetricDef], runs: Sequence[RunEvidence]) -> SuiteMeasurement:
    """Lo que el gate necesita de una corrida de `suite` (decisión D4): cada métrica del agente sobre todos
    los eventos de la corrida juntos, cada guardarraíl de plataforma sumado sobre las corridas, y si cada
    escenario pasó en todas sus repeticiones. Lo que no se puede medir queda fuera: el gate lo cuenta como
    fallo."""
    if not runs:
        return SuiteMeasurement()
    pooled = [event for _, _, events in runs for event in events]
    values: dict[str, Decimal] = {}
    for metric in metrics:
        value = evaluate_metric(metric.expr, pooled)
        if value is not None:
            values[metric.id] = value
    for name in PLATFORM_GUARDRAILS:
        if all(name in score.guardrails for _, score, _ in runs):
            values[name] = Decimal(sum(score.guardrails[name] for _, score, _ in runs))
    scenarios: dict[str, bool] = {}
    for scenario in suite.scenarios:
        scores = [score for sid, score, _ in runs if sid == scenario.id]
        if scores:
            scenarios[scenario.id] = all(s.passed for s in scores)
    return SuiteMeasurement(metrics=values, scenarios=scenarios)


def aggregate(scores: Sequence[RunScore]) -> SuiteMetrics:  # T5: se borra con la métrica principal
    runs = len(scores)
    passed = sum(1 for s in scores if s.passed)
    primary = (Decimal(passed) / Decimal(runs)).quantize(_QUANT, ROUND_HALF_EVEN) if runs else Decimal(0)
    guardrails = {g: sum(s.guardrails.get(g, 0) for s in scores) for g in PLATFORM_GUARDRAILS}
    return SuiteMetrics(primary=primary, guardrails=guardrails, runs=runs)
```

- [ ] **Step 4: `decide` y las pruebas de `main` usan los nombres nuevos**

- `agent_core/registry/evaluation/gate.py`: el import pasa a `from agent_core.registry.evaluation.scoring import PLATFORM_GUARDRAILS` y en `decide`, `for name in GUARDRAILS:` pasa a `for name in PLATFORM_GUARDRAILS:`.
- `tests/registry/test_gate.py` (sección de `main`): `ZERO = {name: 0 for name in PLATFORM_GUARDRAILS}`; `_m("0.9", unverified_writes=1)` → `_m("0.9", platform_unverified_write=1)` y la aserción a `["platform_unverified_write"]`; `_m("0.9", sensitive_leaks=1)` → `_m("0.9", platform_pii_leak=1)`.
- `tests/registry/service_world.py`: añadir `from agent_core.registry.evaluation.scoring import PLATFORM_GUARDRAILS` y `ZERO = {name: 0 for name in PLATFORM_GUARDRAILS}`.

- [ ] **Step 5: Verificar**

```bash
uv run pytest tests/registry tests/composition -q
uv run mypy
uv run ruff check .
```
Expected: todas pasan (incluida `tests/composition/test_registry_harness.py`: `score_run(events, expect, sensitive)` conserva su firma); sin errores.

- [ ] **Step 6: Commit**

```bash
git add agent_core/registry/evaluation/scoring.py agent_core/registry/evaluation/gate.py tests/registry/test_scoring.py tests/registry/test_gate.py tests/registry/service_world.py
git commit -m "feat(registry): guardarrailes de plataforma, aserciones y medicion de una suite en la calificacion (ADR 0020)" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 5: `EvalPort` con doble vara; un solo `EvalReport`; adiós a la métrica principal

**Files:**
- Modify: `agent_core/registry/evaluation/report.py`, `ports.py`, `evaluator.py`, `__init__.py` (contenidos completos)
- Modify: `agent_core/registry/evaluation/gate.py` (borrar `decide`), `scoring.py` (borrar `aggregate`)
- Modify: `agent_core/registry/suite.py` (borrar `Fraction`, `noise_margin`, `floor`)
- Modify: `agent_core/registry/service.py` (`evaluate` arma las varas), `agent_core/registry/__init__.py`
- Modify: `testing/registry_demo.py`, `tests/registry/helpers.py`, `tests/registry/service_world.py`, `tests/registry/test_evaluator.py`, `tests/registry/test_gate.py`, `tests/registry/test_scoring.py`, `tests/registry/test_service_decide.py`, `tests/registry/test_models.py`

- [ ] **Step 1: Pruebas del evaluador con la nueva firma**

Reemplazar `tests/registry/test_evaluator.py`:

```python
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import pytest

from agent_core.domain import EngineEvent, Outcome, RunClosed, RunClosedPayload
from agent_core.ports import ToolExecutor
from agent_core.registry.evaluation.evaluator import ScenarioEvaluator
from agent_core.registry.evaluation.local_sandbox import LocalSandbox
from agent_core.registry.evaluation.ports import EvalRequest, EvalTarget, HarnessUnavailable, SandboxHandle
from agent_core.registry.evaluation.report import EvalReport
from agent_core.registry.evaluation.scoring import PLATFORM_GUARDRAILS
from agent_core.registry.evaluation.yardstick import Yardstick
from agent_core.registry.snapshot import SnapshotRegistry
from agent_core.registry.suite import EvalSuite, SandboxSeed, Scenario
from testing.builders import NOW
from testing.fakes.ids import FakeIds
from tests.registry.eval_support import metric, thr
from tests.registry.helpers import demo_pinned, suite_content


def _closed(outcome: str) -> list[EngineEvent]:
    return [RunClosed(event_id="e", run_id="r", release="x", ts=NOW,
                      payload=RunClosedPayload(outcome=Outcome(outcome), closed_by="flow"))]


@dataclass
class FakeHarness:
    outcomes: dict[str, str]  # label -> outcome
    fail: bool = False
    seen: list[tuple[str, str, ToolExecutor]] = field(default_factory=list)

    def run(self, target: EvalTarget, agent_id: str, scenario: Scenario,
            tools: ToolExecutor) -> list[EngineEvent]:
        assert getattr(tools, "is_sandbox", False)
        self.seen.append((target.label, scenario.id, tools))
        if self.fail:
            raise HarnessUnavailable("gateway caído")
        return _closed(self.outcomes[target.label])


def _targets() -> tuple[EvalTarget, EvalTarget]:
    pinned = demo_pinned()
    reg = SnapshotRegistry(pinned.release, pinned.entities)
    return EvalTarget("candidate", pinned.release, reg), EvalTarget("base", pinned.release, reg)


def _suite(version: str = "1.0.0", **over: Any) -> EvalSuite:
    content = suite_content(version, **over)  # 1 escenario, 2 repeticiones, espera resolved
    # El harness falso solo emite RunClosed: no se exige una acción verificada.
    content["scenarios"][0]["expect"].pop("actions_verified")
    return EvalSuite.model_validate(content)


SUITE = _suite()
SAME = Yardstick(metrics=[], suite=SUITE)


def _request(*, base: bool = True, old: Yardstick = SAME, new: Yardstick = SAME) -> EvalRequest:
    cand, base_target = _targets()
    return EvalRequest(candidate=cand, new=new, base=base_target if base else None, old=old if base else None)


def _failed(report: EvalReport) -> list[str]:
    return sorted(i.metric_id for i in report.items if not i.passed)


def test_candidate_better_than_base_passes_and_shares_the_candidate_run() -> None:
    harness = FakeHarness({"candidate": "resolved", "base": "failed"})
    report = ScenarioEvaluator(harness, LocalSandbox(FakeIds())).run(_request())
    assert report.verdict == "pass"
    assert len(report.results) == 4 and {r.run for r in report.results} == {"base_on_old", "cand_on_new"}
    assert report.runs is not None and report.runs.cand_on_old == report.runs.cand_on_new


def test_candidate_worse_than_base_fails() -> None:
    report = ScenarioEvaluator(FakeHarness({"candidate": "failed", "base": "resolved"}),
                               LocalSandbox(FakeIds())).run(_request())
    assert report.verdict == "fail" and _failed(report) == ["scenario/resuelto"]


def test_each_run_gets_its_own_sandbox() -> None:  # T-REG-23
    harness = FakeHarness({"candidate": "resolved", "base": "resolved"})
    ScenarioEvaluator(harness, LocalSandbox(FakeIds()), max_workers=1).run(_request())
    assert len({id(tools) for _, _, tools in harness.seen}) == 4  # siguen referenciados en `seen`


def test_infra_failure_is_failed_infra() -> None:  # T-REG-11
    report = ScenarioEvaluator(FakeHarness({}, fail=True), LocalSandbox(FakeIds())).run(_request())
    assert report.verdict == "failed_infra" and report.items == [] and report.runs is None


class NotSandbox:
    def provision(self, seed: SandboxSeed, target: EvalTarget) -> SandboxHandle:
        return SandboxHandle("h")

    def tools(self, handle: SandboxHandle) -> ToolExecutor:
        return object()  # type: ignore[return-value]

    def teardown(self, handle: SandboxHandle) -> None:
        pass


def test_refuses_non_sandbox_tools() -> None:  # T-REG-24
    harness = FakeHarness({"candidate": "resolved", "base": "resolved"})
    with pytest.raises(PermissionError):
        ScenarioEvaluator(harness, NotSandbox()).run(_request())
    assert harness.seen == []


def test_without_base_only_the_new_yardstick_runs() -> None:
    report = ScenarioEvaluator(FakeHarness({"candidate": "resolved"}), LocalSandbox(FakeIds())).run(
        _request(base=False))
    assert report.verdict == "pass" and len(report.results) == 2
    assert report.runs is not None and report.runs.base_on_old is None


def test_infra_failure_stops_queued_jobs() -> None:
    harness = FakeHarness({}, fail=True)
    report = ScenarioEvaluator(harness, LocalSandbox(FakeIds()), max_workers=1).run(_request())
    assert report.verdict == "failed_infra" and len(harness.seen) == 1


def test_a_different_old_suite_gets_its_own_candidate_run() -> None:
    old = Yardstick(metrics=[], suite=_suite("0.9.0"))
    harness = FakeHarness({"candidate": "resolved", "base": "resolved"})
    report = ScenarioEvaluator(harness, LocalSandbox(FakeIds())).run(_request(old=old))
    assert report.verdict == "pass" and len(report.results) == 6
    assert {r.run for r in report.results} == {"base_on_old", "cand_on_old", "cand_on_new"}


def test_agent_metrics_are_computed_from_the_run_events() -> None:
    resolved = metric("resueltos", event="engine.run_closed",
                      where=[{"field": "outcome", "op": "eq", "value": "resolved"}])

    def verdict(floor: str) -> str:
        new = Yardstick(metrics=[resolved], suite=_suite(thresholds={"resueltos": thr("0", floor)}))
        report = ScenarioEvaluator(FakeHarness({"candidate": "resolved"}), LocalSandbox(FakeIds())).run(
            _request(base=False, new=new))
        item = next(i for i in report.items if i.metric_id == "resueltos")
        assert item.value == Decimal(2) and item.floor == Decimal(floor)  # 2 repeticiones resueltas
        return report.verdict

    assert verdict("2") == "pass" and verdict("3") == "fail"


def test_platform_guardrails_are_measured_on_every_run() -> None:
    report = ScenarioEvaluator(FakeHarness({"candidate": "resolved", "base": "resolved"}),
                               LocalSandbox(FakeIds())).run(_request())
    assert report.runs is not None and report.runs.base_on_old is not None
    for measured in (report.runs.base_on_old, report.runs.cand_on_new):
        assert all(measured.metrics[k] == Decimal(0) for k in PLATFORM_GUARDRAILS)
```

- [ ] **Step 2: Verificar que fallan**

Run: `uv run pytest tests/registry/test_evaluator.py -q`
Expected: `ImportError: cannot import name 'EvalRequest'`.

- [ ] **Step 3: `report.py` unificado**

Reemplazar `agent_core/registry/evaluation/report.py`:

```python
"""Resultado de una evaluación (registry §6.4; spec de evaluación §6). Cifras en `Decimal`; sin puntaje
compuesto."""

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
    """Un elemento del veredicto: una métrica, un escenario (`scenario/<id>`) o un guardarraíl de plataforma.

    `noise_margin` es la tolerancia frente a la base (vara vieja); `floor`, el piso (vara nueva y
    plataforma: un mínimo si mayor es mejor y un máximo si menor es mejor). No hay puntaje compuesto."""

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
    """Lo medido al correr una suite sobre una release: valor por métrica (incluidos los `platform_*`) y si
    cada escenario pasó. Una clave ausente es «sin medir», y el gate la cuenta como fallo."""

    status: Literal["ok", "failed_infra"] = "ok"
    metrics: dict[str, Decimal] = Field(default_factory=dict)
    scenarios: dict[str, bool] = Field(default_factory=dict)


class GateRuns(_M):
    """Las corridas del gate. Las dos de la vara vieja existen solo si la base registró su suite."""

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
    """Lo que ve quien aprueba: veredicto, cada elemento del gate por separado, las mediciones, el
    resultado de cada corrida, las notas del juez (informativas) y lo que la propuesta afloja de la vara."""

    verdict: Verdict
    items: list[GateItem] = Field(default_factory=list)
    runs: GateRuns | None = None
    results: list[ScenarioResult] = Field(default_factory=list)
    judge_notes: list[JudgeNote] = Field(default_factory=list)
    yardstick_changes: list[YardstickChange] = Field(default_factory=list)
    detail: str | None = None
```

- [ ] **Step 4: `ports.py` con `EvalRequest`**

En `agent_core/registry/evaluation/ports.py`: añadir `from agent_core.registry.evaluation.yardstick import Yardstick`, reemplazar el docstring del módulo por `"""Puertos de la evaluación (registry §6, §7.3; ADR 0020). `ScenarioHarness` lo implementa `composition`."""` y reemplazar `EvalPort` por:

```python
@dataclass(frozen=True)
class EvalRequest:
    """Lo que mide una evaluación con doble vara (spec de evaluación §6.1).

    `new` es la vara de la candidata (sus métricas y la suite elegida). `old` es la de la release base tal
    como se publicó; con `old.suite = None` (la base no registró suite, D3) solo se aplican los pisos de
    `new`."""

    candidate: EvalTarget
    new: Yardstick
    base: EvalTarget | None = None
    old: Yardstick | None = None


class EvalPort(Protocol):
    def run(self, request: EvalRequest) -> EvalReport: ...
```

- [ ] **Step 5: `evaluator.py` con hasta tres mediciones**

Reemplazar `agent_core/registry/evaluation/evaluator.py`:

```python
"""`ScenarioEvaluator` (registry §6.2; spec de evaluación §6.1): corre las varas en sandbox y decide el gate.

Corre la vara nueva sobre la candidata y, si la base registró su suite, la vara vieja sobre la base y sobre la
candidata; si la suite vieja es igual a la nueva, la candidata se corre una vez y esa corrida se mide con las
definiciones de cada vara. Cada escenario se corre `repetitions_of` veces, cada una en su propio sandbox."""

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass

from agent_core.domain import EngineEvent, MetricDef
from agent_core.registry.evaluation.gate import evaluate_gate
from agent_core.registry.evaluation.ports import (
    EvalRequest,
    EvalTarget,
    HarnessUnavailable,
    Judge,
    SandboxPort,
    ScenarioHarness,
    ScenarioTranscript,
)
from agent_core.registry.evaluation.report import (
    EvalReport,
    GateRuns,
    JudgeNote,
    RunName,
    ScenarioResult,
    SuiteMeasurement,
)
from agent_core.registry.evaluation.scoring import measure, score_run
from agent_core.registry.evaluation.yardstick import Yardstick
from agent_core.registry.suite import EvalSuite, Scenario

Plan = list[tuple[RunName, EvalTarget, EvalSuite]]


@dataclass(frozen=True)
class _Job:
    run: RunName
    target: EvalTarget
    suite: EvalSuite
    scenario: Scenario
    repetition: int


def _old_yardstick(request: EvalRequest) -> Yardstick | None:
    """La vara vieja que se puede correr: hace falta base y que la base haya registrado su suite."""
    old = request.old
    if request.base is None or old is None or old.suite is None:
        return None
    return old


class ScenarioEvaluator:
    def __init__(self, harness: ScenarioHarness, sandbox: SandboxPort, *, max_workers: int = 4,
                 judge: Judge | None = None) -> None:
        self._harness, self._sandbox = harness, sandbox
        self._max_workers, self._judge = max_workers, judge

    def _one(self, job: _Job) -> tuple[ScenarioResult, list[EngineEvent]]:
        handle = self._sandbox.provision(job.scenario.seed, job.target)
        try:
            tools = self._sandbox.tools(handle)
            if getattr(tools, "is_sandbox", False) is not True:
                raise PermissionError("el evaluador solo corre contra un sandbox")
            events = self._harness.run(job.target, job.suite.agent_id, job.scenario, tools)
        finally:
            self._sandbox.teardown(handle)
        score = score_run(events, job.scenario.expect, job.scenario.sensitive_values, job.scenario.assertions)
        return ScenarioResult(scenario_id=job.scenario.id, label=job.target.label, run=job.run,
                              repetition=job.repetition, score=score), events

    @staticmethod
    def _plan(request: EvalRequest, new_suite: EvalSuite) -> tuple[Plan, bool]:
        plan: Plan = [("cand_on_new", request.candidate, new_suite)]
        old = _old_yardstick(request)
        shared = old is not None and old.suite == new_suite
        if old is not None and old.suite is not None and request.base is not None:
            plan.append(("base_on_old", request.base, old.suite))
            if not shared:
                plan.append(("cand_on_old", request.candidate, old.suite))
        return plan, shared

    def run(self, request: EvalRequest) -> EvalReport:
        new_suite = request.new.suite
        if new_suite is None:
            raise ValueError("la vara nueva necesita su suite")
        plan, shared = self._plan(request, new_suite)
        jobs = [_Job(name, target, suite, scenario, r) for name, target, suite in plan
                for scenario in suite.scripted() for r in range(suite.repetitions_of(scenario))]
        pool = ThreadPoolExecutor(max_workers=self._max_workers)
        try:
            futures = [pool.submit(self._one, job) for job in jobs]
            try:
                for future in as_completed(futures):
                    future.result()
            except (HarnessUnavailable, TimeoutError) as exc:
                pool.shutdown(wait=True, cancel_futures=True)
                return EvalReport(verdict="failed_infra", detail=type(exc).__name__ + ": " + str(exc)[:200])
            done = [f.result() for f in futures]  # orden de los trabajos, no de finalización
        finally:
            pool.shutdown(wait=True, cancel_futures=True)

        def measured(name: RunName, suite: EvalSuite, metrics: list[MetricDef]) -> SuiteMeasurement:
            return measure(suite, metrics, [(r.scenario_id, r.score, ev) for r, ev in done if r.run == name])

        cand_on_new = measured("cand_on_new", new_suite, request.new.metrics)
        runs = GateRuns(cand_on_new=cand_on_new)
        old = _old_yardstick(request)
        if old is not None and old.suite is not None:
            cand_run: RunName = "cand_on_new" if shared else "cand_on_old"
            runs = GateRuns(base_on_old=measured("base_on_old", old.suite, old.metrics),
                            cand_on_old=measured(cand_run, old.suite, old.metrics), cand_on_new=cand_on_new)
        verdict, items = evaluate_gate(old, request.new, runs)
        notes: list[JudgeNote] = []
        if self._judge is not None:
            transcripts = [ScenarioTranscript(r.scenario_id, r.label, r.repetition, ev) for r, ev in done]
            notes = self._judge.score(new_suite, transcripts)
        return EvalReport(verdict=verdict, items=items, runs=runs, results=[r for r, _ in done],
                          judge_notes=notes)
```

- [ ] **Step 6: Borrar la métrica principal**

- `gate.py`: borrar `decide`; el import de `report` queda `GateItem, GateRuns, SuiteMeasurement, Verdict` y el de `scoring`, `PLATFORM_GUARDRAILS`. Quitar la línea del docstring que menciona `decide`.
- `scoring.py`: borrar `aggregate`, `_QUANT`, `ROUND_HALF_EVEN` y `SuiteMetrics` de los imports.
- `suite.py`: borrar `Fraction` y los campos `noise_margin` y `floor` de `EvalSuite` (y `Decimal` deja de importarse si ya no se usa: lo usan `NonNegativeDecimal` y `FiniteDecimal`, así que se queda).
- `tests/registry/test_gate.py`: borrar la sección "gate de la métrica principal de `main`" completa y los imports `decide`, `SuiteMetrics`, `EvalSuite` y `suite_content`.
- `tests/registry/test_scoring.py`: borrar `test_aggregate_primary_is_decimal_fraction` y `aggregate` del import.
- `tests/registry/helpers.py`: en `suite_content`, borrar `"noise_margin": "0.05", "floor": "0.5",`.
- `testing/registry_demo.py`: en `demo_suite`, borrar `"noise_margin": "0", "floor": "1",`.
- `tests/registry/test_models.py`: reemplazar `test_suite_parses_with_decimal_thresholds` y el parametrizado por:

```python
def test_suite_parses_with_decimal_thresholds() -> None:
    content = suite_content(thresholds={"m": {"noise_margin": "0.05", "floor": "0.5"}})
    threshold = EvalSuite.model_validate(content).thresholds["m"]
    assert (threshold.noise_margin, threshold.floor) == (Decimal("0.05"), Decimal("0.5"))
    assert EvalSuite.model_validate(content).scenarios[0].steps[0].op == "start"


@pytest.mark.parametrize("over", [
    {"scenarios": []},
    {"repetitions": 0},
    {"noise_margin": "0.05"},  # la métrica principal ya no existe (ADR 0020)
    {"thresholds": {"m": {"noise_margin": "-1"}}},
    {"scenarios": [suite_content()["scenarios"][0], suite_content()["scenarios"][0]]},  # ids repetidos
])
def test_suite_rejects_bad_shapes(over: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        EvalSuite.model_validate(suite_content(**over))
```

- [ ] **Step 7: `FakeEvaluator` y `_report` con la nueva firma**

Reemplazar en `tests/registry/service_world.py` los imports de `evaluation` y `FakeEvaluator` (y borrar `ZERO` y el import de `PLATFORM_GUARDRAILS`):

```python
from agent_core.registry.evaluation.ports import EvalRequest
from agent_core.registry.evaluation.report import EvalReport
```

```python
@dataclass
class FakeEvaluator:
    """`EvalPort` guionado: devuelve los reportes en orden (por defecto, `pass`) y guarda cada pedido."""
    reports: list[EvalReport] = field(default_factory=list)
    calls: list[tuple[str, str | None]] = field(default_factory=list)
    requests: list[EvalRequest] = field(default_factory=list)

    def run(self, request: EvalRequest) -> EvalReport:
        self.requests.append(request)
        self.calls.append((request.candidate.release.id, request.base.release.id if request.base else None))
        if self.reports:
            return self.reports.pop(0)
        return EvalReport(verdict="pass")
```

Borrar también el import de `EvalSuite` si quedó sin uso. En `tests/registry/test_service_decide.py`: el import pasa a `from agent_core.registry.evaluation.report import EvalReport`, se borran `from decimal import Decimal` y `ZERO` del import de `service_world`, y:

```python
def _report(verdict: str) -> EvalReport:
    return EvalReport(verdict=verdict)  # type: ignore[arg-type]
```

- [ ] **Step 8: El servicio arma las varas**

En `agent_core/registry/service.py`:

- imports: `from agent_core.domain import Agent, EntityKind, MetricDef, Principal, RegistryEntity, Release, loads`; `from agent_core.registry.evaluation.ports import EvalPort, EvalRequest, EvalTarget`; `from agent_core.registry.evaluation.yardstick import Yardstick`.
- función de módulo, después de `_violations_payload`:

```python
def _agent_metrics(entities: Sequence[RegistryEntity], agent_id: str) -> list[MetricDef]:
    """Las métricas del agente en un conjunto de entidades (candidata o base); [] si no está."""
    for entity in entities:
        if isinstance(entity, Agent) and entity.id == agent_id:
            return list(entity.metrics)
    return []
```

- en `evaluate`, reemplazar desde `candidate_target = ...` hasta `report = self._evaluator.run(...)` por:

```python
        candidate_target = EvalTarget("candidate", cand.release,
                                      SnapshotRegistry(cand.release, cand.entities))
        base_target = (EvalTarget("base", base_release, SnapshotRegistry(base_release, base_entities))
                       if base_release is not None else None)
        # La suite de la base llega en la tarea 6; mientras, la vara vieja son solo sus métricas (D3).
        old = (Yardstick(metrics=_agent_metrics(base_entities, p.agent_id))
               if base_release is not None else None)
        new = Yardstick(metrics=_agent_metrics(cand.entities, cand.agent_id), suite=suite)
        report = self._evaluator.run(EvalRequest(candidate=candidate_target, new=new, base=base_target,
                                                 old=old))  # fuera de la transacción
```

- [ ] **Step 9: Exportaciones**

Reemplazar `agent_core/registry/evaluation/__init__.py`:

```python
"""Evaluación por agente sobre escenarios, con doble vara (registry §6; ADR 0020)."""

from agent_core.registry.evaluation.evaluator import ScenarioEvaluator
from agent_core.registry.evaluation.gate import evaluate_gate
from agent_core.registry.evaluation.local_sandbox import LocalSandbox
from agent_core.registry.evaluation.ports import (
    EvalPort,
    EvalRequest,
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
    GateItem,
    GateRuns,
    JudgeNote,
    RunScore,
    ScenarioResult,
    SuiteMeasurement,
    Verdict,
)
from agent_core.registry.evaluation.scoring import PLATFORM_GUARDRAILS
from agent_core.registry.evaluation.yardstick import (
    Yardstick,
    YardstickChange,
    YardstickChangeKind,
    classify_yardstick_change,
)

__all__ = [
    "PLATFORM_GUARDRAILS", "EvalPort", "EvalReport", "EvalRequest", "EvalTarget", "GateItem", "GateRuns",
    "HarnessUnavailable", "Judge", "JudgeNote", "LocalSandbox", "RunScore", "SandboxHandle", "SandboxPort",
    "ScenarioEvaluator", "ScenarioHarness", "ScenarioResult", "ScenarioTranscript", "SuiteMeasurement",
    "Verdict", "Yardstick", "YardstickChange", "YardstickChangeKind", "classify_yardstick_change",
    "evaluate_gate",
]
```

En `agent_core/registry/__init__.py`, añadir al import de `agent_core.registry.evaluation`: `PLATFORM_GUARDRAILS`, `EvalRequest`, `GateItem`, `Yardstick`, `YardstickChange`; y a `__all__`: `"PLATFORM_GUARDRAILS"`, `"EvalRequest"`, `"GateItem"`, `"Yardstick"`, `"YardstickChange"`.

- [ ] **Step 10: Verificar que no queda ningún duplicado ni la métrica principal**

```bash
uv run pytest tests/registry tests/composition -q
uv run mypy
uv run ruff check .
uv run lint-imports
uv run agentcore contracts --check
```
Expected: todo en verde; `contracts --check` sin diferencias (D12). Con Grep sobre `agent_core`, `testing` y `tests`: `SuiteMetrics|MetricCheck|aggregate\(|GUARDRAILS =|\.primary|noise_margin: Fraction|def decide` → 0 resultados; `class EvalSuite|class EvalReport|^Verdict =` → exactamente 1 cada uno.

- [ ] **Step 11: Commit**

```bash
git add -A agent_core/registry testing/registry_demo.py tests/registry
git commit -m "feat(registry): EvalPort con doble vara y un solo EvalReport; se retira la metrica principal (ADR 0020 enmienda ADR 0018)" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 6: `releases.eval_suite_refs` (memoria y Postgres) y la vara vieja desde la base

**Files:**
- Modify: `agent_core/registry/models.py`, `agent_core/registry/service.py`, `agent_core/registry/postgres/schema.sql`, `agent_core/registry/postgres/store.py`
- Modify: `tests/registry/service_world.py` (helper `publish_cycle`)
- Create: `tests/registry/test_service_yardstick.py`
- Modify: `tests/integration/test_registry_postgres.py` (+2 pruebas)

- [ ] **Step 1: Helper de ciclo completo**

En `tests/registry/service_world.py` añadir los imports `from agent_core.registry.models import EntityDraft, Origin` (junto al import existente de `models`) y `from tests.registry.helpers import AGENT, demo_pinned, human, suite_draft` (ampliando el existente), y al final:

```python
ANA = human()


def publish_cycle(w: World, drafts: list[EntityDraft], *, key: str = "k") -> str:
    """Ciclo completo de una persona: crear, borrador, congelar, evaluar con `disputas-suite`, aprobar y
    publicar. Devuelve la release publicada."""
    p = w.service.create_proposal(ANA, AGENT, Origin.manual, "cambio")
    w.service.put_draft(ANA, p.proposal_id, drafts, expected_rev=0)
    w.service.freeze(ANA, p.proposal_id)
    w.service.evaluate(ANA, p.proposal_id, "disputas-suite")
    h = w.service.get_proposal(p.proposal_id).proposal.candidate_hash or ""
    w.service.approve(ANA, p.proposal_id, h)
    return w.service.publish(ANA, p.proposal_id, key).release_id
```

- [ ] **Step 2: Escribir las pruebas**

Crear `tests/registry/test_service_yardstick.py`:

```python
"""Doble vara en el servicio del registry (ADR 0020): vara de la base, suite, aflojamiento y aprobación."""

import json
import shutil
from pathlib import Path

from agent_core.registry.memory import InMemoryRegistryStore
from agent_core.registry.models import VersionRef
from agent_core.registry.service import RegistryService
from testing.fakes.clock import FakeClock
from testing.fakes.ids import FakeIds
from tests.registry.helpers import REGISTRY_DEMO, admin, prompt_draft, suite_content, suite_draft
from tests.registry.service_world import FakeEvaluator, World, publish_cycle

SUITE_REF = VersionRef(kind="eval_suite", id="disputas-suite", version="1.0.0")


def test_publish_records_the_suite_used_by_the_gate() -> None:
    w = World()
    release_id = publish_cycle(w, [prompt_draft(), suite_draft()])
    assert w.service.get_release(release_id).eval_suite_refs == [SUITE_REF]
    assert w.service.get_release("rel-demo").eval_suite_refs == []


def test_a_base_without_recorded_suite_gives_a_metrics_only_old_yardstick() -> None:  # decisión D3
    w = World()
    publish_cycle(w, [prompt_draft(), suite_draft()])
    first = w.evaluator.requests[0]
    assert first.base is not None and first.base.release.id == "rel-demo"
    assert first.old is not None and first.old.suite is None and first.old.metrics == []
    assert first.new.suite is not None and first.new.suite.version == "1.0.0"


def test_the_next_proposal_is_measured_with_the_suite_of_its_base() -> None:
    w = World()
    publish_cycle(w, [prompt_draft(), suite_draft()])
    publish_cycle(w, [prompt_draft(version="1.2.0", text="Otra variante."), suite_draft("1.1.0")], key="k2")
    second = w.evaluator.requests[-1]
    assert second.old is not None and second.old.suite is not None and second.new.suite is not None
    assert (second.old.suite.version, second.new.suite.version) == ("1.0.0", "1.1.0")


def test_import_records_the_seed_suite(tmp_path: Path) -> None:
    root = tmp_path / "seed"
    shutil.copytree(REGISTRY_DEMO, root)
    (root / "eval_suites").mkdir()
    (root / "eval_suites" / "disputas-suite@1.0.0.yaml").write_text(json.dumps(suite_content()),
                                                                   encoding="utf-8")  # JSON es YAML válido
    service = RegistryService(InMemoryRegistryStore(), FakeEvaluator(), FakeClock(), FakeIds())
    [detail] = service.import_seed(admin(), root)
    assert detail.eval_suite_refs == [SUITE_REF]
```

- [ ] **Step 3: Verificar que fallan**

Run: `uv run pytest tests/registry/test_service_yardstick.py -q`
Expected: 4 failed (`AttributeError: 'ReleaseDetail' object has no attribute 'eval_suite_refs'` y la suite vieja ausente en el segundo pedido).

- [ ] **Step 4: Modelos**

En `agent_core/registry/models.py`, añadir al final de `StoredRelease` y de `ReleaseDetail`:

```python
    eval_suite_refs: list[VersionRef] = Field(default_factory=list)  # ADR 0020: suites del gate
```

(el motor no lee este campo; en `ReleaseDetail` es la vara de la siguiente propuesta).

- [ ] **Step 5: Servicio**

En `agent_core/registry/service.py`:

1. Nuevo método, junto a `_base`:

```python
    def _base_suite(self, tx: RegistryTx, release_id: str, agent_id: str) -> EvalSuite | None:
        """La suite con que la release base pasó el gate (ADR 0020 §5); None si no registró ninguna (D3)."""
        stored = tx.get_release(release_id)
        if stored is None:
            raise RegistryError(RegistryErrorCode.not_found, "la release base no existe")
        if not stored.eval_suite_refs:
            return None
        if len(stored.eval_suite_refs) > 1:
            raise IntegrityError(f"la release {release_id} registra {len(stored.eval_suite_refs)} suites")
        suite = self._load(tx, stored.eval_suite_refs[0])
        if not isinstance(suite, EvalSuite) or suite.agent_id != agent_id:
            raise IntegrityError(f"la suite registrada por {release_id} no es del agente {agent_id}")
        return suite
```

2. En `evaluate`, dentro de la primera transacción, después de `base_release, base_entities = self._base(...)`:

```python
            old: Yardstick | None = None
            if p.base_release_id is not None:
                old = Yardstick(metrics=_agent_metrics(base_entities, p.agent_id),
                                suite=self._base_suite(tx, p.base_release_id, p.agent_id))
```

y borrar la línea `old = Yardstick(...)` (y su comentario) que dejó la tarea 5.

3. En `_publish_in_tx`, después de comprobar la aprobación:

```python
        run = tx.latest_eval_run(p.proposal_id, cand.candidate_hash)
        if run is None or run.verdict != "pass":
            raise RegistryError(RegistryErrorCode.gate_failed, "no hay una evaluación aprobada vigente")
```

y en `StoredRelease(...)` de `_publish_in_tx` añadir `eval_suite_refs=[run.suite]`. La versión de la suite ya se insertó arriba si era nueva (Review Focus 5).

4. En `import_seed`, reemplazar el bucle de suites por:

```python
            suite_refs: dict[str, list[VersionRef]] = {}
            for suite in suites:
                ref = self._insert_if_new(tx, suite, seed_docs, None, who, now)
                suite_refs.setdefault(suite.agent_id, []).append(ref)
```

y, dentro del bucle de `pinned_list`, antes de `tx.insert_release`:

```python
                agent_suites = suite_refs.get(agent_id, [])
                if len(agent_suites) > 1:
                    raise RegistryError(RegistryErrorCode.validation_failed,
                                        f"la semilla trae {len(agent_suites)} suites para {agent_id}; "
                                        "se admite una por agente")
```

y `eval_suite_refs=agent_suites` en ese `StoredRelease(...)`.

5. En `_detail`, `ReleaseDetail(..., eval_suite_refs=list(stored.eval_suite_refs))`.

- [ ] **Step 6: Postgres**

En `agent_core/registry/postgres/schema.sql`, después de `reg_release_entities`:

```sql
-- ADR 0020: la suite con que la release pasó el gate (vara vieja de la siguiente propuesta). Solo inserción.
CREATE TABLE IF NOT EXISTS reg_release_eval_suites (
    release_id text NOT NULL REFERENCES reg_releases(release_id),
    kind text NOT NULL DEFAULT 'eval_suite' CHECK (kind = 'eval_suite'),
    id text NOT NULL, version text NOT NULL,
    PRIMARY KEY (release_id, id),
    FOREIGN KEY (kind, id, version) REFERENCES reg_entity_versions(kind, id, version));
```

y añadir `'reg_release_eval_suites'` al arreglo del bloque `DO $$` de triggers.

En `agent_core/registry/postgres/store.py`: añadir `"reg_release_eval_suites"` a `_INSERT_ONLY`; en `insert_release`, después del `executemany` de `reg_release_entities`:

```python
        if s.eval_suite_refs:
            with self._c.cursor() as cur:
                cur.executemany("INSERT INTO reg_release_eval_suites (release_id, kind, id, version) "
                                "VALUES (%s, %s, %s, %s)",
                                [(s.release.id, r.kind, r.id, r.version) for r in s.eval_suite_refs])
```

y en `get_release`, antes del `return`:

```python
        suites = self._c.execute("SELECT kind, id, version FROM reg_release_eval_suites "
                                 "WHERE release_id = %s ORDER BY id", (release_id,)).fetchall()
```

con `eval_suite_refs=[_ref(*r) for r in suites]` en el `StoredRelease(...)` que devuelve.

- [ ] **Step 7: Pruebas de integración (Postgres)**

Añadir a `tests/integration/test_registry_postgres.py`:

```python
def test_release_records_its_eval_suite(registry_store: PgRegistryStore) -> None:  # ADR 0020 §5
    evaluator = FakeEvaluator()
    service = RegistryService(registry_store, evaluator, FakeClock(), FakeIds())
    service.import_seed(admin(), REGISTRY_DEMO)
    rel = _publish(service)
    assert [str(r) for r in service.get_release(rel).eval_suite_refs] == ["eval_suite:disputas-suite@1.0.0"]
    _publish(service, "k2", "1.2.0")
    old = evaluator.requests[-1].old
    assert old is not None and old.suite is not None and old.suite.version == "1.0.0"


def test_release_eval_suites_are_insert_only(registry_store: PgRegistryStore) -> None:  # T-REG-01
    service = _service(registry_store)
    service.import_seed(admin(), REGISTRY_DEMO)
    _publish(service)
    with registry_store.connect() as conn, pytest.raises(psycopg.errors.InsufficientPrivilege):
        conn.execute("DELETE FROM reg_release_eval_suites")
```

- [ ] **Step 8: Verificar**

```bash
uv run pytest tests/registry tests/composition -q
uv run mypy
uv run ruff check .
docker compose up -d postgres
uv run pytest tests/integration/test_registry_postgres.py -q
```
Expected: todo en verde. Sin Docker, las de integración se omiten (`skipped`): el dueño las corre (ver tarea 12).

- [ ] **Step 9: Commit**

```bash
git add agent_core/registry/models.py agent_core/registry/service.py agent_core/registry/postgres tests/registry/service_world.py tests/registry/test_service_yardstick.py tests/integration/test_registry_postgres.py
git commit -m "feat(registry): releases guardan la suite del gate y la vara vieja sale de la base (ADR 0020)" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 7: `suite_problems` al validar y al evaluar; el reporte trae el aflojamiento

**Files:**
- Modify: `agent_core/registry/validation.py`, `agent_core/registry/service.py`
- Modify: `tests/registry/service_world.py` (helpers `agent_with_metrics`, `loosening_evaluated`)
- Modify: `tests/registry/test_service_yardstick.py`, `tests/registry/test_validation.py`

- [ ] **Step 1: Helpers**

En `tests/registry/service_world.py`, añadir `from agent_core.domain import EntityKind, MetricDef` (ampliando el existente) y `docs, prompt_draft` al import de `helpers`, y al final:

```python
def agent_with_metrics(w: World, version: str, *metrics: MetricDef) -> EntityDraft:
    """El agente vigente con otra versión y `metrics` (borrador sintético)."""
    content = dict(w.service.get_entity("agent", AGENT).content)
    content["version"] = version
    content["metrics"] = [m.model_dump(mode="json") for m in metrics]
    return EntityDraft(kind="agent", content=content, docs=docs("métricas del agente"))


def loosening_evaluated(w: World) -> tuple[str, str]:
    """Publica `disputas-suite@1.0.0` (2 repeticiones) y deja evaluada una propuesta cuya suite 1.1.0 baja a 1
    repetición (`repetitions_lowered`). Devuelve (propuesta, candidate_hash)."""
    publish_cycle(w, [prompt_draft(), suite_draft()])
    p = w.service.create_proposal(ANA, AGENT, Origin.manual, "afloja la vara")
    w.service.put_draft(ANA, p.proposal_id, [prompt_draft(version="1.2.0", text="Otra variante."),
                                              suite_draft("1.1.0", repetitions=1)], expected_rev=0)
    w.service.freeze(ANA, p.proposal_id)
    w.service.evaluate(ANA, p.proposal_id, "disputas-suite")
    return p.proposal_id, w.service.get_proposal(p.proposal_id).proposal.candidate_hash or ""
```

- [ ] **Step 2: Escribir las pruebas**

Añadir a `tests/registry/test_service_yardstick.py` (imports nuevos: `import pytest`, `from agent_core.registry.errors import RegistryError, RegistryErrorCode`, `from agent_core.registry.models import Origin`, `from tests.registry.eval_support import metric`, y `ANA, agent_with_metrics, loosening_evaluated` desde `service_world`):

```python
def test_validate_reports_problems_of_a_drafted_suite() -> None:  # T-EVAL-11
    w = World()
    p = w.service.create_proposal(ANA, AGENT, Origin.manual, "métrica sin umbral")
    w.service.put_draft(ANA, p.proposal_id, [agent_with_metrics(w, "1.1.0", metric("m_gate")), suite_draft()],
                        expected_rev=0)
    report = w.service.validate(ANA, p.proposal_id)
    found = {(v.rule, v.message.split(":")[0]) for v in report.violations}
    assert ("REG-SUITE", "missing_threshold") in found


def test_evaluate_rejects_a_published_suite_with_problems() -> None:  # T-EVAL-11
    w = World()
    publish_cycle(w, [prompt_draft(), suite_draft()])
    p = w.service.create_proposal(ANA, AGENT, Origin.manual, "métrica nueva sin umbral")
    drafts = [agent_with_metrics(w, "1.1.0", metric("m_gate"))]
    w.service.put_draft(ANA, p.proposal_id, drafts, expected_rev=0)
    w.service.freeze(ANA, p.proposal_id)
    calls = len(w.evaluator.requests)
    with pytest.raises(RegistryError) as info:
        w.service.evaluate(ANA, p.proposal_id, "disputas-suite")
    assert info.value.code is RegistryErrorCode.validation_failed
    assert info.value.payload[0]["message"].startswith("missing_threshold")  # type: ignore[index,call-overload]
    assert len(w.evaluator.requests) == calls  # no se evaluó nada


def test_the_report_carries_the_loosening() -> None:  # T-EVAL-08 en el servicio
    w = World()
    pid, _ = loosening_evaluated(w)
    last = w.service.get_proposal(pid).last_eval
    assert last is not None and [c.kind for c in last.report.yardstick_changes] == ["repetitions_lowered"]


def test_tightening_carries_no_loosening() -> None:
    w = World()
    publish_cycle(w, [prompt_draft(), suite_draft()])
    p = w.service.create_proposal(ANA, AGENT, Origin.manual, "endurece")
    w.service.put_draft(ANA, p.proposal_id, [prompt_draft(version="1.2.0", text="Otra variante."),
                                              suite_draft("1.1.0", repetitions=3)], expected_rev=0)
    w.service.freeze(ANA, p.proposal_id)
    assert w.service.evaluate(ANA, p.proposal_id, "disputas-suite").yardstick_changes == []
```

y `AGENT` al import de `helpers`. En `tests/registry/test_validation.py`:

```python
def test_drafted_suite_problems_are_reg_suite_violations() -> None:  # spec de evaluación §5
    pinned, cand = _cand(prompt_draft(), suite_draft(thresholds={"fantasma": {"noise_margin": "0"}}))
    out = validate_candidate(cand, base_versions=_base_versions(pinned), drafted=set())
    assert [(v.rule, v.message.split(":")[0]) for v in out] == [("REG-SUITE", "unknown_threshold_metric")]
```

- [ ] **Step 3: Verificar que fallan**

Run: `uv run pytest tests/registry/test_service_yardstick.py tests/registry/test_validation.py -q`
Expected: las 5 nuevas fallan (sin violaciones `REG-SUITE` por umbrales, `evaluate` no rechaza, `yardstick_changes == []`); `test_tightening_carries_no_loosening` pasa ya.

- [ ] **Step 4: `validation.py`**

Imports: `from agent_core.domain import Agent, Flow, canonical_bytes` y `from agent_core.registry.suite import EvalSuite, SuiteProblem, suite_problems`. Nueva función:

```python
def suite_violations(suite: EvalSuite, problems: Sequence[SuiteProblem]) -> list[Violation]:
    """Los problemas de una suite como violaciones `REG-SUITE` (el código va al inicio del mensaje)."""
    return [Violation(rule="REG-SUITE", path=f"eval_suite:{suite.id}{p.path}",
                      message=f"{p.code.value}: {p.message}") for p in problems]
```

En `validate_candidate`, reemplazar el bucle final de suites por:

```python
    agent = next((e for e in c.entities if isinstance(e, Agent) and e.id == c.agent_id), None)
    for suite in c.suites:
        if agent is not None:  # sin el agente la candidata ya falló con REG-AGENT
            out.extend(suite_violations(suite, suite_problems(agent, suite)))
    return out
```

(`agent_mismatch` sigue saliendo como `REG-SUITE`: `test_suite_of_other_agent_is_violation` no cambia.)

- [ ] **Step 5: `service.evaluate`**

Imports: `from agent_core.registry.evaluation.yardstick import Yardstick, classify_yardstick_change`, `from agent_core.registry.suite import EvalSuite, suite_problems`, `from agent_core.registry.validation import (DEFAULT_LIMITS, Limits, check_draft_limits, suite_violations, validate_candidate)`. En `evaluate`, dentro de la primera transacción, después de `suite = self._suite(...)`:

```python
            agent = next((e for e in cand.entities if isinstance(e, Agent) and e.id == cand.agent_id), None)
            problems = suite_problems(agent, suite) if agent is not None else []
            if problems:
                raise RegistryError(RegistryErrorCode.validation_failed,
                                    f"la suite {suite.id} tiene {len(problems)} problemas",
                                    payload=_violations_payload(suite_violations(suite, problems)))  # type: ignore[arg-type]
```

y, después de `report = self._evaluator.run(...)`:

```python
        report = report.model_copy(update={"yardstick_changes": classify_yardstick_change(old, new)})
```

- [ ] **Step 6: Verificar**

```bash
uv run pytest tests/registry tests/composition -q
uv run mypy
uv run ruff check .
```
Expected: todo en verde.

- [ ] **Step 7: Commit**

```bash
git add agent_core/registry/validation.py agent_core/registry/service.py tests/registry/service_world.py tests/registry/test_service_yardstick.py tests/registry/test_validation.py
git commit -m "feat(registry): suite_problems al validar y evaluar, y clasificacion yardstick_loosened en el reporte (ADR 0020)" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 8: El aflojamiento se aprueba aparte; la aprobación muestra tres elementos

**Files:**
- Modify: `agent_core/registry/errors.py`, `models.py`, `service.py`, `http.py`, `postgres/schema.sql`, `postgres/store.py`, `agent_core/composition/registry.py`
- Modify: `tests/registry/test_service_yardstick.py`, `tests/registry/test_http.py`, `tests/composition/test_registry_cli.py`, `tests/integration/test_registry_postgres.py`

- [ ] **Step 1: Escribir las pruebas**

En `tests/registry/test_service_yardstick.py` (import nuevo: `from agent_core.registry.models import ProposalState`):

```python
def test_loosening_needs_its_own_approval() -> None:  # T-EVAL-17, decisión D7
    w = World()
    pid, h = loosening_evaluated(w)
    with pytest.raises(RegistryError) as info:
        w.service.approve(ANA, pid, h)
    assert info.value.code is RegistryErrorCode.loosening_not_accepted
    assert [c["kind"] for c in info.value.payload] == ["repetitions_lowered"]  # type: ignore[union-attr,index]
    assert w.service.get_proposal(pid).proposal.state is ProposalState.evaluated
    approval = w.service.approve(ANA, pid, h, accept_yardstick_loosened=True)
    assert [c.kind for c in approval.yardstick_loosened] == ["repetitions_lowered"]


def test_review_shows_change_suite_and_loosening_apart() -> None:  # T-EVAL-17
    w = World()
    pid, _ = loosening_evaluated(w)
    review = w.service.get_proposal(pid).review
    assert review is not None
    assert [d.kind for d in review.functional_changes] == ["prompt"]
    assert [d.version for d in review.suite_changes] == ["1.1.0"]
    assert str(review.suite) == "eval_suite:disputas-suite@1.1.0"
    assert [c.kind for c in review.yardstick_loosened] == ["repetitions_lowered"]
    assert review.gate == []  # el evaluador falso no mide; con ScenarioEvaluator llegan los elementos
```

En `tests/registry/test_http.py` (import: `from tests.registry.service_world import SUITE, World, loosening_evaluated`):

```python
def test_loosening_must_be_accepted_over_http() -> None:  # D7
    c, w = _client()
    pid, h = loosening_evaluated(w)
    r = c.post(f"/v1/registry/proposals/{pid}/approve", json={"candidate_hash": h}, headers=_h("ana"))
    assert r.status_code == 409 and r.json()["code"] == "loosening_not_accepted"
    ok = c.post(f"/v1/registry/proposals/{pid}/approve",
                json={"candidate_hash": h, "accept_yardstick_loosened": True}, headers=_h("ana"))
    assert ok.status_code == 200 and ok.json()["yardstick_loosened"][0]["kind"] == "repetitions_lowered"
```

En `tests/composition/test_registry_cli.py`:

```python
def test_approve_accepts_the_loosening_flag() -> None:  # D7
    parser = argparse.ArgumentParser()
    add_registry_parser(parser.add_subparsers(dest="command", required=True))
    args = parser.parse_args(["registry", "approve", "p-1", "h" * 64, "--accept-yardstick-loosened"])
    assert args.accept_yardstick_loosened is True
    assert parser.parse_args(["registry", "approve", "p-1", "h"]).accept_yardstick_loosened is False
```

En `tests/integration/test_registry_postgres.py` (import: `suite_draft` desde `tests.registry.helpers`):

```python
def test_approval_keeps_the_accepted_loosening(registry_store: PgRegistryStore) -> None:  # D7
    service = _service(registry_store)
    service.import_seed(admin(), REGISTRY_DEMO)
    _publish(service)
    p = service.create_proposal(ANA, AGENT, Origin.manual, "afloja")
    service.put_draft(ANA, p.proposal_id, [prompt_draft(version="1.2.0", text="Otra."),
                                            suite_draft("1.1.0", repetitions=1)], expected_rev=0)
    service.freeze(ANA, p.proposal_id)
    service.evaluate(ANA, p.proposal_id, "disputas-suite")
    h = service.get_proposal(p.proposal_id).proposal.candidate_hash or ""
    service.approve(ANA, p.proposal_id, h, accept_yardstick_loosened=True)
    with registry_store.transaction() as tx:
        approval = tx.latest_approval(p.proposal_id, h)
    assert approval is not None and [c.kind for c in approval.yardstick_loosened] == ["repetitions_lowered"]
```

- [ ] **Step 2: Verificar que fallan**

Run: `uv run pytest tests/registry/test_service_yardstick.py tests/registry/test_http.py tests/composition/test_registry_cli.py -q`
Expected: las nuevas fallan (`AttributeError: loosening_not_accepted`, `unexpected keyword argument 'accept_yardstick_loosened'`, `review` inexistente, opción de CLI desconocida).

- [ ] **Step 3: Código de error y modelo**

`agent_core/registry/errors.py`: en `RegistryErrorCode`, `loosening_not_accepted = "loosening_not_accepted"`; en `HTTP_STATUS`, `RegistryErrorCode.loosening_not_accepted: 409`.

`agent_core/registry/models.py`: importar `from agent_core.registry.evaluation.yardstick import YardstickChange` y añadir a `Approval`, antes de `at`:

```python
    yardstick_loosened: list[YardstickChange] = Field(default_factory=list)  # ADR 0020 §6.2: aprobado aparte
```

- [ ] **Step 4: Servicio**

En `agent_core/registry/service.py` (imports: `from agent_core.registry.entities import SUITE_KIND, ...`, `from agent_core.registry.evaluation.report import EvalReport, GateItem`, `from agent_core.registry.evaluation.yardstick import Yardstick, YardstickChange, classify_yardstick_change`):

```python
class ApprovalReview(_V):
    """Lo que ve quien aprueba, en tres elementos separados (spec de evaluación §8.5, T-EVAL-17): el cambio
    funcional, la suite con que se midió (y su borrador, si cambió) con cada elemento del gate, y lo que la
    propuesta afloja de la vara."""

    functional_changes: list[EntityDraft]
    suite: VersionRef
    suite_changes: list[EntityDraft]
    gate: list[GateItem]
    yardstick_loosened: list[YardstickChange]


class ProposalDetail(_V):
    proposal: Proposal
    changes: list[EntityDraft]
    last_eval: EvalRun | None
    review: ApprovalReview | None = None
```

`get_proposal`:

```python
    def get_proposal(self, proposal_id: str) -> ProposalDetail:
        with self._store.transaction() as tx:
            p = self._proposal(tx, proposal_id, for_update=False)
            last = tx.latest_eval_run(p.proposal_id, p.candidate_hash) if p.candidate_hash else None
            changes = tx.get_changes(proposal_id)
            review = None if last is None else ApprovalReview(
                functional_changes=[d for d in changes if d.kind != SUITE_KIND], suite=last.suite,
                suite_changes=[d for d in changes if d.kind == SUITE_KIND], gate=list(last.report.items),
                yardstick_loosened=list(last.report.yardstick_changes))
            return ProposalDetail(proposal=p, changes=changes, last_eval=last, review=review)
```

`approve`:

```python
    def approve(self, actor: Principal, proposal_id: str, candidate_hash: str, *,
                accept_yardstick_loosened: bool = False) -> Approval:
        require_approver(actor)
        with self._store.transaction() as tx:
            p = self._proposal(tx, proposal_id)
            self._expect(p, ProposalState.evaluated)
            if candidate_hash != p.candidate_hash:
                raise RegistryError(RegistryErrorCode.candidate_changed,
                                    "la candidata aprobada no es la vigente")
            run = tx.latest_eval_run(proposal_id, candidate_hash)
            if run is None or run.verdict != "pass":
                raise RegistryError(RegistryErrorCode.gate_failed, "no hay una evaluación aprobada vigente")
            loosened = list(run.report.yardstick_changes)
            if loosened and not accept_yardstick_loosened:
                raise RegistryError(RegistryErrorCode.loosening_not_accepted,
                                    "la propuesta afloja la vara: se aprueba aparte con "
                                    "accept_yardstick_loosened",
                                    payload=[c.model_dump(mode="json") for c in loosened])
            approval = Approval(proposal_id=proposal_id, candidate_hash=candidate_hash, actor=actor_id(actor),
                                decision="approved", yardstick_loosened=loosened, at=self._clock.now())
            tx.insert_approval(approval)
            p = self._save(tx, p, state=ProposalState.approved)
            self._event(tx, "approved", actor, p)
            return approval
```

- [ ] **Step 5: HTTP y CLI**

`agent_core/registry/http.py`:

```python
class _Approve(BaseModel):
    candidate_hash: str
    accept_yardstick_loosened: bool = False
```

y en la ruta `approve`: `service.approve(who(request, authorization), pid, body.candidate_hash, accept_yardstick_loosened=body.accept_yardstick_loosened)`.

`agent_core/composition/registry.py`: después de `ap.add_argument("candidate_hash")`:

```python
    ap.add_argument("--accept-yardstick-loosened", action="store_true",
                    help="aprueba además lo que la propuesta afloja de la vara (ADR 0020)")
```

y en `_dispatch`: `return s.approve(actor, a.proposal_id, a.candidate_hash, accept_yardstick_loosened=a.accept_yardstick_loosened)`.

- [ ] **Step 6: Postgres**

`schema.sql`, después de `CREATE TABLE IF NOT EXISTS reg_approvals (...)`:

```sql
-- ADR 0020 §6.2: lo que la aprobación aceptó aflojar (JSON). Aditivo e idempotente.
ALTER TABLE reg_approvals ADD COLUMN IF NOT EXISTS yardstick_loosened text NOT NULL DEFAULT '[]';
```

`store.py` (import `from agent_core.registry.evaluation.yardstick import YardstickChange`):

```python
    def insert_approval(self, a: Approval) -> None:
        self._c.execute("INSERT INTO reg_approvals (proposal_id, candidate_hash, actor, decision, "
                        "reason, at, yardstick_loosened) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                        (a.proposal_id, a.candidate_hash, a.actor, a.decision, a.reason, a.at,
                         dumps([c.model_dump(mode="json") for c in a.yardstick_loosened])))

    def latest_approval(self, proposal_id: str, candidate_hash: str) -> Approval | None:
        row = self._one("SELECT actor, decision, reason, at, yardstick_loosened FROM reg_approvals "
                        "WHERE proposal_id = %s AND candidate_hash = %s ORDER BY seq DESC LIMIT 1",
                        (proposal_id, candidate_hash))
        if row is None:
            return None
        loosened = loads(row[4])
        assert isinstance(loosened, list)
        return Approval(proposal_id=proposal_id, candidate_hash=candidate_hash, actor=row[0],
                        decision=row[1], reason=row[2], at=row[3],
                        yardstick_loosened=[YardstickChange.model_validate(c) for c in loosened])
```

- [ ] **Step 7: Verificar**

```bash
uv run pytest tests/registry tests/composition -q
uv run mypy
uv run ruff check .
uv run lint-imports
uv run pytest tests/integration/test_registry_postgres.py -q   # con docker compose up -d postgres
```
Expected: todo en verde (`test_every_code_has_http_status` cubre el código nuevo).

- [ ] **Step 8: Commit**

```bash
git add agent_core/registry agent_core/composition/registry.py tests/registry tests/composition/test_registry_cli.py tests/integration/test_registry_postgres.py
git commit -m "feat(registry): el aflojamiento de la vara se aprueba aparte y la aprobacion muestra cambio, suite y aflojamiento (ADR 0020)" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 9: Ninguna propuesta edita los guardarraíles de plataforma (`forbidden_role`, T-EVAL-10)

**Files:**
- Modify: `agent_core/registry/validation.py` (`platform_edits`), `agent_core/registry/service.py` (`put_draft`)
- Modify: `tests/registry/test_validation.py`, `tests/registry/test_service_yardstick.py`

- [ ] **Step 1: Escribir las pruebas**

`tests/registry/test_validation.py` (import: `from agent_core.registry.validation import ..., platform_edits`):

```python
def test_platform_edits_are_found_in_agents_and_suites() -> None:  # spec de evaluación §7
    agent = EntityDraft(kind="agent", content={"id": AGENT, "version": "1.1.0",
                                               "metrics": [{"id": "ok"}, {"id": "platform_pii_leak"}]},
                        docs=docs())
    suite = suite_draft("1.1.0", thresholds={"platform_unverified_write": {"noise_margin": "1"}})
    assert platform_edits([agent, suite, prompt_draft()]) == [
        "agent:atencion/metrics/1/id", "eval_suite:disputas-suite/thresholds/platform_unverified_write"]
```

`tests/registry/test_service_yardstick.py` (import: `from agent_core.registry.models import EntityDraft`; `docs` desde `helpers`):

```python
@pytest.mark.parametrize("draft", [
    EntityDraft(kind="agent", docs=docs(),
                content={"id": AGENT, "version": "1.1.0", "metrics": [{"id": "platform_pii_leak"}]}),
    suite_draft("1.1.0", thresholds={"platform_pii_leak": {"noise_margin": "1"}}),
])
def test_a_proposal_cannot_edit_platform_guardrails(draft: EntityDraft) -> None:  # T-EVAL-10
    w = World()
    p = w.service.create_proposal(ANA, AGENT, Origin.manual, "intenta tocar la plataforma")
    with pytest.raises(RegistryError) as info:
        w.service.put_draft(ANA, p.proposal_id, [draft], expected_rev=0)
    assert info.value.code is RegistryErrorCode.forbidden_role
    assert w.service.get_proposal(p.proposal_id).changes == []
```

- [ ] **Step 2: Verificar que fallan**

Run: `uv run pytest tests/registry/test_validation.py tests/registry/test_service_yardstick.py -q`
Expected: `ImportError: cannot import name 'platform_edits'`.

- [ ] **Step 3: Implementar**

`agent_core/registry/validation.py` (import `from agent_core.domain import PLATFORM_METRIC_PREFIX, Agent, Flow, canonical_bytes`):

```python
def platform_edits(drafts: Sequence[EntityDraft]) -> list[str]:
    """Rutas del borrador que declaran o umbralizan un guardarraíl de plataforma (spec de evaluación §7).

    Los guardarraíles son de la plataforma: ninguna propuesta los edita, ni por la métrica del agente (que M1
    rechaza también con `MT-05`) ni por los umbrales de la suite."""
    found: list[str] = []
    for d in drafts:
        where = f"{d.kind[:40]}:{d.id[:80]}"
        if d.kind == "agent" and isinstance(metrics := d.content.get("metrics"), list):
            for i, item in enumerate(metrics):
                mid = item.get("id") if isinstance(item, dict) else None
                if isinstance(mid, str) and mid.startswith(PLATFORM_METRIC_PREFIX):
                    found.append(f"{where}/metrics/{i}/id")
        elif d.kind == "eval_suite" and isinstance(thresholds := d.content.get("thresholds"), dict):
            found.extend(f"{where}/thresholds/{key[:80]}" for key in sorted(thresholds)
                         if key.startswith(PLATFORM_METRIC_PREFIX))
    return found
```

`agent_core/registry/service.py`, en `put_draft`, después de `check_draft_limits`:

```python
        edits = platform_edits(changes)
        if edits:
            raise RegistryError(RegistryErrorCode.forbidden_role,
                                "los guardarraíles de plataforma no se editan desde una propuesta",
                                payload=edits)  # type: ignore[arg-type]
```

(y `platform_edits` en el import de `validation`).

- [ ] **Step 4: Verificar**

```bash
uv run pytest tests/registry -q
uv run mypy
uv run ruff check .
```
Expected: todo en verde.

- [ ] **Step 5: Commit**

```bash
git add agent_core/registry/validation.py agent_core/registry/service.py tests/registry/test_validation.py tests/registry/test_service_yardstick.py
git commit -m "feat(registry): forbidden_role si una propuesta toca los guardarrailes de plataforma (T-EVAL-10)" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 10: Pruebas de extremo a extremo del servicio: T-EVAL-09 y T-EVAL-16

Estas pruebas cubren comportamiento ya construido en las tareas 6–9: deben pasar al escribirlas. Si alguna falla, es un defecto de esas tareas, no de esta.

**Files:**
- Modify: `tests/registry/test_service_yardstick.py` (import: `bot` desde `helpers`)

- [ ] **Step 1: Escribir las pruebas**

```python
def test_published_loosening_only_affects_later_proposals() -> None:  # T-EVAL-09
    w = World()
    pid, h = loosening_evaluated(w)
    own = w.evaluator.requests[-1].old
    assert own is not None and own.suite is not None and own.suite.version == "1.0.0"  # se mide con la vieja
    w.service.approve(ANA, pid, h, accept_yardstick_loosened=True)
    loosened = w.service.publish(ANA, pid, "k-afloja").release_id
    assert [str(r) for r in w.service.get_release(loosened).eval_suite_refs] == [
        "eval_suite:disputas-suite@1.1.0"]
    later = w.service.create_proposal(ANA, AGENT, Origin.manual, "posterior")
    w.service.put_draft(ANA, later.proposal_id, [prompt_draft(version="1.3.0", text="Una más.")],
                        expected_rev=0)
    w.service.freeze(ANA, later.proposal_id)
    report = w.service.evaluate(ANA, later.proposal_id, "disputas-suite")
    old = w.evaluator.requests[-1].old
    assert old is not None and old.suite is not None and old.suite.version == "1.1.0"
    assert report.yardstick_changes == []


def test_builder_cannot_decide_even_with_its_own_suite_and_metrics() -> None:  # T-EVAL-16
    w = World()
    builder = bot()
    p = w.service.create_proposal(builder, AGENT, Origin.builder_chat, "propuesta del constructor")
    drafts = [agent_with_metrics(w, "1.1.0", metric("m_gate")),
              suite_draft(thresholds={"m_gate": {"noise_margin": "0", "floor": "0"}})]
    w.service.put_draft(builder, p.proposal_id, drafts, expected_rev=0)
    view = w.service.freeze(builder, p.proposal_id)
    assert w.service.evaluate(builder, p.proposal_id, "disputas-suite").verdict == "pass"
    for call in (
        lambda: w.service.approve(builder, p.proposal_id, view.candidate_hash,
                                  accept_yardstick_loosened=True),
        lambda: w.service.publish(builder, p.proposal_id, "k"),
        lambda: w.service.promote(builder, AGENT, "prod", "rel-demo"),
        lambda: w.service.revoke(builder, "rel-demo", "x"),
    ):
        with pytest.raises(RegistryError) as info:
            call()
        assert info.value.code is RegistryErrorCode.forbidden_role
```

- [ ] **Step 2: Verificar**

Run: `uv run pytest tests/registry/test_service_yardstick.py -q`
Expected: todas pasan.

- [ ] **Step 3: Commit**

```bash
git add tests/registry/test_service_yardstick.py
git commit -m "feat(registry): pruebas T-EVAL-09 y T-EVAL-16 sobre el servicio con doble vara" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 11: Specs, ADR 0018 y temas abiertos describen el diseño unificado

**Files:**
- Modify: `docs/specs/2026-09-30-evaluacion-y-metricas-design.md`, `docs/specs/2026-09-29-registry-design.md`, `docs/adr/0018-propuestas-y-gate-de-publicacion.md`, `docs/specs/TEMAS-ABIERTOS-PENDIENTES.md`

- [ ] **Step 1: Spec de evaluación**

1. Encabezado: `Estado: **aprobado e integrado en el registry** (2026-10-01; plan docs/superpowers/plans/2026-10-01-integracion-gate-doble-vara.md)`.
2. **§5** — reemplazar el bloque de código por el formato real (`agent_core/registry/suite.py`): `EvalSuite(id, version, agent_id, repetitions=3, scenarios: list[Scenario | DatasetScenario], thresholds: dict[str, MetricThreshold])`; `Scenario(id, source="scripted", principal, steps, seed, sensitive_values, expect, assertions ≤ 20, repetitions: 1..10 | None)`; `DatasetScenario(id, source="dataset", dataset_id, dataset_hash, reference_outcome, expect, assertions, repetitions)`; `Assertion`; `MetricThreshold`. Añadir: "Un escenario sin `source` es `scripted` (las suites del registry anteriores siguen siendo válidas). El guion es el de `steps` (`start`/`turn`/`confirm`); `signal` para agentes `task` y `clock_start` no existen todavía (abierto 12)". Bullet de `suite_problems`: "`duplicate_scenario` solo con instancias sin validar: `EvalSuite` rechaza ids repetidos al parsear". Añadir: "`suite_problems` corre al validar (suites del borrador, `REG-SUITE` con el código al inicio del mensaje) y al evaluar (la suite elegida, `validation_failed`)".
3. **§6.1** — después del punto 6, añadir: "**Corridas:** `cand_on_new` siempre; `base_on_old` y `cand_on_old` si la base registró su suite (`releases.eval_suite_refs`); si la suite vieja es igual a la nueva, la candidata se corre una vez y se mide con las definiciones de cada vara. **Base sin suite registrada** (D3): solo la vara nueva contra los `floor` y la plataforma; la clasificación de §6.2 sí compara las métricas. **Cálculo** (D4): cada métrica sobre todos los eventos de la corrida de la suite; un escenario pasa si pasa en todas sus repeticiones; `GateItem` reporta `noise_margin` (vara vieja) y `floor` (vara nueva y plataforma) por separado". Borrar el párrafo "`evaluate_gate` no verifica…" y sustituirlo por "Las precondiciones de `evaluate_gate` (suite sin problemas, ids únicos, cada medición sobre su suite y release) las garantiza `RegistryService.evaluate`".
4. **§7** — añadir la tabla de definición de cada guardarraíl (D6) y "Ningún rol los edita: `put_draft` responde `forbidden_role` si un borrador declara una métrica `platform_*` o un umbral para una (además de `MT-05`)".
5. **§8.5** — añadir: "`approve` exige `accept_yardstick_loosened = true` si la última evaluación trae `yardstick_changes`; si no, `loosening_not_accepted` (409). La aprobación guarda lo aceptado (`Approval.yardstick_loosened`). `GET /proposals/{id}` devuelve `review` con `functional_changes`, `suite`, `suite_changes`, `gate` y `yardstick_loosened`".
6. **§11** — pieza 2: "hecha (2026-10-01)"; pieza 3: "evaluador en memoria del DSL hecho (`registry/evaluation/metric_eval.py`); juez LLM y datasets pendientes".
7. **§12 Registry** — `releases` guarda las suites en la tabla `reg_release_eval_suites`; `reg_approvals.yardstick_loosened`; error `loosening_not_accepted`; `EvalPort.run(EvalRequest)`.
8. **§13** — 9: "decidido para el gate (D4, D5): sin medir = fallo; `count`/`sum` vacíos = 0"; 10: "decidido en parte (D4): población de la corrida completa, escenario = todas sus repeticiones; `group_by` sigue abierto (sin medir en el gate)"; 11: "decidido (D8)". Nuevos: 12 "Agentes `task` (`signal`) y `Clock` por escenario en el harness"; 13 "Métricas `gate`/`guardrail` sobre `registry.*` o `judge`: hoy siempre fallan el gate en sandbox (D5)".
9. **§15** — reescribir la definición de terminado con esta trazabilidad:

| T-EVAL | Prueba |
|---|---|
| 01 | `tests/m01/test_agent_metrics.py` |
| 02, 03 | `tests/m00/test_metrics.py` |
| 04 | pendiente (analítica, tema #11) |
| 05, 06, 07, 13, 15 | `tests/registry/test_gate.py` |
| 08 | `tests/registry/test_yardstick.py`, `tests/registry/test_service_yardstick.py::test_the_report_carries_the_loosening` |
| 09 | `tests/registry/test_service_yardstick.py::test_published_loosening_only_affects_later_proposals` |
| 10 | `tests/registry/test_service_yardstick.py::test_a_proposal_cannot_edit_platform_guardrails` y `MT-05` |
| 11 | `tests/registry/test_suite.py`, `tests/registry/test_service_yardstick.py::test_evaluate_rejects_a_published_suite_with_problems` |
| 12 | `tests/registry/test_suite.py::test_dataset_source_is_disabled` |
| 14 | parcial: `tests/registry/test_metric_eval.py` (falta el compilador SQL) |
| 16 | `tests/registry/test_service_yardstick.py::test_builder_cannot_decide_even_with_its_own_suite_and_metrics` |
| 17 | `tests/registry/test_service_yardstick.py::test_loosening_needs_its_own_approval`, `::test_review_shows_change_suite_and_loosening_apart` |

- [ ] **Step 2: Spec del registry**

1. §3.2: sustituir la nota "Pendiente de incorporarse…" por filas de tabla: `release_eval_suites` (`(release_id, id)` PK, `kind = eval_suite`, `version`; FK a `entity_versions`; solo inserción) y la columna `approvals.yardstick_loosened` (JSON).
2. §5.2 punto 6: "Cada `eval_suite` del borrador pasa `suite_problems` (spec de evaluación §5): `REG-SUITE` con el código al inicio del mensaje". Reemplazar la nota "Enmendado por el ADR 0020… se reconcilia en un cambio posterior" por "**Reconciliado (2026-10-01):** el gate de este registry es el de la spec de evaluación §6".
3. §6.1: el YAML de ejemplo sin `noise_margin`/`floor`, con `thresholds`, un `assertions` y `repetitions` por escenario.
4. §6.2: "Corre hasta tres mediciones (vara nueva sobre la candidata; vara vieja sobre base y candidata)"; punto 3: "una corrida pasa si cumple `expect` y todas sus `assertions`; las métricas del agente se calculan con el evaluador en memoria del DSL"; punto 4: guardarraíles con sus ids `platform_*` (D6). Costo: "hasta 3 mediciones × N escenarios × k corridas".
5. §6.4: reemplazar las cuatro viñetas del veredicto por "Doble vara: spec de evaluación §6 (guardarraíles de plataforma en 0, N métricas `gate` por separado, sin métrica principal ni puntaje compuesto)". `EvalReport`: `verdict`, `items: list[GateItem]`, `runs: GateRuns`, `results`, `judge_notes`, `yardstick_changes`, `detail`.
6. §7.2: `approve(actor, proposal_id, candidate_hash, *, accept_yardstick_loosened=False)`; `get_proposal` devuelve también `review`. §7.3: `EvalPort.run(request: EvalRequest) -> EvalReport` con `EvalRequest(candidate, new: Yardstick, base, old: Yardstick | None)`. §7.4: cuerpo de `approve` con `accept_yardstick_loosened`; fila `loosening_not_accepted | 409 | la evaluación trae yardstick_changes y la aprobación no los acepta`; `forbidden_role` también "si un borrador edita un guardarraíl de plataforma". §7.5: `approve --accept-yardstick-loosened`.
7. §13: T-REG-08 → "un guardarraíl que empeora falla aunque una métrica `gate` mejore (= T-EVAL-05)"; T-REG-09 → "cada métrica `gate` dentro de su margen pasa, fuera falla (= T-EVAL-06)"; T-REG-10 → "sin base, contra los `floor` (= T-EVAL-13)"; T-REG-25 → "… y los guardarraíles de plataforma y las aserciones se cuentan bien".
8. §14: sustituir el último ítem por "[x] Gate con doble vara integrado (2026-10-01): `suite.py`, `evaluation/{metric_eval,scoring,gate,yardstick,evaluator}.py`; T-EVAL según la spec de evaluación §15". Trazabilidad: 08/09/10 → las pruebas `test_gate.py` de T-EVAL-05/06/13; 25 → `tests/registry/test_scoring.py::test_resolved_with_verified_action_passes`.
9. §15: "**Registry** (2026-10-01): tabla `reg_release_eval_suites`, columna `reg_approvals.yardstick_loosened` (`ALTER … ADD COLUMN IF NOT EXISTS`), código `loosening_not_accepted`. Sin cambios en M0".

- [ ] **Step 3: ADR 0018 y temas abiertos**

- ADR 0018, línea de Estado: añadir "La enmienda de entrega (2026-09-30), punto 2 (métrica principal = tasa de pase), queda reemplazada por el gate con doble vara del ADR 0020, integrado en el registry el 2026-10-01". Al final del punto 2 de la enmienda: "(**Reemplazado** por el ADR 0020.)".
- `TEMAS-ABIERTOS-PENDIENTES.md` #11: "El evaluador en memoria del DSL existe (`agent_core/registry/evaluation/metric_eval.py`): NULL para campos ausentes, percentil discreto y redondeo a 4 decimales son la semántica que el compilador a SQL debe reproducir (T-EVAL-14)". #19: "La fuente `dataset` es `DatasetScenario` (`source: dataset`)".

- [ ] **Step 4: Verificar enlaces y menciones viejas**

Con Grep sobre `docs/`: `double_gate|evaluation/platform|evaluation\.suite|métrica principal` → solo menciones históricas en `docs/superpowers/plans/2026-09-30-evaluacion-y-metricas.md` y las notas de reemplazo escritas en este paso.

- [ ] **Step 5: Commit**

```bash
git add docs/specs/2026-09-30-evaluacion-y-metricas-design.md docs/specs/2026-09-29-registry-design.md docs/adr/0018-propuestas-y-gate-de-publicacion.md docs/specs/TEMAS-ABIERTOS-PENDIENTES.md
git commit -m "docs(registry): specs, ADR 0018 y temas abiertos describen el gate con doble vara integrado (ADR 0020)" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 12: Verificación completa

- [ ] **Step 1: Suite completa y chequeos**

```bash
uv run pytest -q
uv run lint-imports
uv run mypy
uv run ruff check .
uv run agentcore contracts --check
```
Expected: 0 failed (las de `tests/integration` se omiten sin Postgres); `lint-imports` "Contracts: N kept, 0 broken"; `mypy` "Success"; `ruff` "All checks passed!"; `contracts --check` sin diferencias (D12: `SCHEMA_VERSION` sigue en 1.2.0).

- [ ] **Step 2: Postgres (lo corre el dueño)**

```bash
docker compose up -d postgres
uv run pytest tests/integration/test_registry_postgres.py -q
```
Pruebas de integración que este plan toca o añade:

- **Nuevas:** `test_release_records_its_eval_suite`, `test_release_eval_suites_are_insert_only` (tarea 6), `test_approval_keeps_the_accepted_loosening` (tarea 8).
- **Existentes que ahora ejercitan código nuevo:** `test_end_to_end_prompt_change` (T-REG-27: evaluador real con la vara nueva, base importada sin suite → D3), `test_immutable_tables_reject_update_and_delete`, `test_publish_failure_mid_way_leaves_nothing`, `test_second_publish_on_same_agent_is_stale`, `test_revoked_release_not_resolved_for_new_runs` y el resto del archivo (todas publican con `eval_suite_refs` y aprueban con la columna nueva).
- Una BD de desarrollo ya creada con el esquema anterior recibe la tabla y la columna nuevas por `CREATE TABLE IF NOT EXISTS` y `ALTER TABLE … ADD COLUMN IF NOT EXISTS`; sus reportes y suites viejos no se leen (D2): recrearla con `docker compose down -v`.

- [ ] **Step 3: Sin duplicados**

Con Grep sobre `agent_core`, `testing` y `tests`: `^class EvalSuite`, `^class EvalReport`, `^Verdict =` → 1 resultado cada uno; `SuiteMetrics|MetricCheck|GUARDRAILS =|def decide|def aggregate` → 0; `tests/registry/yardstick` no existe.

- [ ] **Step 4: Descripción del PR**

Incluir: R1–R7 cumplidos, la tabla "Decisiones que necesito del dueño" con lo elegido, la tabla de pruebas de `main` que cambiaron, y el aviso de D2 (BD de desarrollo a recrear). No hay cambio de interfaz de M0 (regla 7 de CLAUDE.md no aplica).
