# Spec — Registry (unidad 2: entidades, versionado y publicación)

- Estado: **rev. 2 implementada (entrega), 2026-09-30.** Pendiente en §14: composición del motor con `PostgresRegistry` (raíz de composición del servidor, con el LLM gateway). §5.2 y §6 reconciliados con el ADR 0020 (gate con doble vara, 2026-10-01)
- Fecha: 2026-09-29 (rev. 1) · 2026-09-30 (rev. 2)
- Repo: `agent-core`
- Paquete: `agent_core.registry`
- ADRs: 0017 (Postgres como única fuente de verdad), 0018 (propuestas y gate de publicación, enmendado el 2026-09-30); además 0002 (contratos), 0006 (principal), 0007 (acciones), 0009 (políticas protegidas), 0015 (conocimiento)
- Usa: M0 (tipos y `RegistryPort`), M1 (validación y `pin_release`), `composition` (solo el evaluador) · Lo usan: el motor (por `RegistryPort`), la plataforma, el agente constructor, el detector automático, M12
- Autor: Juan Zapata, con Claude

> **Qué cambió en la rev. 2.** El alcance se recorta a lo que exige la demo del 05/10 (§0). Todo lo que la rev. 1 describía y queda fuera se conserva en §16 como diseño de fase 2. Cambios de fondo: la evaluación es **por agente** con escenarios corridos en un **sandbox** (§6); el estado `validated` se absorbe en `freeze`; `reject` devuelve a `draft`; un humano puede recorrer el ciclo completo y autoaprobarse; la candidata se evalúa desde un registro en memoria y nunca toca las tablas publicadas (§5).

## 0. Objetivo de la entrega

Demostrar **un ciclo de punta a punta**: una propuesta mejora la resolución de un caso, cambiando un prompt, el modelo de decisión, un flow o varias entidades a la vez. La propuesta pasa el gate de evaluación, un humano la aprueba y la publica, un run nuevo usa la release nueva y la plataforma muestra en el linaje qué versión cambió, por qué y quién la aprobó.

Criterio de éxito: T-REG-27 (§13) en verde, y el mismo ciclo reproducible por CLI y por API.

## 1. Propósito y límites

El registry guarda las **entidades** del sistema de decisión (agentes, flows, modelos de decisión, políticas, plantillas, prompts, tools, perfiles de modelo, configuración de idioma, reglas de injection, snapshots de conocimiento y suites de evaluación). También las versiona, las agrupa en **releases inmutables** y controla cómo se publican.

Clientes:

- **El motor en runtime:** solo lee releases publicadas, por el `RegistryPort` de M0.
- **Personas** (técnicas o no), que construyen a mano o conversando, y el **agente constructor** y el **detector automático**, que las asisten. Todos crean y mejoran entidades mediante *propuestas*. Una persona puede recorrer el ciclo completo sin ayuda; el agente es un asistente, nunca decide.
- **La plataforma:** muestra qué versión de cada entidad corrió en un run (linaje), qué cambió entre releases, el reporte de evaluación y quién aprobó.

**No hace:**

- Ejecutar flows en producción (M2/M4). Sí compone el motor para **evaluar** candidatas (§6).
- Ingesta ni mantenimiento de conocimiento (unidad 7). En la entrega, las propuestas no modifican páginas (§3.1).
- Autenticar identidades (M9 y el servicio de identidad) ni decidir permisos sobre datos de clientes (unidad 3).
- Aprobar por sí mismo: la aprobación la da siempre un principal humano (§8).

## 2. Decisiones de diseño

| # | Decisión | Detalle |
|---|---|---|
| 1 | **Postgres es la única fuente de verdad** | Tablas `reg_*` en el `search_path` de la conexión (no hay un esquema `registry` aparte). Git no interviene. El YAML de M1 sirve para importar y exportar. ADR 0017. |
| 2 | **Contenido detrás de `BlobStore`** | Direccionado por hash. Adaptador único sobre Postgres; S3 es fase 2. |
| 3 | **Entidad versionada + release** | Cada entidad tiene su semver. Publicar crea una release que fija todas las versiones exactas. La traza guarda solo el `release_id`. |
| 4 | **Una sola vía de cambio** | Toda modificación es una *propuesta* con el mismo ciclo, venga de una persona, del constructor o del detector (`origin`). |
| 5 | **Evaluación por agente sobre escenarios** | Lo que se mide es si el agente resuelve mejor los casos, sin importar qué entidad cambió. Los escenarios corren con el motor real, el LLM real y acciones contra un sandbox. |
| 6 | **Gate sin excepción manual** | Doble vara (ADR 0020, spec de evaluación §6): los guardarraíles de plataforma valen 0, cada métrica `gate` del agente no empeora frente a la base más allá de su margen de ruido y supera su piso si es nueva o cambió. Sin métrica principal ni puntaje compuesto. ADR 0018 (enmendado por el ADR 0020). |
| 7 | **Aprobación solo humana** | Un principal no humano (el constructor o el detector) nunca aprueba, publica, promueve ni revoca, aunque tenga el rol. Una persona sí puede aprobar su propia propuesta. |
| 8 | **El motor solo lee lo publicado** | La candidata vive en memoria durante la evaluación (§5.3). Las versiones no publicadas nunca entran en `entity_versions`. |

## 3. Modelo de datos

### 3.1 Tipos de entidad

- Los de M0 (`agent`, `flow`, `decision_model`, `policy`, `template`, `prompt`, `tool`, `language_detection`, `injection_ruleset`, `model_profile`, `knowledge_snapshot`).
- **`knowledge_snapshot`:** manifiesto inmutable de páginas (`path`, `hash`, `audience`, `status`, `lang`, vigencia, `source_refs`; ADR 0015). El manifiesto es una entidad normal y el texto de cada página vive en el `BlobStore`. **En la entrega** una release fija un snapshot sembrado por `import`, y las propuestas **no** crean ni modifican snapshots (lo rechaza `validate`).
- **`eval_suite`** (propio del paquete; no entra en M0 ni en una `Release`): suite de escenarios versionada y ligada a un agente (§6.1). Se guarda en `entity_versions` como las demás entidades y se crea o cambia por propuesta.

### 3.2 Tablas (prefijo `reg_`)

En el código cada tabla lleva el prefijo `reg_` (`reg_blobs`, `reg_aliases`, …) y vive en el `search_path` de la conexión; los nombres de abajo van sin prefijo.

**Inmutables** (el rol de la aplicación solo tiene `INSERT` y `SELECT`, y un trigger rechaza `UPDATE` y `DELETE`):

| Tabla | Columnas clave |
|---|---|
| `blobs` | `hash` PK (sha256 de `canonical_bytes`), `bytes` |
| `entity_versions` | `(kind, id, version)` PK, `content_hash` → `blobs`, `docs` (`VersionDocs`), `proposal_id`, `created_by`, `created_at` |
| `releases` | `release_id` PK, `release_hash`, `agent_id`, `base_release_id`, `proposal_id`, `published_by`, `published_at` |
| `release_entities` | `(release_id, kind, id, version)` |
| `release_eval_suites` | `(release_id, id)` PK, `kind = eval_suite`, `version`; FK a `entity_versions`. Las suites con que la release pasó el gate (`eval_suite_refs`, ADR 0020): metadato de gobierno que el motor no lee y vara vieja de la siguiente propuesta. Solo inserción |
| `approvals` | `proposal_id`, `candidate_hash`, `actor`, `decision` (`approved` o `rejected`), `reason`, `at`, `yardstick_loosened` (JSON: lo que la aprobación aceptó aflojar, `[]` si nada; columna añadida con `ALTER TABLE … ADD COLUMN IF NOT EXISTS`) |
| `eval_runs` | `eval_run_id` PK, `proposal_id`, `candidate_hash`, `base_release_id`, `suite_ref`, `report` (JSON, §6.4), `verdict` (`pass`, `fail` o `failed_infra`), `at` |
| `registry_events` | bitácora de auditoría (§12) |

