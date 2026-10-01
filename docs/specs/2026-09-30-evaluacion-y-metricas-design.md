# Spec — Evaluación y métricas por agente

- Estado: **borrador para revisión**
- Fecha: 2026-09-30
- Repo: `agent-core`
- Alcance: diseño transversal. Se implementa en piezas (§11); este documento especifica M0/M1 y el registry, y deja las interfaces hacia la unidad 6 y la analítica
- ADRs: 0020 (este diseño); enmienda 0018 (gate de publicación); además 0003 (observabilidad), 0008 (vistas), 0017 (registry en Postgres), 0019 (agentes internos)
- Specs relacionados: `2026-09-29-registry-design.md` (§3.2, §5.2, §7, §13, §17), `TEMAS-ABIERTOS-PENDIENTES.md` (#11, #13)
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

```python
class EvalSuite(Model):
    id: EntityId
    version: ExactVersion
    agent_id: EntityId
    scenarios: list[Scenario] = Field(min_length=1)
    thresholds: dict[str, MetricThreshold] = {}   # por id de métrica `gate` o `guardrail`


class Scenario(Model):
    id: EntityId
    source: ScriptedSource | DatasetSource   # discriminada por `kind`
    assertions: list[Assertion] = []         # máximo 20
    repetitions: PositiveInt = 1             # N; de ahí se estima el ruido


class Assertion(Model):                      # sobre eventos: "debe escalar con X", "debe llamar a la tool Y"
    event: str                               # del catálogo (§4)
    where: list[Predicate] = []              # máximo 8
    expect: Literal["at_least_one", "none"] = "at_least_one"


class ScriptedSource(Model):                 # habilitada
    kind: Literal["scripted"] = "scripted"
    user_turns: list[str] | None             # agente conversacional
    signal: JsonValue                        # agente task; exactamente uno de user_turns o signal
    tool_fixtures: dict[str, JsonValue] = {} # tools simuladas
    clock_start: UtcDatetime                 # Clock fijo


class DatasetSource(Model):                  # DISEÑADA, DESACTIVADA (§9)
    kind: Literal["dataset"] = "dataset"
    dataset_id: str
    dataset_hash: Sha256Hex
    reference_outcome: bool = False          # usa el resultado histórico como etiqueta


class MetricThreshold(Model):
    noise_margin: Decimal                    # tolerancia frente a la base; no negativo
    floor: Decimal | None = None             # solo agentes sin release base
```

- `suite_problems(agent, suite)` devuelve lo que impide publicar: `missing_suite`, `agent_mismatch`, `duplicate_scenario`, `dataset_source_disabled`, `unknown_assertion_event`, `missing_threshold` (métrica `gate`/`guardrail` sin umbral) y `unknown_threshold_metric`.
- Todos los valores numéricos son `Decimal` (regla 4 de CLAUDE.md).
- Los escenarios `scripted` usan solo datos sintéticos (regla 5 de CLAUDE.md, registry §7).
- Un escenario cuenta como "pasado" solo si todas sus aserciones se cumplen; el resultado por escenario aparece en el reporte.

## 6. Gate de evaluación (reemplaza registry §5.2)

Se congela la candidata **C** y se identifica la base **B** (puede no existir).

### 6.1 Cálculo

1. **Vara vieja** (solo si hay B). Se corren la suite y las definiciones de métricas de B, tal como estaban, sobre C y sobre B.
   - `guardrail`: C no puede ser peor que B. Tolerancia cero.
   - `gate`: C no puede ser peor que B por más del `noise_margin`.
   - Un escenario que pasaba en B y no pasa en C falla el gate.
2. **Vara nueva.** Se corre la suite de C. Toda métrica o escenario nuevo o modificado debe superar su `floor`. Si la métrica existía sin cambios en B, debe además superar a B.
3. **Guardarraíles de plataforma** (§7) se evalúan siempre y deben valer 0 en la candidata, medidos en la corrida de la suite nueva. Si hay base, además deben estar medidos en las dos corridas de la suite vieja (B y C) y no pueden empeorar frente a la base; **un guardarraíl de plataforma sin medir en cualquiera de las dos corridas viejas falla** (fail-closed). Sin medir en la suite nueva también falla.
4. **Veredicto** = AND de todo lo anterior. Cada métrica se reporta por separado con valor, base y umbral. No hay puntaje compuesto. Si una métrica de B no puede calcularse sobre C, el gate falla.
5. **Sin B:** solo se aplica la vara nueva contra los `floor`.
6. `failed_infra`, sin excepción manual del gate y propuesta que vuelve a `draft` ante un fallo: sin cambios respecto al registry §5.2.

Ambas mediciones usan la misma suite congelada y el mismo `candidate_hash`.

### 6.2 Clasificación de cambios en la vara

El registry compara la suite y las definiciones de C contra las de B. Solo las métricas `gate` y `guardrail` forman parte de la vara: borrar o cambiar una métrica `monitor` no se marca. Una métrica cuenta como cambiada si cambia su `role`, `higher_is_better` o `expr`; la descripción y la alerta no cuentan.

- **Solo endurecimiento:** escenarios añadidos, `noise_margin` menor, `floor` mayor, rol promovido (`monitor → gate → guardrail`), métricas nuevas.
- **`yardstick_loosened`:** cualquier otro cambio sobre algo ya existente. Incluye borrar una métrica `gate`/`guardrail`, quitarle su umbral a la suite, borrar un escenario, bajar un `floor`, ampliar un `noise_margin`, degradar un rol, cambiar `expr`, `higher_is_better`, el perfil del juez o su rúbrica, y bajar `repetitions`.

La marca aparece como elemento de aprobación aparte (§8). **Mientras no se publique, la vara vieja sigue vigente**, y una vez publicada solo cambia la vara de propuestas posteriores: nunca la de la propia propuesta que aflojó.

Consecuencia: si una métrica obsoleta bloquea un cambio legítimo, hacen falta dos propuestas: primero el aflojamiento solo, luego el cambio.

## 7. Guardarraíles de plataforma

Conjunto universal, definido por la plataforma y no por cada agente. Ninguna propuesta puede editarlo, ni siquiera por el constructor:

- fugas de PII;
- afirmaciones de éxito sin `verify`;
- escrituras sin verificar;
- citas a páginas de conocimiento no aprobadas.

Se calculan para todo agente sobre los eventos de cada escenario. Sus ids llevan el prefijo reservado `platform_`: M1 (`MT-05`) rechaza que un agente declare una métrica con ese prefijo. Una propuesta que intente modificarlos por otra vía se rechaza con `forbidden_role` (verificado por rol en el servidor).

## 8. Flujo del constructor y roles

Ciclo sin cambios (`draft → validated → candidate → evaluated → approved → published`). Lo nuevo:

1. El constructor crea la propuesta con `origin = builder_chat` o `auto_detect`.
2. Redacta entidades, `Agent.metrics` y la `eval_suite` como borradores (la suite es un borrador del registry, cubierto por `write_draft` del ADR 0019).
3. `validated` añade: M1 valida el DSL y el catálogo; y un **chequeo de release** exige que todo agente tenga una suite (sin escenarios no se pueden medir los guardarraíles de plataforma) y que cada métrica `gate` o `guardrail` tenga umbral en ella. Sin suite, no es publicable.
4. Congela y evalúa con su credencial `constructor`. **Nunca** aprueba, publica, promueve ni revoca.
5. **La aprobación humana muestra tres elementos por separado:** el cambio funcional, la suite (escenarios, métricas, umbrales, `floor`) y la marca `yardstick_loosened` si existe. Si la propuesta es de un agente nuevo, la suite se revisa a fondo: el `floor` lo fijó el propio constructor.

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
2. **Registry:** entidad `eval_suite`, gate de §6, referencias a suites en `releases`, elemento de aprobación `yardstick_loosened`, guardarraíles de plataforma.
3. **Unidad 6 (sin spec):** `EvalPort`, ejecutor de escenarios, evaluador en memoria del DSL, juez LLM, conector de datasets reales.
4. **Analítica (tema #11):** compilador del DSL a SQL, alertas, visualización.

Este trabajo especifica 1 y 2 y deja interfaces para 3 y 4.

## 12. Cambios en otros módulos y documentos

**M0** (cambio de interfaz para todos los módulos; regla 7 de CLAUDE.md):

- `Agent.metrics`, `MetricDef`, `MetricExpr`, `JudgeExpr`, `AlertThreshold`, `Predicate`, `Aggregation`.
- Catálogo de eventos medibles.
- Subir `SCHEMA_VERSION` y regenerar `contracts/`.
- Sin cambios en `Release`, `RegistryPort` ni `EngineEvent`.

**M1:** reglas `MT-01` a `MT-06` en `validate_agent` (evento y campos en el catálogo, tipos de valor, ids únicos, `target_event` del juez, prefijo `platform_` reservado, `judge_profile` resoluble). La ventana obligatoria, `field` según la agregación, `rate` bien formado y la ausencia de funciones de hora los impone el esquema de M0. El motor ignora `metrics` en runtime. Detalle en `docs/specs/motor/m01-validacion-estatica.md` §3.8.

**Registry:**

- §3.2: `releases` guarda `eval_suite_refs` (metadato de gobierno; el motor no lo lee).
- §5.2: sustituido por §6 de este documento.
- §7: la regla de datos sintéticos se refiere solo a `scripted`.
- §13: añade métricas de gobierno (propuestas con `yardstick_loosened`, tasa de aflojamiento aprobado).
- §17: añade los abiertos de §13 de este documento.

**ADR 0018:** punto 4 (métrica principal única) y punto 9 (`eval_suite`) quedan enmendados por el ADR 0020.

**`TEMAS-ABIERTOS-PENDIENTES.md`:** #11 apunta a este spec; nuevo #13 para el conector de datasets reales.

## 13. Abiertos

1. **Conector de datasets reales:** origen (BD transaccional o warehouse), dueño de los datos, y el ADR que enmiende la regla 5 de CLAUDE.md y el registry §7 (tema #13).
2. **Eventos del registry y del motor:** si comparten tabla/log (M11 es el log del motor) o el compilador los une desde dos orígenes.
3. **Valores:** `floor`, `noise_margin` y `repetitions` por defecto; la calibración corresponde a cada suite.
4. **Gestión del juez LLM:** versión del perfil, cómo se calibra y qué hacer cuando el proveedor retira un modelo (cuenta como cambio de vara).
5. **Catálogo inicial** de eventos medibles de `engine.*`: lista exacta al implementar M0.
6. **Alertas:** destinatarios y canal (analítica, tema #11).
7. **Semver de la suite:** quién propone el salto (mismo abierto que el registry §17.3).
8. **Tamaño mínimo de muestra** para comparar contra la base cuando haya pocos escenarios.
9. **Métricas que el evaluador no calcula** (denominador cero, evento sin datos): hoy cuentan como fallo; decidir con la unidad 6 si algún caso debe ser un pase.

## 14. Pruebas

| Id | Comportamiento |
|---|---|
| T-EVAL-01 | M1 rechaza un `event` o campo fuera del catálogo |
| T-EVAL-02 | M1 rechaza una métrica sin `window`, o con `field` ausente cuando la agregación lo requiere |
| T-EVAL-03 | M1 rechaza una expresión con función de hora o referencia a un campo de PII |
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

- `Agent.metrics` y los tipos asociados en M0, con `SCHEMA_VERSION` subida y `contracts/` regenerado (`uv run agentcore contracts --check` en verde).
- Reglas del DSL en M1 con `T-EVAL-01` a `T-EVAL-03` en verde (`T-EVAL-04`, compilador SQL, es de la analítica).
- Entidad `eval_suite`, gate de §6 y marca `yardstick_loosened` en el registry, con `T-EVAL-05` a `T-EVAL-13` y `T-EVAL-15` en verde (implementado como funciones puras en `agent_core.registry.evaluation`; `T-EVAL-16` y `T-EVAL-17` quedan para el servicio del registry, junto con la persistencia de `eval_suite` y de `releases.eval_suite_refs`).
- El evaluador en memoria y el compilador SQL pasan `T-EVAL-14` (cuando existan las piezas 3 y 4).
- `import-linter`, `mypy` y `ruff` en verde.
- Sin TODO sin issue.
