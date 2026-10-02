# Spec — Evaluación y métricas por agente

- Estado: **aprobado e integrado en el registry** (2026-10-01; plan `docs/superpowers/plans/2026-10-01-integracion-gate-doble-vara.md`). Quedan pendientes los puntos marcados en §11, §13 y §15
- Fecha: 2026-09-30 (integración: 2026-10-01)
- Repo: `agent-core`
- Alcance: diseño transversal. Se implementa en piezas (§11); este documento especifica M0/M1 y el registry, y deja las interfaces hacia la unidad 6 y la analítica
- ADRs: 0020 (este diseño); enmienda 0018 (gate de publicación); además 0003 (observabilidad), 0008 (vistas), 0017 (registry en Postgres), 0019 (agentes internos)
- Specs relacionados: `2026-09-29-registry-design.md` (§3.2, §5.2, §7, §13, §17), `TEMAS-ABIERTOS-PENDIENTES.md` (#11, #20)
- Autor: Juan Zapata, con Claude

## 1. Propósito y límites

Cada agente declara **sus propias métricas** como datos versionados. Esas mismas definiciones sirven para dos usos:

1. **Gate de release:** se calculan sobre los escenarios de una `eval_suite` y deciden si una propuesta puede publicarse.
2. **Monitoreo en producción:** se calculan sobre los eventos reales y alimentan alertas para personas.

El gate no se desbloquea con métricas sueltas: se desbloquea con **evals** (escenarios) sobre los que se calculan las métricas. Si el rendimiento es peor, no hay release.

**Dentro de alcance:** la declaración de métricas (`Agent.metrics`), el DSL y su catálogo de eventos, la entidad `eval_suite`, el gate de evaluación con doble vara, el flujo del agente constructor de punta a punta y los roles que lo protegen.

**Fuera de alcance:** el ejecutor de escenarios, el juez LLM y el conector de datasets reales (unidad 6, sin spec); el cálculo y la visualización en producción, las alertas y la retención (analítica, tema #11). Aquí solo se fijan sus interfaces.

## 2. Decisiones de diseño

| # | Decisión | Detalle |
|---|---|---|
| 1 | **La métrica se declara en el agente** | Campo `Agent.metrics`. Cambiar una métrica cambia la versión del agente: requiere propuesta, evaluación y aprobación. |
| 2 | **DSL declarativo restringido** | Evento, filtro, agregación, ventana y agrupación como datos. M1 lo valida; un compilador lo traduce a SQL. Nada de SQL libre en una entidad. |
| 3 | **Tres roles de métrica** | `guardrail` (tolerancia cero), `gate` (no peor que la base dentro del ruido, y sobre el piso) y `monitor` (no bloquea). |
| 4 | **N métricas `gate`, cada una por separado** | Reemplaza la "métrica principal" única del ADR 0018. Sin puntaje compuesto. |
| 5 | **Eval suite = entidad del registry** | Con semver propio; no entra en la release del motor. La `releases` del registry guarda las suites usadas. |
| 6 | **Doble vara** | La candidata debe pasar la suite y las definiciones de la release base, sin cambios, y además su propia suite nueva. |
| 7 | **Aflojar la vara se marca y lo aprueba una persona por separado** | `yardstick_loosened`. El constructor puede redactarlo; solo vale para propuestas futuras. |
| 8 | **Guardarraíles de plataforma** | Un conjunto universal que ninguna propuesta puede editar. |
| 9 | **Escenarios con fuente intercambiable** | `scripted` (sintético) habilitado; `dataset` diseñado y desactivado hasta un ADR aparte. |
| 10 | **Calificación mixta** | Métricas deterministas sobre eventos; juez LLM opcional con perfil fijo y margen de ruido propio. |
| 11 | **Producción: calcular, mostrar y alertar a personas** | Sin reversión automática. Revocar o volver atrás sigue siendo humano. |

## 3. Métricas del agente (`Agent.metrics`)

Cambio en M0: `Agent.metrics: list[MetricDef] = []`. Es opcional; un agente sin métricas sigue válido, pero no es publicable por el gate sin una `eval_suite` (§8).

```python
class MetricDef(Model):
    id: EntityId                      # único dentro del agente
    description: str
    role: Literal["guardrail", "gate", "monitor"]
    higher_is_better: bool
    alert: AlertThreshold | None      # solo producción; no es el umbral del gate
    expr: MetricExpr | JudgeExpr      # una de las dos


class MetricExpr(Model):              # determinista, sobre eventos
    event: str                        # del catálogo cerrado (§4)
    where: list[Predicate] = []       # campos del catálogo, valores literales
    aggregation: Aggregation          # count | sum | avg | percentile | rate
    field: str | None                 # requerido por sum, avg y percentile, y solo por ellas
    percentile: int | None            # 1..99; requerido por percentile, y solo por ella
    denominator: MetricExpr | None    # requerido por rate, y solo por ella: un count
    window: Literal["scenario", "run"] | timedelta   # cota obligatoria; timedelta positivo
    group_by: list[str] = []          # campos del catálogo; máximo 3


class JudgeExpr(Model):               # único tipo no determinista
    judge_profile: EntityRef          # perfil de modelo con versión exacta
    rubric: str
    target_event: str                 # evento cuyo contenido se califica
```

- `rate` define el denominador en `denominator`, otra `MetricExpr` que debe ser un `count` con la misma ventana y agrupación que el numerador; no hay división libre.
- `window` es obligatoria. Una métrica sin cota se rechaza.
- Las expresiones no tienen funciones de hora, subconsultas ni uniones: no pueden depender del instante de ejecución ni leer datos fuera del catálogo.
- Una métrica `judge` declara su margen de ruido en la suite igual que las demás (§5), y el perfil del juez es parte de su definición: cambiar el perfil o la rúbrica cuenta como cambiar la expresión (§6.2).

## 4. Catálogo de eventos del DSL

Lista cerrada y tipada. Cada evento declara los campos filtrables y agrupables; M1 rechaza una métrica que use un evento o campo fuera del catálogo.

- **`engine.*`:** eventos de M0 (`turn_completed`, `agent_step`, `escalated` y demás). Solo exponen campos que el evento ya lleva.
- **`registry.*`:** eventos de la bitácora del registry (`proposal_created`, `evaluated`, `approved`, `published`, `promoted`, `revoked`…). Llevan `origin`, `actor_role` y `agent_id` para poder filtrar por agente.
- **PII:** los campos de datos de cliente no existen en el catálogo, así que ninguna métrica puede referenciarlos (regla 6 de CLAUDE.md, ADR 0008).
- Agregar un evento medible es un cambio de catálogo versionado con `SCHEMA_VERSION`, no un cambio de la métrica.

## 5. Eval suite (entidad del registry)

Vive en `agent_core.registry`; no entra en M0 ni en la release del motor (registry §3.1).

Formato real, en `agent_core/registry/suite.py`:

```python
class EvalSuite(_M):
    id: EntityId
    version: ExactVersion
    agent_id: EntityId
    repetitions: PositiveInt = 3             # 1..10; las de cada escenario que no declare las suyas
    scenarios: list[Scenario | DatasetScenario] = Field(min_length=1, max_length=200)   # discrimina `source`
    thresholds: dict[str, MetricThreshold] = {}   # por id de métrica `gate` o `guardrail`


class Scenario(_M):                          # `source = "scripted"`: habilitada
    id: ScenarioId
    source: Literal["scripted"] = "scripted"
    principal: ScenarioPrincipal             # sintético
    steps: list[Step]                        # `start` (uno, primero), `turn`, `confirm`; 1..50
    seed: SandboxSeed                        # respuestas sembradas por tool
    sensitive_values: list[str] = []         # valores cuya aparición en un evento es una fuga de PII
    expect: Expect                           # outcome, actions_verified, escalated
    assertions: list[Assertion] = []         # máximo 20
    repetitions: int | None = None           # 1..10; None = las de la suite; de ahí se estima el ruido


class DatasetScenario(_M):                   # `source = "dataset"`: DISEÑADA, DESACTIVADA (§9)
    id: ScenarioId
    source: Literal["dataset"]
    dataset_id: str
    dataset_hash: Sha256Hex
    reference_outcome: bool = False          # usa el resultado histórico como etiqueta
    expect: Expect
    assertions: list[Assertion] = []         # máximo 20
    repetitions: int | None = None


class Assertion(_M):                         # sobre eventos: "debe escalar con X", "debe llamar a la tool Y"
    event: str                               # del catálogo (§4)
    where: list[Predicate] = []              # máximo 8
    expect: Literal["at_least_one", "none"] = "at_least_one"


class MetricThreshold(_M):
    noise_margin: Decimal                    # tolerancia frente a la base; no negativo
    floor: Decimal | None = None             # exigido a toda métrica gate/guardrail nueva o cambiada, con o sin base
```

- Un escenario sin `source` es `scripted` (las suites del registry anteriores siguen siendo válidas). El guion es el de `steps` (`start`/`turn`/`confirm`); `signal` para agentes `task` y `clock_start` no existen todavía (abierto 12).
- `noise_margin` y `floor` ya no son campos de la suite entera: viven por métrica en `thresholds` (decisión D1 de la integración; formato sin migración, las bases de desarrollo con suites del formato anterior se recrean).
- `suite_problems(agent, suite)` devuelve lo que impide publicar: `missing_suite`, `agent_mismatch`, `duplicate_scenario` (solo aparece con instancias sin validar: `EvalSuite` rechaza ids repetidos al parsear), `dataset_source_disabled`, `unknown_assertion_event`, `invalid_assertion_filter` (el `where` de una aserción usa un campo que no existe en el evento, un valor de otro tipo o un operador de orden sobre un campo no numérico; misma comprobación que `MT-02`, función `predicate_problems` de M0; sin ella un filtro mal escrito con `expect: none` pasaría siempre), `missing_threshold` (métrica `gate`/`guardrail` sin umbral) y `unknown_threshold_metric`.
- `suite_problems` corre al validar (suites del borrador; `REG-SUITE`, con el código al inicio del mensaje) y al evaluar (la suite elegida; `validation_failed`). **Lo que no corre:** el rechazo de un agente sin suite (`missing_suite`) existe solo como comprobación pura, `suite_problems(agent, None)`; el servicio no lo cablea porque la suite se elige al evaluar (`evaluate(suite_id, suite_version)`) y no hay una relación agente → suite al validar (ver §8.3 y §15).
- `floor` se exige a toda métrica `gate`/`guardrail` nueva o cambiada, haya o no release base; es un mínimo si mayor es mejor y un máximo si menor es mejor. Un `user_turns` vacío no es válido (un escenario que no ejecuta nada).
- Todos los valores numéricos son `Decimal` (regla 4 de CLAUDE.md).
- Los escenarios `scripted` usan solo datos sintéticos (regla 5 de CLAUDE.md, registry §7).
- Una corrida pasa si cumple su `expect` y todas sus `assertions`; un escenario cuenta como "pasado" solo si pasa en todas sus repeticiones (D4). El resultado por escenario y por corrida aparece en el reporte.

## 6. Gate de evaluación (reemplaza registry §5.2)

Se congela la candidata **C** y se identifica la base **B** (puede no existir).

### 6.1 Cálculo

1. **Vara vieja** (solo si hay B). Se corren la suite y las definiciones de métricas de B, tal como estaban, sobre C y sobre B.
   - `guardrail`: C no puede ser peor que B. Tolerancia cero.
   - `gate`: C no puede ser peor que B por más del `noise_margin`, aplicado en la dirección de la métrica (`higher_is_better`).
   - Un escenario que pasaba en B y no pasa en C falla el gate. Un escenario de la suite de B sin resultado en la corrida de B o de C falla (fail-closed); solo un fallo explícito en B lo exime.
2. **Vara nueva.** Se corre la suite de C. Toda métrica o escenario nuevo o modificado debe superar su `floor` (un máximo si la métrica es de menor-es-mejor). Si la métrica existía sin cambios en B, debe además superar a B.
3. **Guardarraíles de plataforma** (§7) se evalúan siempre y deben valer 0 en la candidata, medidos en la corrida de la suite nueva. Si hay base, además deben estar medidos en las dos corridas de la suite vieja (B y C) y valer 0 también en la corrida vieja de C (no basta con no empeorar frente a la base: una fuga medida en C no pasa); **un guardarraíl de plataforma sin medir en cualquiera de las dos corridas viejas falla** (fail-closed). Sin medir en la suite nueva también falla.
4. **Veredicto** = AND de todo lo anterior. Cada métrica se reporta por separado con valor, base y umbral. No hay puntaje compuesto. Si una métrica de B no puede calcularse sobre C, el gate falla.
5. **Sin B:** solo se aplica la vara nueva contra los `floor`.
6. `failed_infra`, sin excepción manual del gate y propuesta que vuelve a `draft` ante un fallo: sin cambios respecto al registry §5.2.

**Corridas** (`agent_core/registry/evaluation/evaluator.py`): `cand_on_new` siempre; `base_on_old` y `cand_on_old` si la base registró su suite (`releases.eval_suite_refs`); si la suite vieja es igual a la nueva, la candidata se corre una vez y se mide con las definiciones de cada vara. **Base sin suite registrada** (D3): la vara vieja se reduce a las métricas de la base, sin suite que correr; solo se aplican la vara nueva contra los `floor` y la plataforma, y la clasificación de §6.2 sí compara las métricas.

**Cálculo** (D4, `scoring.py` y `metric_eval.py`): cada métrica se calcula sobre todos los eventos de la corrida de la suite juntos; un escenario pasa si pasa en todas sus repeticiones; `GateItem` reporta `noise_margin` (vara vieja) y `floor` (vara nueva y plataforma) por separado (D8). Una métrica `judge`, con `group_by` o sobre eventos `registry.*` no se mide en el gate (D5): queda fuera de la medición y, si es `gate` o `guardrail`, el gate falla (§13.9). Una aserción sobre un evento que el evaluador no observa nunca se cumple.

**Limitación conocida** (errata A11): la vara vieja juzga una métrica que no cambió de identidad (`role`, `higher_is_better`, `expr`) con los umbrales de la base. Si la propuesta sube su `floor` o baja su `noise_margin`, §6.2 lo clasifica como endurecimiento, pero el gate no exige el umbral más estricto (solo `_new_items` en `gate.py` exige el `floor` a las métricas nuevas o cambiadas).

Ambas mediciones usan la misma suite congelada y el mismo `candidate_hash`.

Las precondiciones de `evaluate_gate` (suite sin problemas, ids de métrica únicos —`MT-03`— y cada medición sobre su suite y su release; `GateRuns` no lleva referencia a la suite ni al `candidate_hash`) las garantiza `RegistryService.evaluate`.

### 6.2 Clasificación de cambios en la vara

El registry compara la suite y las definiciones de C contra las de B. Solo las métricas `gate` y `guardrail` forman parte de la vara: borrar o cambiar una métrica `monitor` no se marca. Una métrica cuenta como cambiada si cambia su `role`, `higher_is_better` o `expr`; la descripción y la alerta no cuentan.

- **Solo endurecimiento:** escenarios añadidos, `noise_margin` menor, `floor` mayor, rol promovido (`monitor → gate → guardrail`) sin cambiar `expr` ni `higher_is_better`, métricas nuevas.
- **`yardstick_loosened`:** cualquier otro cambio sobre algo ya existente. Incluye borrar una métrica `gate`/`guardrail`, quitarle su umbral a la suite, borrar un escenario, bajar un `floor`, ampliar un `noise_margin`, degradar un rol, cambiar `expr`, `higher_is_better` (también si la métrica se promueve en la misma propuesta), el perfil del juez o su rúbrica, y bajar `repetitions`.

La marca aparece como elemento de aprobación aparte (§8). **Mientras no se publique, la vara vieja sigue vigente**, y una vez publicada solo cambia la vara de propuestas posteriores: nunca la de la propia propuesta que aflojó.

Consecuencia: si una métrica obsoleta bloquea un cambio legítimo, hacen falta dos propuestas: primero el aflojamiento solo, luego el cambio.

## 7. Guardarraíles de plataforma

Conjunto universal, definido por la plataforma y no por cada agente. Ninguna propuesta puede editarlo, ni siquiera por el constructor:

- fugas de PII;
- afirmaciones de éxito sin `verify`;
- escrituras sin verificar;
- citas a páginas de conocimiento no aprobadas.

Se calculan para todo agente sobre los eventos de cada escenario (`PLATFORM_GUARDRAILS` y `score_run` en `registry/evaluation/scoring.py`). Sus ids llevan el prefijo reservado `platform_`: M1 (`MT-05`) rechaza que un agente declare una métrica con ese prefijo.

| Id | Qué cuenta (D6) |
|---|---|
| `platform_pii_leak` | apariciones de cualquier `sensitive_values` del escenario en los eventos serializados de la corrida |
| `platform_unverified_success_claim` | 1 si la corrida cierra con `outcome = resolved` y alguna acción despachada no quedó verificada; 0 si no |
| `platform_unverified_write` | acciones despachadas (`ActionDispatched`) sin un `ActionVerified` con `result = verified` |
| `platform_unapproved_knowledge_citation` | respuestas `generated` (evento de M8) cuyo validador falló (`validator.ok` falso) o fue rechazado por las comprobaciones de citas de página (`page_citations`, `page_audience`). M8 nunca emite una respuesta así, de modo que el guardarraíl vigila que siga siendo cierto |

Los valores de la corrida se suman; el gate exige 0 en la candidata (§6.1.3). Ningún rol los edita: `put_draft` responde `forbidden_role` si un borrador declara una métrica `platform_*` o un umbral para una (además de `MT-05`; `platform_edits` en `registry/validation.py`, verificado en el servidor).

## 8. Flujo del constructor y roles

Ciclo sin cambios (`draft → validated → candidate → evaluated → approved → published`). Lo nuevo:

1. El constructor crea la propuesta con `origin = builder_chat` o `auto_detect`.
2. Redacta entidades, `Agent.metrics` y la `eval_suite` como borradores (la suite es un borrador del registry, cubierto por `write_draft` del ADR 0019).
3. `validated` añade: M1 valida el DSL y el catálogo; y un **chequeo de release** exige que todo agente tenga una suite (sin escenarios no se pueden medir los guardarraíles de plataforma) y que cada métrica `gate` o `guardrail` tenga umbral en ella. Sin suite, no es publicable. **Estado en el código:** `suite_problems` cubre ambas reglas, pero el servicio solo la aplica a las suites que existen (las del borrador al validar y la elegida al evaluar). Que un agente sin suite no pase a `validated` **no está cableado** (pendiente, abierto 14): hoy lo frena el gate, que exige una suite para evaluar (`evaluate` responde `not_found` si no existe).
4. Congela y evalúa con su credencial `constructor`. **Nunca** aprueba, publica, promueve ni revoca.
5. **La aprobación humana muestra tres elementos por separado:** el cambio funcional, la suite (escenarios, métricas, umbrales, `floor`) y la marca `yardstick_loosened` si existe. Si la propuesta es de un agente nuevo, la suite se revisa a fondo: el `floor` lo fijó el propio constructor.
6. **Aflojar la vara exige aceptarlo al aprobar:** `approve(actor, proposal_id, candidate_hash, *, accept_yardstick_loosened=False)` exige `accept_yardstick_loosened = true` si la última evaluación trae `yardstick_changes`; si no, responde `loosening_not_accepted` (409, con la lista de cambios en el cuerpo). La aprobación guarda lo aceptado (`Approval.yardstick_loosened`; columna `reg_approvals.yardstick_loosened` en Postgres). El valor es un booleano: no queda ligado a la lista vista, que sale de la evaluación vigente bajo el mismo hash. Por HTTP es el campo `accept_yardstick_loosened` del cuerpo de `POST /proposals/{id}/approve`; por CLI, `approve --accept-yardstick-loosened`.
7. **Vista de aprobación:** `GET /proposals/{id}` devuelve `review` (`ApprovalReview`) con `functional_changes`, `suite` (la versión con que se midió), `suite_changes` (el borrador de la suite, si cambió), `gate` (cada `GateItem`) y `yardstick_loosened`. Es `null` mientras no haya una evaluación del hash vigente.

Roles: sin cambios (`lector`, `constructor`, `aprobador`). Editar los guardarraíles de plataforma no lo permite ningún rol desde una propuesta.

Presupuestos por propuesta (borradores, evaluaciones, costo): sin cambios. Cada evaluación consume el presupuesto en proporción a `repetitions` × escenarios.

## 9. Fuentes de escenarios y datos reales

- **`scripted`:** habilitada. Datos sintéticos, tools simuladas, `Clock` fijo, motor real.
- **`dataset`:** el tipo y su interfaz existen, pero **M1/registry la rechazan** con `dataset_source_disabled` mientras no se apruebe el ADR que la habilite.
- Los datos reales **no viven en el repo ni en el registry**. El registry guarda solo `dataset_id` y `dataset_hash`.
- Los casos reales no suelen traer respuesta esperada: las métricas sobre ellos deben poder medirse sin etiqueta (escalamientos, `verify`, costo) o contra el resultado histórico (`reference_outcome`).
- La suite `scripted` sigue siendo la base obligatoria del gate; el dataset la complementaría, no la reemplazaría.
- Para habilitarla se necesita: un ADR firmado por el dueño de los datos que enmiende la regla 5 de CLAUDE.md y el registry §7; el origen de los datos (BD transaccional o warehouse), aún sin decidir; y su paso por las vistas tokenizadas de M7 (ADR 0008), sin PII hacia modelos, logs ni eventos.

## 10. Producción: monitoreo y alertas

- El mismo `MetricDef` se calcula sobre eventos reales, agrupado por `release_id` y `agent_id`.
- Una métrica puede declarar `alert`; al cruzarlo se notifica al dueño del agente.
- **Sin acción automática.** No hay reversión automática ni promoción automática. Revocar o volver a una release anterior sigue siendo del `aprobador` (registry §5.3).
- El cálculo, el almacenamiento, la visualización y la retención son del tema #11. Aquí solo se garantiza que **el evaluador en memoria (usado por `EvalPort`) y el compilador a SQL (analítica) producen el mismo valor sobre los mismos eventos** (T-EVAL-14).

### Métricas del agente constructor (ejemplo)

| id | rol | expresión (resumen) |
|---|---|---|
| `proposals_detected` | `monitor` | `count` de `registry.proposal_created` con `origin = auto_detect` |
| `proposals_per_detection` | `monitor` | `rate` de propuestas creadas sobre señales recibidas |
| `proposals_promoted` | `monitor` | `count` de `registry.promoted` cuyo origen es del constructor |
| `first_pass_validation_rate` | `gate` | `rate` de propuestas que llegan a `validated` sin volver a `draft` |
| `proposal_quality` | `gate` | `judge` sobre el contenido de la propuesta, con rúbrica y perfil fijos |
| `invariant_breaking_proposals` | `guardrail` | `count` de propuestas cuya validación reporta violaciones de invariantes |

Las de embudo son `monitor`: "promovidas" depende de una persona y "detectadas" depende de los datos de entrada, no de la calidad del constructor.

## 11. Piezas de implementación

Cada una tiene su propio plan:

1. **M0 y M1:** `MetricDef`/`MetricExpr`/`JudgeExpr`, el catálogo de eventos, `SCHEMA_VERSION` + `contracts/`, y reglas de validación del DSL (numeración al implementar).
2. **Registry:** entidad `eval_suite`, gate de §6, referencias a suites en `releases`, elemento de aprobación `yardstick_loosened`, guardarraíles de plataforma. **Hecha (2026-10-01).**
3. **Unidad 6 (sin spec):** `EvalPort`, ejecutor de escenarios, evaluador en memoria del DSL, juez LLM, conector de datasets reales. **Evaluador en memoria del DSL hecho** (`registry/evaluation/metric_eval.py`); juez LLM y datasets pendientes.
4. **Analítica (tema #11):** compilador del DSL a SQL, alertas, visualización.

Este trabajo especifica 1 y 2 y deja interfaces para 3 y 4. La pieza 2 está integrada; la 3 solo en su parte del evaluador en memoria.

## 12. Cambios en otros módulos y documentos

**M0** (cambio de interfaz para todos los módulos; regla 7 de CLAUDE.md):

- `Agent.metrics`, `MetricDef`, `MetricExpr`, `JudgeExpr`, `AlertThreshold`, `Predicate`, `Aggregation`.
- Catálogo de eventos medibles.
- Subir `SCHEMA_VERSION` y regenerar `contracts/`.
- Sin cambios en `Release`, `RegistryPort` ni `EngineEvent`.

**M1:** reglas `MT-01` a `MT-06` en `validate_agent` (evento y campos en el catálogo, tipos de valor, ids únicos, `target_event` del juez, prefijo `platform_` reservado, `judge_profile` resoluble). La ventana obligatoria, `field` según la agregación, `rate` bien formado y la ausencia de funciones de hora los impone el esquema de M0. El motor ignora `metrics` en runtime. Detalle en `docs/specs/motor/m01-validacion-estatica.md` §3.8.

**Registry:**

- §3.2: `releases` guarda las suites usadas (`eval_suite_refs`, metadato de gobierno; el motor no lo lee) en la tabla `reg_release_eval_suites` (`(release_id, id)`, solo inserción), y `reg_approvals.yardstick_loosened` guarda lo que la aprobación aceptó aflojar (`ALTER TABLE … ADD COLUMN IF NOT EXISTS`; sin cambios en M0).
- §5.2: sustituido por §6 de este documento.
- §7.3: `EvalPort.run(request: EvalRequest) -> EvalReport`, con `EvalRequest(candidate, new: Yardstick, base, old: Yardstick | None)`.
- §7.4: código de error `loosening_not_accepted` (409) y campo `accept_yardstick_loosened` en `approve`; `forbidden_role` también cuando un borrador edita un guardarraíl de plataforma.
- §7: la regla de datos sintéticos se refiere solo a `scripted`.
- §13: añade métricas de gobierno (propuestas con `yardstick_loosened`, tasa de aflojamiento aprobado).
- §17: añade los abiertos de §13 de este documento.

**ADR 0018:** punto 4 (métrica principal única) y punto 9 (`eval_suite`) quedan enmendados por el ADR 0020.

**`TEMAS-ABIERTOS-PENDIENTES.md`:** #11 apunta a este spec; nuevo #20 para el conector de datasets reales (#13 en la rama `feat/eval-metrics`).

## 13. Abiertos

1. **Conector de datasets reales:** origen (BD transaccional o warehouse), dueño de los datos, y el ADR que enmiende la regla 5 de CLAUDE.md y el registry §7 (tema #20).
2. **Eventos del registry y del motor:** si comparten tabla/log (M11 es el log del motor) o el compilador los une desde dos orígenes.
3. **Valores:** `floor`, `noise_margin` y `repetitions` por defecto; la calibración corresponde a cada suite.
4. **Gestión del juez LLM:** versión del perfil, cómo se calibra y qué hacer cuando el proveedor retira un modelo (cuenta como cambio de vara).
5. **Catálogo inicial** de eventos medibles de `engine.*`: lista exacta al implementar M0.
6. **Alertas:** destinatarios y canal (analítica, tema #11).
7. **Semver de la suite:** quién propone el salto (mismo abierto que el registry §17.3).
8. **Tamaño mínimo de muestra** para comparar contra la base cuando haya pocos escenarios.
9. **Métricas que el evaluador no calcula** (denominador cero, evento sin datos): **decidido para el gate (D4, D5):** una métrica sin medir cuenta como fallo si es `gate` o `guardrail`; `count` y `sum` sin filas valen 0.
10. **Forma de `EvalReport`:** **decidido en parte (D4):** la población es la corrida completa (todos sus eventos juntos) y un escenario pasa si pasa en todas sus repeticiones. `group_by` sigue abierto: una métrica con `group_by` no se mide en el gate (sin medir = fallo si es `gate`/`guardrail`); falta una forma para un vector por grupo, valores por ventana de escenario y varianza entre repeticiones.
11. **`GateItem.threshold` era ambiguo:** **decidido (D8):** `GateItem` lleva `noise_margin` (vara vieja) y `floor` (vara nueva y plataforma) por separado.
12. **Agentes `task` (`signal`) y `Clock` por escenario en el harness:** el guion de un escenario es solo `steps` (`start`/`turn`/`confirm`); no hay `signal` ni `clock_start`.
13. **Métricas `gate`/`guardrail` sobre `registry.*` o `judge`:** hoy siempre fallan el gate en el sandbox (D5), porque el evaluador solo observa eventos `engine.*` y no hay juez en el gate; hace falta decidir cómo medirlas (eventos del registry reales o escenarios del propio constructor).
14. **Rechazo de un agente sin suite en `validated` (§8.3, T-EVAL-11):** `suite_problems(agent, None)` lo detecta, pero el servicio no lo cablea: la suite se elige al evaluar (`evaluate(suite_id, suite_version)`) y no hay una relación agente → suite al validar. Decidir si el agente declara su suite o si `validate` recibe el `suite_id`.

## 14. Pruebas

| Id | Comportamiento |
|---|---|
| T-EVAL-01 | M1 rechaza un `event` o campo fuera del catálogo |
| T-EVAL-02 | El esquema de M0 rechaza una métrica sin `window`, o con `field` ausente cuando la agregación lo requiere |
| T-EVAL-03 | El esquema de M0 rechaza una expresión con función de hora; la referencia a un campo de PII la rechaza `MT-02` (campo fuera del catálogo) y el test del catálogo garantiza que ningún campo de PII entra en él |
| T-EVAL-04 | El compilador a SQL es determinista y no emite `now()` |
| T-EVAL-05 | El gate falla si un `guardrail` empeora, aunque un `gate` mejore |
| T-EVAL-06 | Cada métrica `gate` se evalúa por separado: basta una que falle para fallar el gate |
| T-EVAL-07 | La vara vieja sigue vigente cuando la propuesta cambia o borra métricas de B |
| T-EVAL-08 | Cada tipo de cambio de §6.2 produce `yardstick_loosened`; los endurecimientos no |
| T-EVAL-09 | Un `yardstick_loosened` publicado afecta solo a propuestas posteriores |
| T-EVAL-10 | Una propuesta no puede editar los guardarraíles de plataforma (`forbidden_role`) |
| T-EVAL-11 | Un agente sin `eval_suite`, o con métricas `gate`/`guardrail` sin umbral en ella, no es publicable |
| T-EVAL-12 | Un escenario `dataset` se rechaza con `dataset_source_disabled` |
| T-EVAL-13 | Sin base, el gate usa los `floor`; si falta el `floor` de una métrica, falla |
| T-EVAL-14 | El evaluador en memoria y el compilador SQL dan el mismo valor sobre los mismos eventos |
| T-EVAL-15 | `failed_infra` no cuenta como pase ni como fallo y no permite aprobar |
| T-EVAL-16 | El constructor no puede aprobar, publicar, promover ni revocar, aunque su propuesta incluya la suite |
| T-EVAL-17 | La aprobación expone por separado cambio funcional, suite y `yardstick_loosened` |

## 15. Definición de terminado

- `Agent.metrics` y los tipos asociados en M0, con `SCHEMA_VERSION` subida y `contracts/` regenerado (`uv run agentcore contracts --check` en verde). Esta integración no toca M0 (`SCHEMA_VERSION` sigue en 1.3.0, la que ya numeró `Agent.metrics`).
- Entidad `eval_suite`, gate de §6 y marca `yardstick_loosened` en el registry (`suite.py`, `evaluation/{metric_eval,scoring,gate,yardstick,evaluator}.py`, `service.py`), con la persistencia de las suites de cada release (`reg_release_eval_suites`) y de lo aceptado al aprobar (`reg_approvals.yardstick_loosened`).
- `import-linter`, `mypy` y `ruff` en verde.
- Sin TODO sin issue.

Trazabilidad (cada id existe como prueba; los pendientes están listados abajo):

| T-EVAL | Prueba |
|---|---|
| 01 | `tests/m01/test_agent_metrics.py` |
| 02, 03 | `tests/m00/test_metrics.py` |
| 04 | **pendiente** (analítica, tema #11): no existe el compilador a SQL |
| 05, 06, 07, 13, 15 | `tests/registry/test_gate.py` |
| 08 | `tests/registry/test_yardstick.py` (clasificación) y `tests/registry/test_service_yardstick.py::test_the_report_carries_the_loosening` (servicio) |
| 09 | `tests/registry/test_service_yardstick.py::test_published_loosening_only_affects_later_proposals` |
| 10 | `tests/registry/test_service_yardstick.py::test_a_proposal_cannot_edit_platform_guardrails` y `MT-05` (`tests/m01/test_agent_metrics.py::test_mt_05_platform_ids_are_reserved`) |
| 11 | **parcial:** `tests/registry/test_suite.py` (`test_agent_without_suite_is_not_publishable`, `test_gate_and_guardrail_metrics_need_thresholds`) y `tests/registry/test_service_yardstick.py` (`test_validate_reports_problems_of_a_drafted_suite`, `test_evaluate_rejects_a_published_suite_with_problems`). Falta el rechazo de un agente sin suite al pasar a `validated` (§8.3, abierto 14) |
| 12 | `tests/registry/test_suite.py::test_dataset_source_is_disabled` |
| 14 | **parcial:** `tests/registry/test_metric_eval.py` fija la semántica del evaluador en memoria; falta el compilador a SQL y la prueba que compara ambos |
| 16 | `tests/registry/test_service_yardstick.py::test_builder_cannot_decide_even_with_its_own_suite_and_metrics` |
| 17 | `tests/registry/test_service_yardstick.py::test_loosening_needs_its_own_approval` y `::test_review_shows_change_suite_and_loosening_apart` (servicio y datos; la interfaz de aprobación en pantalla es de la plataforma) |

**Pendientes:**

- `T-EVAL-04` y `T-EVAL-14` completo: compilador del DSL a SQL (analítica, tema #11). El evaluador en memoria ya fija la semántica que debe reproducir.
- `T-EVAL-11` completo: cablear en `validated` el rechazo de un agente sin suite (abierto 14).
- La pantalla de aprobación de `T-EVAL-17` (la plataforma consume `review`).
- Sandbox real de `EvalPort` (hoy `LocalSandbox`), juez LLM y fuente `dataset` (§9).
- Una métrica sin cambios que sube su `floor` o baja su `noise_margin` se clasifica como endurecimiento pero el gate no lo exige (§6.1, limitación conocida).