**Mutables y controladas** (el rol de la aplicación nunca tiene `DELETE`; `release_status`, `aliases` y `agent_pause` tienen `SELECT`, `INSERT` y `UPDATE`, `publish_keys` solo `SELECT` e `INSERT`):

| Tabla | Qué cambia |
|---|---|
| `release_status` | `release_id → active` o `revoked`. Está separada de `releases` para que `releases` sea estrictamente inmutable. Solo cambia por `revoke`. |
| `aliases` | `(agent_id, alias) → release_id` (`staging`, `prod`). Solo cambia por `publish` (`staging`) y `promote`. |
| `alias_log` | Solo inserción: cada cambio de alias con actor, motivo y release anterior. |
| `agent_pause` | `agent_id` → `paused`, `release_id` (el de `prod` al pausar), `actor`, `at`. Una fila por agente que alguna vez se pausó; reanudar pone `paused = false` (no se borra). Solo cambia por `pause_agent` y `resume_agent` (rol aprobador, con step-up). Un agente en pausa sale del directorio de `recepcion` (`RegistryDirectory`); `prod` no cambia y los casos abiertos siguen. HTTP: `POST /v1/registry/agents/{id}/pause`, `POST …/resume`, `GET …/pause`. Eventos `paused` y `resumed` (no salen como `release.*`). |
| `proposals` | `proposal_id`, `agent_id`, `origin`, `state`, `rev`, `base_release_id`, `title`, `candidate_hash` (null hasta `freeze`), `created_by`. |
| `proposal_changes` | `(proposal_id, kind, id)` → contenido en borrador (JSON), `new_version`, `docs`. Se reescribe libremente mientras la propuesta está en `draft`. |

### 3.3 Documentación por versión

```python
class VersionDocs(BaseModel, frozen=True):
    description: str       # qué es y para qué sirve
    rationale: str         # por qué se hizo este cambio
    changelog: str         # qué cambió respecto de la versión anterior
```

Es una envoltura: no forma parte del contenido de la entidad ni del `content_hash`, y no cambia los tipos de M0. La escribe quien propone y la ve quien aprueba y quien consulta el linaje.

### 3.4 Versionado

- **Cascada:** si una entidad que cambia es referenciada con versión exacta por otra de la base, esa otra sube de patch automáticamente (repetido hasta el punto fijo), con `VersionDocs` generadas. `freeze` las devuelve en `Candidate.auto_bumped`.
- Quien propone fija `new_version` para cada entidad que cambia. `validate` exige que sea mayor que la versión de esa entidad en la base y que `(kind, id, version)` no exista ya en `entity_versions`. Una entidad nueva puede empezar en cualquier versión.
- En el borrador se permiten rangos (`^1`, `~1.2`) en las referencias entre entidades. `freeze` los reescribe a versiones exactas con `pin_release` de M1. El motor nunca resuelve rangos.

### 3.5 Hashes

- `content_hash` = sha256 de `canonical_bytes(entidad)` (M0).
- `release_id = rel-` + los primeros 16 caracteres de `candidate_hash`.
- `release_hash` = sha256 de `canonical_bytes` de la `Release` de M0 sin `id` ni `status`.
- `candidate_hash` = sha256 de `canonical_bytes({release_hash, [(kind, id, version, content_hash)] ordenado})` de la candidata. Todo lo que el evaluador y la persona aprobadora vieron queda atado a este hash.

## 4. Ciclo de vida de una propuesta

```
            put_draft
            ┌──────┐
            ▼      │
create ─► draft ───┘ ──freeze──► candidate ──evaluate(pass)──► evaluated ──approve──► approved ──publish──► published
            ▲                       │  ▲                          │   │                    │
            │                       │  └── evaluate(failed_infra) │   │                    │
            ├── evaluate(fail) ─────┘                             │   │                    │
            ├── reject ───────────────────────────────────────────┘   │                    │
            ├── reopen  (desde candidate, evaluated o approved) ──────┴────────────────────┤
            └── publish con base cambiada (proposal_stale) ────────────────────────────────┘
```

| Desde → hacia | Operación | Rol | Efecto |
|---|---|---|---|
| — → `draft` | `create_proposal(agent_id, origin, title)` | constructor | `base_release_id` = alias `staging` del agente (o null si el agente no tiene release) |
| `draft` → `draft` | `put_draft(changes, expected_rev)` | constructor | reescribe `proposal_changes` y sube `rev`. Si `rev` no coincide → `proposal_stale` |
| `draft` → `candidate` | `freeze()` | constructor | arma la candidata (§5.1) y ejecuta `validate` (§5.2). Con violaciones → `validation_failed` y sigue en `draft`. Sin violaciones → guarda `candidate_hash` |
| `candidate` → `evaluated` | `evaluate(suite_id, suite_version)` | constructor | síncrono (§6). `pass` → `evaluated`; `fail` → `draft` y responde `gate_failed`; `failed_infra` → sigue en `candidate` |
| `evaluated` → `approved` | `approve(candidate_hash, accept_yardstick_loosened)` | aprobador humano | exige una evaluación `pass` con ese hash. Si el hash es distinto → `candidate_changed`. Si la evaluación trae `yardstick_changes` y no se acepta → `loosening_not_accepted` |
| `evaluated` → `draft` | `reject(reason)` | aprobador humano | el motivo queda en `approvals` como entrada para iterar |
| `approved` → `published` | `publish()` | aprobador humano | §5.4 |
| `candidate`, `evaluated`, `approved` → `draft` | `reopen()` | constructor | borra `candidate_hash`; las evaluaciones y aprobaciones anteriores dejan de valer |

`validate()` también existe como operación de solo lectura en `draft`, para iterar sin congelar.

Reglas:

- Cualquier transición que no esté en la tabla → `illegal_transition` (`409`).
- Las propuestas son independientes. Si dos tocan el mismo agente, la segunda en publicar recibe `proposal_stale` (§5.4) y vuelve a `draft` con su base actualizada al `staging` vigente.
- Estado terminal: `published`. Los estados `abandoned` y `stale` como estados propios son de fase 2.

## 5. Candidata, validación y publicación

### 5.1 Armar la candidata

1. Construye un `AuthoringRegistry` de M1 en memoria con las entidades de la release base (desde `entity_versions`) más los cambios del borrador.
2. Declara la release del agente y la fija con `pin_release`. El resultado es una `Release` de M0 con referencias exactas y el conjunto exacto de entidades de su clausura.
3. Calcula los hashes (§3.5).

No escribe nada en las tablas publicadas.

### 5.2 Validación

Sobre la candidata, en memoria:

1. Gate G0 de M1 sobre cada flow de la clausura, y chequeos de M1 por agente.
2. Chequeos de release (spec general §6.2): dueño único por intención, `tools_allowed` cubre las tools de los flows, outcomes de `end` compatibles con el `mode` del agente, interrupciones válidas.
3. Versionado (§3.4).
4. Límites de tamaño y cantidad por entidad y por propuesta (evitan entidades desbocadas escritas por un agente). Los valores viven en la configuración del paquete.
5. La propuesta no crea ni modifica `knowledge_snapshot` (entrega).
6. Cada `eval_suite` del borrador pasa `suite_problems` (spec de evaluación §5): `REG-SUITE`, con el código del problema al inicio del mensaje. La suite elegida al evaluar pasa la misma comprobación (`validation_failed`) y pertenece al agente de la propuesta.

Devuelve `list[Violation]` (la de M1): `rule`, `entity`, `path` y un mensaje en lenguaje claro para personas no técnicas.

> **Reconciliado (2026-10-01):** el gate de este registry es el de la spec de evaluación (`docs/specs/2026-09-30-evaluacion-y-metricas-design.md` §6, ADR 0020): las métricas las declara cada agente (`Agent.metrics`), puede haber N métricas `gate` evaluadas por separado y se aplica una doble vara (la de la base y la de la candidata). El rechazo de un agente sin suite al validar no está cableado (spec de evaluación §13.14).

### 5.3 `SnapshotRegistry`

Es la implementación de `RegistryPort` en memoria y de solo lectura, construida desde una candidata (una `Release` más sus entidades). Solo la usa el evaluador. El `RegistryPort` de producción (`PostgresRegistry`) nunca ve candidatas.

### 5.4 `publish` (una transacción)

0. `pg_advisory_xact_lock(hashtext(agent_id))` (`RegistryTx.lock_agent`): `FOR UPDATE` sobre un alias que aún no existe no bloquea, así que sin este lock dos primeras publicaciones (o importaciones) del mismo agente correrían a la vez. `import` toma el mismo lock.
1. `SELECT … FOR UPDATE` sobre `aliases(agent_id, 'staging')`. Si no apunta a `base_release_id` → `proposal_stale`, y la propuesta vuelve a `draft` con la base actualizada (hay que congelar y evaluar de nuevo).
2. Rearma la candidata y verifica que su hash sea igual a `candidate_hash`; si no, `candidate_changed`. Si al rearmarla ya no es válida (p. ej. otra publicación ocupó una versión del borrador, `REG-VERSION-TAKEN`), también es `candidate_changed`, con las violaciones en el detalle (sin valores de entrada). Lo mismo vale en `evaluate`.
3. Verifica que exista una aprobación vigente para ese hash.
4. Inserta los blobs y las `entity_versions` nuevas, `releases`, `release_entities` y `release_status = active`.
5. Mueve `staging`, escribe `alias_log` y `registry_events`, y la propuesta pasa a `published`.
6. `Idempotency-Key`: si se reintenta con la misma clave, devuelve la misma release sin escribir nada.

Cualquier falla revierte todo.

### 5.5 Operaciones directas

- `promote(agent_id, alias, release_id)`: apunta un alias a una release `active` del agente. Volver atrás es promover una release anterior; no necesita gate nuevo porque esa release ya lo pasó.
- `revoke(release_id, reason)`: `active → revoked`, inmediato y sin gate. Una release a la que apunta `prod` no se puede revocar sin antes promover otra (`illegal_transition`). Los runs abiertos fijados a una release revocada escalan con `release_revoked` (M4).

### 5.6 Importación y exportación

- `import <carpeta>`: carga YAML con `load_registry` de M1, fija cada release declarada con `pin_release` y la inserta directamente como publicada, con `origin = import`, apuntando `staging` y `prod`. **Solo se usa para sembrar** un agente que aún no tiene release; si el agente ya tiene una, falla. Exige rol `aprobador` y un principal humano.
- `export`: escribe una entidad o una release a YAML con el formato del loader de M1, para usarla como punto de partida de un borrador manual. `export` seguido de `import` conserva los `content_hash`.

## 6. Evaluación

### 6.1 Formato de `eval_suite`

```yaml
id: disputas-suite
version: 1.0.0
agent_id: soporte-tarjetas
repetitions: 3                 # k corridas por escenario sin `repetitions` propio
thresholds:                    # por id de métrica `gate` o `guardrail` del agente
  tasa_resolucion: {noise_margin: 0.05, floor: 0.70}
scenarios:
  - id: disputa-cargo-duplicado
    repetitions: 5             # opcional, 1..10; sin él, las de la suite
    principal: {id: cust-001, attrs: {country: CO}}   # sintético; tipo customer
    steps:
      - {op: start}
      - {op: turn, text: "no reconozco un cargo de ciento veinte dólares"}
      - {op: confirm, answer: "yes"}
    seed:
      tools:
        buscar_transacciones: [{status: ok, result: [{transaction_id: tx-1, amount: "120.50"}]}]
    sensitive_values: ["4111-1111"]
    expect: {outcome: resolved, actions_verified: [radicar_pqr], escalated: false}
    assertions:                # opcional, hasta 20: eventos que deben (o no) aparecer
      - {event: engine.escalated, expect: none}
```

`noise_margin` y `floor` ya no son campos de la suite: cada métrica los declara en `thresholds` (spec de evaluación §5). Un escenario sin `source` es `scripted`; la fuente `dataset` está desactivada (`dataset_source_disabled`). El formato completo está en `agent_core/registry/suite.py`.

Los escenarios de negocio de la demo los escribe otra persona del equipo; este paquete define el formato y su validación.

### 6.2 `ScenarioEvaluator` (implementa `EvalPort`)

Corre hasta tres mediciones (vara nueva sobre la candidata; vara vieja sobre la base y sobre la candidata; la vara vieja solo existe si la base registró su suite), cada una con su suite congelada (spec de evaluación §6.1):

1. Compone el motor a través de un `ScenarioHarness` (protocolo del paquete) que implementa `agent_core.composition`: un `SnapshotRegistry` construido desde la candidata o desde la release base publicada (así la base no depende de que un alias se mueva durante la evaluación), el LLM gateway real y las tools apuntadas al sandbox (§6.3).
2. Corre cada escenario `repetitions` veces. Cada corrida recibe su propio entorno de sandbox sembrado con el `seed` del escenario.
3. **Califica desde los eventos del motor.** Una corrida pasa si cumple su `expect` y todas sus `assertions`; un escenario pasa si pasa en todas sus repeticiones. Las métricas del agente (`Agent.metrics`) se calculan con el evaluador en memoria del DSL (`evaluation/metric_eval.py`) sobre los eventos de la corrida de la suite.
4. **Guardarraíles de plataforma** (conteos sobre los eventos, ids `platform_*`): `platform_pii_leak`, `platform_unverified_success_claim`, `platform_unverified_write` y `platform_unapproved_knowledge_citation` (definidos en la spec de evaluación §7). Los emiten M2, M3, M6 y M8.
5. Corre en paralelo con un tope de concurrencia configurable.
6. Una falla del gateway o de un proveedor de decisión (JEV, classifier) durante una corrida (la detecta una sonda del harness, porque el motor la absorbe) o del sandbox → `failed_infra`, igual que el tiempo vencido. Toda la evaluación queda `failed_infra`. Nunca hay un pase parcial.

Costo orientativo: hasta 3 mediciones × N escenarios × k corridas (si la suite vieja es igual a la nueva, la candidata se corre una vez y son 2). Con 10 escenarios y k = 3, son hasta 90 conversaciones por evaluación.

### 6.3 Sandbox

Las acciones de la evaluación corren de verdad (incluido el read-back de `verify`) contra un entorno aislado, lo más cercano posible a producción:

```python
class SandboxPort(Protocol):                                       # lo implementa la unidad 3
    def provision(self, seed: SandboxSeed, target: EvalTarget) -> SandboxHandle        # entorno aislado con el estado inicial
    def tools(self, h: SandboxHandle) -> ToolExecutor              # atributo `is_sandbox = True`; las tools de esta corrida
    def teardown(self, h: SandboxHandle) -> None
```

- Cada corrida tiene su propio entorno. Base y candidata parten del mismo `seed`.
- **Barrera dura:** el evaluador se niega a correr si el `ToolExecutor` no tiene el atributo `is_sandbox` verdadero. El sandbox usa solo datos sintéticos y nunca comparte credenciales ni endpoints con producción.
- **Respaldo de la entrega:** `LocalSandbox` en `agent-core` implementa el puerto **en memoria**: respuestas sembradas por tool (`seed.tools`), un entorno aislado por corrida y sin Postgres. Sustituirlo por el de la unidad 3 no toca el evaluador.

### 6.4 Veredicto y reporte

Doble vara: spec de evaluación §6 (guardarraíles de plataforma en 0, N métricas `gate` por separado, sin métrica principal ni puntaje compuesto). Es `fail` si cualquier elemento falla; `failed_infra` no es veredicto.

`EvalReport` (`evaluation/report.py`): `verdict`, `items: list[GateItem]` (cada métrica, escenario o guardarraíl con su valor, `base_value`, `noise_margin` y `floor` por separado, y el motivo si falló), `runs: GateRuns` (las mediciones `base_on_old`, `cand_on_old` y `cand_on_new`), `results` (el resultado por escenario, corrida y repetición, para que quien aprueba vea *qué caso mejoró o empeoró*), `judge_notes` si las hay, `yardstick_changes` (lo que la propuesta afloja en la vara, spec de evaluación §6.2) y `detail`.

**Juez LLM opcional:** un hook `Judge` que recibe las transcripciones y devuelve notas por escenario. Va al reporte como información y **no entra al veredicto**. Lo implementa otra persona del equipo; sin juez, el reporte omite esa sección.

**No hay excepción manual.** Un gate fallido devuelve la propuesta a `draft`; nadie puede aprobar sobre él.

## 7. Interfaz pública

### 7.1 `RegistryPort` (runtime, sin cambios en M0)

`resolve_release`, `release_status` y `get`. Tiene dos implementaciones en el paquete: `PostgresRegistry` (producción) y `SnapshotRegistry` (evaluación). Ambas pasan la suite de contrato de `InMemoryRegistry`.

`PostgresRegistry`: `resolve_release` sigue el alias (`prod` por defecto, `staging` si el selector lo pide) y cachea la release por `release_id`. `release_status` se consulta con un TTL corto para que una revocación surta efecto rápido. `get` verifica el hash al leer; si no coincide, `IntegrityError` y el motor escala.

### 7.1b Directorio de agentes (ADR 0021)

El directorio de transferencias es el puerto `AgentDirectory` (M0, `members(directory) -> [(release_id, Agent)]`, ordenado por `agent.id`). Lo sirven los agentes **publicados**: los que tienen el alias `prod` apuntando a una release `active` y cuya ficha de ruteo (`Agent.routing`) lleva la etiqueta pedida. No filtra por principal; la elegibilidad es de quien lo llama.

- `RegistryTx.aliases_named(alias) -> [(agent_id, release_id)]`, ordenado por `agent_id`, en la memoria y en Postgres (`reg_aliases`). Es el único cambio del store.
- `RegistryDirectory(store, registry, releases)` implementa el puerto: lee los alias `prod` en una transacción, descarta las releases revocadas (`release_status`), resuelve la versión del agente en la release (`releases`, un `Callable[[str], Release]` como `EngineDeps.releases`, normalmente `PostgresRegistry.release`) y lo lee con `RegistryPort.get`. Una release `prod` revocada no aparece, así que `directory_hash` cambia al publicar, promover o revocar.
- `InMemoryDirectory` (`testing/fakes`) cumple la misma suite de contrato (`tests/contracts/test_directory_contract.py`); la de Postgres está en `tests/integration/test_registry_postgres.py`.
- La tool `directory/list@1.0.0` (riesgo `read`) no vive aquí: la sirve `composition` (§18, fila 11).

### 7.2 `RegistryService` (gestión)

```python
class RegistryService:
    # Construcción (rol constructor)
    def create_proposal(self, actor, agent_id, origin, title, *, idempotency_key=None, audit=None) -> Proposal
    def put_draft(self, actor, proposal_id, changes: list[EntityDraft], expected_rev: int, *, idempotency_key=None, audit=None) -> Proposal
    def validate(self, actor, proposal_id) -> ValidationReport
    def freeze(self, actor, proposal_id, *, idempotency_key=None, audit=None) -> Candidate
    def evaluate(self, actor, proposal_id, suite_id: str, suite_version: str | None = None, *, idempotency_key=None, audit=None) -> EvalReport
    def reopen(self, actor, proposal_id, *, idempotency_key=None, audit=None) -> Proposal

    # Decisiones (rol aprobador, principal humano)
    def approve(self, actor, proposal_id, candidate_hash: str, *, accept_yardstick_loosened=False) -> Approval
    def reject(self, actor, proposal_id, reason: str) -> Proposal
    def publish(self, actor, proposal_id, idempotency_key: str) -> ReleaseDetail
    def promote(self, actor, agent_id, alias, release_id) -> AliasChange
    def revoke(self, actor, release_id, reason: str) -> ReleaseDetail
    def import_seed(self, actor, root: Path) -> list[ReleaseDetail]  # rechaza (validation_failed) y no guarda nada si una suite de la semilla tiene problemas (`suite_problems`) o su agente no tiene release en la semilla

    # Lecturas (cualquier builder autenticado)
    def get_proposal(self, proposal_id) -> ProposalDetail            # cambios, candidata, último reporte y `review` (spec de evaluación §8.5)
    def get_entity(self, kind, entity_id, version: str | None) -> EntityVersion
    def get_write(self, idempotency_key) -> WriteRecord | None       # readback de las escrituras con clave
    def list_versions(self, kind, entity_id) -> list[VersionSummary]
    def get_release(self, release_id) -> ReleaseDetail
    def diff_releases(self, a: str, b: str) -> ReleaseDiff           # refs añadidas, quitadas y cambiadas, con VersionDocs
    def export(self, target: EntityRef | str) -> dict[str, bytes]    # ruta → YAML
    def lineage_for_run(self, actor, run_id) -> RunLineage
```

`origin`: `manual`, `builder_chat`, `auto_detect` o `import`.

### 7.3 Puertos que consume

```python
class BlobStore(Protocol):
    def put(self, data: bytes) -> str                                # devuelve el hash
    def get(self, hash: str) -> bytes                                # verifica el hash; si no coincide, IntegrityError

class EvalPort(Protocol):
    def run(self, request: EvalRequest) -> EvalReport               # EvalRequest(candidate, new: Yardstick, base, old: Yardstick | None)

class Judge(Protocol):                                               # opcional
    def score(self, suite: EvalSuite, transcripts: list[ScenarioTranscript]) -> list[JudgeNote]
```

Más `SandboxPort` (§6.3) y, para el linaje, la lectura del `release_id` de un run por la interfaz pública de M11.

### 7.4 API REST (`/v1/registry`)

Se monta en la app FastAPI de M9 como `ApiExtension` (`ApiDeps.extensions`, por defecto vacío) con la misma autenticación JWS (la función `authenticate` que M9 entrega a la extensión), `problem+json` y `trace_id` en toda respuesta.

**Contrato publicado (2026-10-02).** `contracts/registry-openapi.json` (generado por `agentcore contracts`, verificado con `--check`) describe estas rutas con esquemas de éxito, `problem+json` y `bearerAuth`. `Idempotency-Key` es opcional (máx. 255) en crear, editar borrador, congelar, reabrir y evaluar, y obligatoria en publicar; un reintento con la misma clave devuelve el mismo resultado y otro cuerpo da `409 idempotency_conflict`. Los topes de cuota y el tope de costo no cambian.

**Listado (2026-10-02).** `GET /v1/registry/proposals?agent_id=&state=&created_by=&limit=&offset=` devuelve `{items, total}`, más recientes primero (`updated_at`, luego id); `limit` por defecto 50 y tope 200; lectura de cualquier `builder`. Filtra en el servicio sobre `RegistryTx.list_proposals()` (en Postgres, un `SELECT` de `reg_proposals`; sin índice, aceptable mientras haya cientos de propuestas). `GET /writes/{key}` no se publica: `DraftWrite` no guarda quién escribió, así que cualquier `builder` podría leer por clave el run y el principal de otro. Hace falta un dueño en `DraftWrite` antes.

**Vigencia (2026-10-02).** Con el verificador del staff (`--staff-keys`) la API no pasa por la puerta de M9, y el verificador solo comprueba la firma. Por eso `registry_extension` exige un `Clock` junto al verificador y rechaza `exp <= now` con `401 principal_expired` en todas las rutas, igual que M9; sin reloj falla al construirse. Antes de este cambio una credencial vencida seguía operando el registry.

| Método y ruta | Operación |
|---|---|
| `POST /proposals` · `GET /proposals/{id}` | crear · ver |
| `PUT /proposals/{id}/draft` | `put_draft` |
| `POST /proposals/{id}/validate` · `/freeze` · `/reopen` | construir |
| `POST /proposals/{id}/evaluate` | `evaluate` (síncrono; devuelve `EvalReport`) |
| `POST /proposals/{id}/approve` (cuerpo: `candidate_hash`, `accept_yardstick_loosened` opcional) · `/reject` | decidir |
| `POST /proposals/{id}/publish` | `publish` (exige `Idempotency-Key`) |
| `POST /aliases/{agent}/{alias}` | promover (`aprobador`) |
| `POST /releases/{id}/revoke` | revocar (`admin`) |
| `GET /entities/{kind}/{id}` (`?version=` opcional) | leer entidades; el `id` admite `/` (p. ej. `t/saludo`), por eso la versión va como query |
| `GET /releases/{id}` · `GET /releases/{a}/diff/{b}` | leer releases |
| `GET /runs/{id}/lineage` | linaje |

**Errores** (enum del paquete; no se añaden a `ProblemCode` de M0):

| Código | HTTP | Cuándo |
|---|---|---|
| `validation_failed` | 422 | `freeze` o `validate` con violaciones (incluye la lista) |
| `gate_failed` | 409 | `evaluate` con veredicto `fail`; incluye el `EvalReport` y la propuesta vuelve a `draft` |
| `proposal_stale` | 409 | `expected_rev` desactualizado o `staging` movido antes de publicar |
| `candidate_changed` | 409 | el hash aprobado o evaluado no es el de la candidata vigente |
| `illegal_transition` | 409 | operación no permitida en el estado actual |
| `forbidden_role` | 403 | falta el rol, el principal no es `builder` o no es humano donde se exige, o un borrador edita un guardarraíl de plataforma (métrica `platform_*` o su umbral) |
| `step_up_required` | 403 | una operación de `aprobador` o `admin` sin autenticación reforzada |
| `integrity_error` | 500 | el hash de un contenido no coincide |
| `loosening_not_accepted` | 409 | la evaluación trae `yardstick_changes` y la aprobación no los acepta (`accept_yardstick_loosened`); el cuerpo lista los cambios |
| `idempotency_conflict` | 409 | una clave de idempotencia reutilizada con otro contenido |
| `quota_exceeded` | 429 | tope del constructor autónomo (propuestas por día o evaluaciones por propuesta) |

### 7.5 CLI (`agentcore registry …`)

`import`, `export`, `propose`, `draft <propuesta> <archivos-o-carpeta> --rev N` (YAML con el formato de M1; `--rev` es la revisión esperada de la propuesta), `validate`, `freeze`, `reopen`, `show`, `evaluate`, `approve` (con `--accept-yardstick-loosened`), `reject`, `publish`, `promote`, `revoke`, `diff` y `lineage`. Es un cliente delgado sobre `RegistryService`, sin lógica propia.

Formato de cada archivo de `draft` (una entidad por archivo YAML; una carpeta se recorre de forma recursiva): `{kind, docs: {description, rationale, changelog}, content: {...}}`, donde `content` es la entidad en el formato de M1. Las opciones globales son `--dsn` (o `AGENTCORE_REGISTRY_DSN`), `--credential` (o `AGENTCORE_CREDENTIAL`, el JWS del principal), `--verifier` y `--harness` (rutas `modulo:atributo`). **Son obligatorios y no tienen valor por defecto**: los dobles de PRUEBA de `testing.registry_demo` (claves y mundo sintéticos del repo) solo se usan si `AGENTCORE_ALLOW_DEMO=1` está en el entorno; así un `--dsn` de una base real nunca se combina por descuido con un verificador de prueba. Una ruta que no se puede importar termina con código 2 y un mensaje claro. La CLI evalúa con `max_workers=1` (el harness comparte reloj e ids entre corridas). `lineage` por CLI construye el servicio sin `RunReleaseReader` y responde `not_found` hasta que se cablee un lector de runs.

El ciclo manual queda así: `export` → editar en el editor → `draft` → `freeze` → `evaluate` → `approve` → `publish`.

## 8. Roles y seguridad

| Rol | Puede |
|---|---|
| (cualquier `builder` autenticado) | leer entidades, releases, diffs, propuestas y reportes. Un `customer`, un `advisor` o un `service` no consultan el registry |
| `constructor` | crear propuestas, editar borradores, validar, congelar, evaluar, reabrir y leer el linaje de un run |
| `aprobador` | aprobar, rechazar, publicar y promover (a `staging` y a `prod`) |
| `admin` | revocar una release e importar la semilla |

Quién es quién (decisión 2026-09-30, tema #14): el **supervisor** es una persona con `constructor` y `aprobador`; el **administrador** es una persona con `constructor`, `aprobador` y `admin`; el **agente constructor** autentica con una credencial de servicio propia que solo puede traer `constructor` y nunca `attrs.actor = "human"`. Todos son `builder`: no hay un tipo de principal nuevo.

- **Los roles son acumulables.** Una persona con `constructor` y `aprobador` recorre el ciclo completo sola y puede aprobar su propia propuesta. `approvals` y el linaje lo registran ("construido por X, aprobado por X").
- **Barrera de actor humano, verificada en el servidor:** las operaciones de `aprobador` exigen además un principal humano. En M0 no existe un tipo `agent` (ADR 0006): el agente constructor autentica como `builder` con su **propia credencial** de rol `constructor`, y el principal del run viaja solo como actor de auditoría, nunca como fuente de permisos (ADR 0019). Una persona con rol `aprobador` que chatea con el constructor no le presta ese rol. Un principal es humano solo si su credencial firmada trae `attrs.actor = "human"`; si falta o tiene otro valor, se trata como no humano (falla cerrado) y recibe `forbidden_role` aunque tenga el rol. Nunca se confía en el prompt del agente.
- **Solo un `builder`:** `require_builder` se aplica a toda ruta y operación, lecturas incluidas; un principal de otro tipo recibe `forbidden_role` aunque su credencial traiga los roles. La lista de roles es cerrada (`constructor`, `aprobador`, `admin`): un rol desconocido no concede nada.
- **`admin` y `aprobador` exigen persona y autenticación reforzada:** además de `attrs.actor = "human"`, el principal debe traer `auth.level = step_up` (ADR 0010; el OTP de la demo es simulado). Con un nivel menor el servicio responde `step_up_required`. Construir (`constructor`) solo exige una sesión.
- **Quién firma:** las credenciales del staff (supervisor, administrador y bot) las emite un emisor propio, con una clave y un `kid` distintos de los del emisor de clientes y asesores. La API del registry se monta con un `authenticate` construido solo con esas claves, así que una credencial de cliente ni siquiera verifica. Los roles salen de los grupos del proveedor de identidad del staff al emitir la credencial; el núcleo solo los valida. En la demo los emite `TestStaffIssuer`, etiquetado como de prueba.
- **Contenido no confiable:** todo lo que propone un agente o un LLM se valida contra el esquema estricto, nunca se ejecuta y tiene límites de tamaño y cantidad.
- **Lo que el constructor lee** (trazas, documentación, páginas) es dato, no instrucción (ADR 0008, M12).
- **Datos:** las suites y los `seed` del sandbox usan solo datos sintéticos. La fuente `dataset` para datos reales está diseñada y desactivada hasta un ADR aparte (ADR 0020).
- **Datos:** las suites y los `seed` del sandbox usan solo datos sintéticos.
- **Topes del constructor autónomo (tema #16):** `create_proposal` con `origin = auto_detect` falla con `quota_exceeded` (429) si ya hay 10 creadas en las últimas 24 h (ventana móvil con el `Clock`), y `evaluate` sobre una propuesta `auto_detect` falla igual si ya tiene 20 evaluaciones (incluidas `failed_infra`). Se aplican en el servicio. El tope de costo por propuesta está diferido.

## 9. Linaje por ejecución

```python
class EntityInRelease(BaseModel, frozen=True):
    kind: EntityKind
    ref: EntityRef                       # id@version exacta
    content_hash: str
    docs: VersionDocs
    changed_vs_base: bool                # frente a base_release_id

class RunLineage(BaseModel, frozen=True):
    run_id: str
    release_id: str
    entities: list[EntityInRelease]
    knowledge_snapshot: EntityRef | None
    proposal_id: str | None
    eval_verdict: EvalVerdict | None     # resumen del reporte que habilitó la publicación
    built_by: str | None
    approved_by: str | None              # actor humano
    published_at: datetime
```

- La traza ya guarda `release_id`; el motor no cambia.
- Como las releases son inmutables, el resultado no cambia con el tiempo.
- En la entrega, `lineage_for_run` exige el rol `constructor` (decisión 2026-09-30, auditoría: expone autoría y aprobación, y no debe leerlo cualquier principal autenticado, como un cliente). Un rol `lector` explícito y la autorización por dueño del run (M9) son de fase 2.

## 10. Comportamiento en runtime

- Un run fija su release al iniciar y no la cambia.
- El motor no accede a `proposals`, `proposal_changes`, `eval_runs` ni a candidatas.
- Si un contenido no coincide con su hash, `get` lanza `IntegrityError` y el motor escala; nunca sirve contenido dudoso.

## 11. Fallas

| Falla | Comportamiento |
|---|---|
| Postgres falla a mitad de `publish` | Se revierte todo. Reintento seguro con la misma `Idempotency-Key`. |
| Dos `publish` sobre el mismo agente | Bloqueo sobre el alias `staging`; el segundo recibe `proposal_stale`. |
| LLM, sandbox o tiempo vencido durante `evaluate` | `failed_infra`; la propuesta sigue en `candidate` y se puede reintentar. |
| `expected_rev` desactualizado | `proposal_stale`. |
| La candidata cambió después de evaluar o aprobar | `candidate_changed`. |
| Hash de contenido distinto al guardado | `IntegrityError`. |

## 12. Eventos y observabilidad

`registry_events` es de solo inserción y está separada de la cadena de los runs:

`proposal_created`, `draft_updated`, `frozen`, `evaluated`, `approved`, `rejected`, `reopened`, `published`, `promoted`, `revoked`, `imported`.

Cada evento lleva `actor`, `principal_type`, `origin`, `proposal_id`, `candidate_hash` y la marca de tiempo del `Clock`. Solo referencias y hashes, nunca el contenido de las entidades.

## 13. Pruebas

Las que necesitan Postgres van en `tests/integration/`; el resto usa dobles en memoria.

| ID | Caso |
|---|---|
| T-REG-01 | `UPDATE` o `DELETE` sobre las tablas inmutables falla por permisos y por trigger |
| T-REG-02 | `publish` con una falla inyectada a mitad no deja nada parcial; reintentar con la misma `Idempotency-Key` devuelve la misma release |
| T-REG-03 | `put_draft` con `expected_rev` viejo → `proposal_stale` |
| T-REG-04 | `freeze` con una violación de M1 → `validation_failed` con la violación legible; la propuesta sigue en `draft` |
| T-REG-05 | `new_version` repetida o no mayor que la de la base → violación |
| T-REG-06 | Una entidad por encima de los límites de tamaño o cantidad se rechaza |
| T-REG-07 | Una transición no permitida → `illegal_transition` |
| T-REG-08 | Gate: un guardarraíl que empeora falla aunque una métrica `gate` mejore (= T-EVAL-05) |
| T-REG-09 | Gate: cada métrica `gate` dentro de su margen pasa, fuera falla (= T-EVAL-06) |
| T-REG-10 | Gate: sin base, se compara contra los `floor` (= T-EVAL-13) |
| T-REG-11 | `failed_infra` (LLM o sandbox caídos) no permite aprobar y deja reintentar |
| T-REG-12 | `approve` con un hash distinto del vigente → `candidate_changed`; `reopen` invalida la evaluación y la aprobación |
| T-REG-13 | Un principal no humano (sin `attrs.actor = "human"`), incluso con rol `aprobador`, no puede aprobar, rechazar, publicar, promover, revocar ni importar (`forbidden_role`) |
| T-REG-14 | Una persona con `constructor` y `aprobador` completa el ciclo sola y `approvals` lo registra |
| T-REG-15 | `publish` con `staging` movido después de crear la propuesta → `proposal_stale` y la propuesta vuelve a `draft` con la base nueva |
| T-REG-16 | Dos `publish` concurrentes sobre el mismo agente: solo uno gana |
| T-REG-17 | `PostgresRegistry` y `SnapshotRegistry` pasan la suite de contrato de `RegistryPort` |
| T-REG-18 | Por `PostgresRegistry` nunca se ve una candidata ni un borrador |
| T-REG-19 | Un contenido con hash alterado → `IntegrityError` al leerlo |
| T-REG-20 | Revocar: los runs nuevos no resuelven la release y los abiertos escalan con `release_revoked`; no se revoca la release de `prod` |
| T-REG-21 | `lineage_for_run` coincide exactamente con las entidades de la release del run, con sus `VersionDocs` y `changed_vs_base` |
| T-REG-22 | `diff_releases` coincide con `release_entities` |
| T-REG-23 | Evaluador: base y candidata parten del mismo `seed` y cada corrida tiene su propio entorno |
| T-REG-24 | El evaluador se niega a correr con un `ToolExecutor` (el que entrega `SandboxPort.tools`) no marcado con `is_sandbox = True` |
| T-REG-25 | Calificación: `expect` se evalúa desde los eventos, y los guardarraíles de plataforma y las aserciones se cuentan bien con eventos sintéticos |
| T-REG-26 | `import` de la carpeta YAML de la demo crea releases publicadas; `export` seguido de `import` conserva los `content_hash` |
| T-REG-27 | **E2E:** `import` → propuesta (cambio de prompt) → `freeze` → `evaluate` (`LocalSandbox`, LLM falso determinista) → `approve` → `publish` → un run nuevo usa la release nueva → el linaje muestra la entidad cambiada con su `VersionDocs` |
| T-REG-28 | Matriz de permisos por perfil: solo un `builder` opera el registry (lecturas incluidas); el bot construye pero nunca decide aunque su credencial traiga los roles; el supervisor construye, aprueba, publica y promueve a `prod` pero no revoca ni importa; el administrador hace todo; aprobar y revocar exigen `step_up`; el verificador del staff rechaza credenciales del emisor de clientes |

## 14. Definición de terminado

- [x] `RegistryService`, `BlobStore`, `EvalPort`, `SandboxPort`, `Judge`, `PostgresRegistry` y `SnapshotRegistry` exportados y tipados (`agent_core/registry/__init__.py`; `mypy` strict en verde).
- [x] T-REG-01 a T-REG-27 en verde (suite completa con Postgres: 3909 pasaron, 1 omitida por `AGENT_CORE_PERF`, ajena al registry; al 2026-10-02). Trazabilidad abajo.
- [x] `lint-imports`, `mypy` y `ruff` en verde.
- [x] API montada en M9 (`registry_extension`, `ApiDeps.extensions`) y CLI (`agentcore registry …`).
- [x] La composición del motor usa `PostgresRegistry` (`agentcore serve`, 2026-09-30, tema #13). `serve --registry-api` monta `registry_extension` con evaluador real y el verificador del staff (`registry_extension(service, verifier)`), spec `2026-09-30-serve-registry-api-design.md`.
- [x] `contracts/` regenerado (`agentcore contracts --check` en verde).
- [x] Sin TODO sin issue (no hay `TODO` en `agent_core/`, `testing/` ni `tests/`).
- [x] Gate con doble vara integrado (2026-10-01): `suite.py`, `evaluation/{metric_eval,scoring,gate,yardstick,evaluator}.py`; T-EVAL según la spec de evaluación §15 (pendientes allí: T-EVAL-04, el compilador SQL de T-EVAL-14 y el rechazo en `validated` de T-EVAL-11).

### Trazabilidad de pruebas

| T-REG | Prueba |
|---|---|
| 01 | `tests/integration/test_registry_postgres.py::test_immutable_tables_reject_update_and_delete` |
| 02 | `tests/registry/test_service_decide.py::test_publish_failure_mid_way_leaves_nothing`, `tests/integration/test_registry_postgres.py::test_publish_failure_mid_way_leaves_nothing` |
| 03 | `tests/registry/test_service_build.py::test_put_draft_with_stale_rev_fails` |
| 04 | `tests/registry/test_service_build.py::test_freeze_with_violation_keeps_draft` |
| 05 | `tests/registry/test_validation.py::test_version_not_greater_than_base_is_violation` |
| 06 | `tests/registry/test_validation.py::test_entity_over_size_limit_is_violation` |
| 07 | `tests/registry/test_service_build.py::test_illegal_transitions` |
| 08 | `tests/registry/test_gate.py::test_a_worse_guardrail_fails_even_if_a_gate_metric_improves` (T-EVAL-05) |
| 09 | `tests/registry/test_gate.py::test_each_gate_metric_is_judged_on_its_own` (T-EVAL-06) |
| 10 | `tests/registry/test_gate.py::test_no_base_fails_when_a_floor_is_missing_or_not_met`, `::test_no_base_passes_when_every_metric_meets_its_floor` (T-EVAL-13) |
| 11 | `tests/registry/test_service_decide.py::test_failed_infra_keeps_candidate_and_blocks_approval`, `tests/registry/test_evaluator.py::test_infra_failure_is_failed_infra` |
| 12 | `tests/registry/test_service_decide.py::test_approve_with_other_hash_is_candidate_changed`, `::test_reopen_invalidates_approval` |
| 13 | `tests/registry/test_service_decide.py::test_non_human_or_non_approver_cannot_decide`, `tests/registry/test_http.py::test_bot_gets_forbidden_role_problem` |
| 14 | `tests/registry/test_service_decide.py::test_human_completes_cycle_alone` |
| 15 | `tests/registry/test_service_decide.py::test_publish_with_moved_staging_is_stale_and_rebases` |
| 16 | `tests/registry/test_service_decide.py::test_two_proposals_same_agent_second_publish_is_stale`, `tests/integration/test_registry_postgres.py::test_second_publish_on_same_agent_is_stale` |
| 17 | `tests/integration/test_registry_postgres.py::test_postgres_registry_passes_contract`, `tests/contracts/test_registry_contract.py` (`InMemoryRegistry` y `SnapshotRegistry`, lista `CHECKS` completa; la misma lista corre contra `PostgresRegistry`) |
| 18 | `tests/integration/test_registry_postgres.py::test_candidates_and_drafts_are_invisible` |
| 19 | `tests/registry/test_memory_store.py::test_blob_round_trip_and_integrity`, `tests/integration/test_registry_postgres.py::test_tampered_blob_raises_integrity_error` |
| 20 | `tests/registry/test_service_decide.py::test_promote_and_revoke`, `tests/integration/test_registry_postgres.py::test_revoked_release_not_resolved_for_new_runs` |
| 21 | `tests/registry/test_service_reads.py::test_lineage_matches_release_exactly` |
| 22 | `tests/registry/test_service_reads.py::test_diff_matches_release_entities` |
| 23 | `tests/registry/test_evaluator.py::test_each_run_gets_its_own_sandbox`, `tests/registry/test_local_sandbox.py::test_each_provision_is_isolated` |
| 24 | `tests/registry/test_evaluator.py::test_refuses_non_sandbox_tools` |
| 25 | `tests/registry/test_scoring.py::test_resolved_with_verified_action_passes` |
| 26 | `tests/registry/test_yaml_io.py::test_import_demo_creates_published_release_with_both_aliases`, `::test_export_then_load_keeps_content_hashes` |
| 27 | `tests/integration/test_registry_postgres.py::test_end_to_end_prompt_change` |
| 28 | `tests/registry/test_roles_matrix.py`, `tests/registry/test_http_real_app.py::test_customers_and_advisors_cannot_even_read_the_registry`, `tests/registry/test_staff_issuer.py` |

## 15. Cambios en otros módulos

Todos **aditivos**.

- **M0** (aplicado el 2026-09-29, `SCHEMA_VERSION` 0.2.0): `EntityKind.knowledge_snapshot`, `KnowledgeSnapshot` y `Release.knowledge_snapshot`. No cambia `RegistryPort`, `EngineEvent` ni `ProblemCode`.
- **M0:** `IdKind.proposal` y `IdKind.eval_run` (`SCHEMA_VERSION` 0.5.0).
- **M1:** exporta `entity_ref_sites`, `RefSite` y `kind_of`.
- **M1** (aplicado el 2026-09-29, rev. 3): `ReleaseDecl.knowledge`, `pin_release` con snapshot, `load_registry` con manifiestos. **Verificado:** `flows/__init__.py` exporta todo lo que el registry reutiliza (validación de flow, chequeos por agente y de release, `pin_release`, `Violation`, `AuthoringRegistry` construible en memoria) y existe un volcado a YAML para `export`.
- **M3:** sin cambios (el sandbox entra como `ToolExecutor`).
- **M9:** `ApiDeps.extensions`: cada extensión recibe la app y un `authenticate(request, authorization)`.
- **M11:** expone por su interfaz pública la lectura del `release_id` de un run.
- **`composition`:** una fábrica que compone el motor con un `RegistryPort` y un `ToolExecutor` dados (la usan el evaluador y la API).
- **`.importlinter`:** `registry` solo usa `domain`, `ports` y la interfaz pública de `flows`; **no** importa `composition`. La dependencia va al revés: `registry.evaluation.ports` define `ScenarioHarness` y `composition` lo implementa (y cablea el servicio), por lo que `composition` importa `registry`. Ningún módulo del motor importa `registry`.
- **M12:** el `KnowledgeSource` respaldado por el registry sustituye a `FileKnowledgeSource`.
- **Unidad 3:** implementa `SandboxPort`. Hasta entonces se usa `LocalSandbox`.
- **Unidad 6 / otra persona del equipo:** escenarios de negocio de la demo y `Judge`.

- **M0** (2026-09-30, `SCHEMA_VERSION` 1.3.0): `Agent.metrics` y los tipos del DSL de métricas (ADR 0020).
- **Registry** (2026-10-01): tabla `reg_release_eval_suites`, columna `reg_approvals.yardstick_loosened` (`ALTER TABLE … ADD COLUMN IF NOT EXISTS`, idempotente; las filas previas quedan con `[]`), código `loosening_not_accepted`. El formato de la suite cambió (`thresholds` por métrica en lugar de `noise_margin`/`floor` de la suite) sin migración de datos: las bases de desarrollo con suites del formato anterior se recrean. Sin cambios en M0.

## 16. Fase 2 (diseño de producción, fuera de la entrega)

Diseño conservado de la rev. 1, que no se construye antes del 05/10:

- **Suites por entidad** además de la suite por agente.
- **Estados `stale` y `abandoned`** como estados propios, con detección proactiva cuando cambia la base y retención de propuestas abandonadas.
- **Políticas protegidas:** un cambio a una política exige la aprobación de su `dueño_politica` (ADR 0009).
- **Conocimiento editable por propuesta:** crear y modificar páginas, con la regla de que una página `approved` la aprueba un principal humano (ADR 0015).
- **Escáner de secretos** en `validate` y **guarda contra ReDoS** en las reglas de injection.
- **Presupuestos del agente autónomo** (borradores, evaluaciones y costo por propuesta).
- **Cadena de hash** en `registry_events` (`sha256(JCS(evento) ‖ hash_previo)`, como en M11).
- **Evaluación asíncrona** (`start_evaluation` y `get_evaluation`).
- **Diff estructural** campo a campo entre versiones de una entidad, y `where_used`.
- **Rol `lector` explícito** y autorización del linaje por dueño del run (M9).
- **Salto semver propuesto por el servicio** a partir del diff.
- **Aprobación de cuatro ojos** configurable (prohibir la autoaprobación).
- **Aprobación automática** de cambios de bajo riesgo.
- `BlobStore` sobre S3, export a git de solo lectura, spans OTel `agentcore.registry.*` y las métricas de la rev. 1 (tiempo de propuesta a publicación, tasa de `gate_failed` por métrica, aceptación por `origin`, reversiones, latencias).
- Extraer el registry a un servicio propio; la frontera es `RegistryPort` más `RegistryService`.

## 17. Abiertos

1. **Calibración de la suite de la demo:** `repetitions`, `noise_margin` y `floor` se fijan con corridas reales contra el LLM.
2. **Entrega del `SandboxPort` real** por la unidad 3 (fecha y forma del `seed`). Mientras tanto, `LocalSandbox`.
3. ~~**Límites concretos**~~ **Decidido 2026-09-30 (tema #16):** 50 cambios por propuesta, 262 144 bytes por entidad y 200 nodos por flow (`registry/validation.py`, `Limits`).
4. ~~**Retención**~~ **Decidido 2026-09-30 (tema #16):** se conserva todo en el MVP; fase 2: purgar propuestas abandonadas de más de 90 días y conservar las últimas N evaluaciones por propuesta. Los topes del constructor autónomo (10 y 20) están implementados.

## 18. Dependencias del motor y de los agentes internos (ADR 0019)

> Rev. 2: la entrega cubre #1 en parte (`put_draft` con `expected_rev`; sin `readback_by`), #3 con el código `forbidden_role` propio del paquete (no se añade a `ProblemCode`), #4 con `RegistryService` y su API (no hay puerto nuevo en M0) y #5 con los roles `constructor` y `aprobador` en `Principal.roles` más `attrs.actor`. El resto queda como estaba.

Lo que el motor y los agentes internos (constructor, copiloto del asesor) necesitan de este registry y que hoy no existe. Nada de esto está construido.

| # | Dependencia | Quién la necesita | Nota |
|---|---|---|---|
| 1 | **Borradores reversibles e idempotentes** (`put_draft` con `expected_rev`, reintento con la misma `idempotency_key` sin duplicar) y **`readback_by`** para verificar la escritura | tools `write_draft` del constructor | La clase `write_draft` solo es admisible si ninguna release publicada lee un borrador (§2 regla 8). Si no se garantiza, el constructor vuelve a `confirm → act → verify`. **Construido (2026-09-30, spec write-draft fase 1):** `reg_draft_writes`, `idempotency_key` en `create_proposal`, `put_draft`, `freeze`, `reopen` y `evaluate`, y `get_write`. La regla 8 la guardan `tests/registry/test_rule8_invariant.py` y la prueba de contrato de `tests/integration/test_registry_postgres.py` |
| 2 | **Adaptador de `ToolExecutor`** que envuelva la API de §6 (crear propuesta, editar borrador, validar, congelar, evaluar) con su propia credencial de rol `constructor` | constructor | `FakeToolExecutor` cubre las pruebas mientras tanto **Construido (2026-09-30):** `BuilderToolExecutor` en `agent_core/composition/builder_tools.py` (tools `registry/<nombre>@1.0.0`; sin aprobar, publicar, promover ni revocar). |
| 3 | **`forbidden_role` en `ProblemCode`** (M0) y su verificación en el servidor | constructor | Hoy no existe en `agent_core` |
| 4 | **Puerto de escritura de propuestas.** `RegistryPort` (M0) es solo lectura | constructor | Un puerto nuevo cambia M0 y `contracts/` |
| 5 | **Roles del registry frente a `Principal.roles`** (lista libre) y sus scopes | constructor, `aprobador` | Definir cómo se emiten y quién los firma |
| 6 | **Validación de referencias del nodo `agent`** (`prompt_ref`, `tools_allowed`) en el gate G0 | copiloto y constructor | Al levantar G0-01 para `agent` |
| 7 | **Clase de riesgo de las tools del constructor** declarada en su `ToolDef` (`write_draft`) y comprobada por AG-02 | constructor | m01 §3.13 **Construido (2026-09-30):** `RiskClass.write_draft` (M0 1.2.0) y AG-02 (M1). |
| 8 | **Publicación de conocimiento aprobado**, si el copiloto lo consulta | copiloto | Depende también de M12 y de habilitar `knowledge_refs` (G0-01) |
| 9 | **Catálogo de campos y plantillas de handoff como entidades versionadas**, solo si se elige esa vía | M7, M10 | Hoy son valores por defecto en código |
| 10 | **Topes del agente autónomo** (§17.4) | constructor por señal | Sin valores, el constructor `task` no debería activarse **Construido (2026-09-30):** 10 propuestas por día y 20 evaluaciones por propuesta aplicados en `RegistryService`; sigue diferido el tope de costo, así que el constructor `task` aún no debe activarse. |
| 11 | **Directorio de agentes y tool `directory/list`** | agente de recepción (transferencia, ADR 0021) | Implementado en la unidad de transferencia, **sin cablear en la raíz de composición** (solo lo usan las pruebas): `AgentDirectory`, `RegistryDirectory` (§7.1b) y `DirectoryToolExecutor` en `composition`, que envuelve al `ToolExecutor`. Cierra el abierto 5 de la spec de transferencia (dueño de la tool). La tool recibe `directory` y `locale` como argumentos, porque `ToolCallContext` no trae el idioma |
