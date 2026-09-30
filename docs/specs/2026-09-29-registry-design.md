# Spec — Registry (unidad 2: entidades, versionado y publicación)

- Estado: **rev. 2, alcance de entrega (MVP) acordado el 2026-09-30; pendiente de revisión**
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
| 1 | **Postgres es la única fuente de verdad** | Esquema `registry`. Git no interviene. El YAML de M1 sirve para importar y exportar. ADR 0017. |
| 2 | **Contenido detrás de `BlobStore`** | Direccionado por hash. Adaptador único sobre Postgres; S3 es fase 2. |
| 3 | **Entidad versionada + release** | Cada entidad tiene su semver. Publicar crea una release que fija todas las versiones exactas. La traza guarda solo el `release_id`. |
| 4 | **Una sola vía de cambio** | Toda modificación es una *propuesta* con el mismo ciclo, venga de una persona, del constructor o del detector (`origin`). |
| 5 | **Evaluación por agente sobre escenarios** | Lo que se mide es si el agente resuelve mejor los casos, sin importar qué entidad cambió. Los escenarios corren con el motor real, el LLM real y acciones contra un sandbox. |
| 6 | **Gate sin excepción manual** | Los guardarraíles no pueden empeorar y la métrica principal debe ser ≥ la de la base, dentro del margen de ruido. ADR 0018. |
| 7 | **Aprobación solo humana** | Un principal no humano (el constructor o el detector) nunca aprueba, publica, promueve ni revoca, aunque tenga el rol. Una persona sí puede aprobar su propia propuesta. |
| 8 | **El motor solo lee lo publicado** | La candidata vive en memoria durante la evaluación (§5.3). Las versiones no publicadas nunca entran en `entity_versions`. |

## 3. Modelo de datos

### 3.1 Tipos de entidad

- Los de M0 (`agent`, `flow`, `decision_model`, `policy`, `template`, `prompt`, `tool`, `language_detection`, `injection_ruleset`, `model_profile`, `knowledge_snapshot`).
- **`knowledge_snapshot`:** manifiesto inmutable de páginas (`path`, `hash`, `audience`, `status`, `lang`, vigencia, `source_refs`; ADR 0015). El manifiesto es una entidad normal y el texto de cada página vive en el `BlobStore`. **En la entrega** una release fija un snapshot sembrado por `import`, y las propuestas **no** crean ni modifican snapshots (lo rechaza `validate`).
- **`eval_suite`** (propio del paquete; no entra en M0 ni en una `Release`): suite de escenarios versionada y ligada a un agente (§6.1). Se guarda en `entity_versions` como las demás entidades y se crea o cambia por propuesta.

### 3.2 Tablas (esquema `registry`)

**Inmutables** (el rol de la aplicación solo tiene `INSERT` y `SELECT`, y un trigger rechaza `UPDATE` y `DELETE`):

| Tabla | Columnas clave |
|---|---|
| `blobs` | `hash` PK (sha256 de `canonical_bytes`), `bytes` |
| `entity_versions` | `(kind, id, version)` PK, `content_hash` → `blobs`, `docs` (`VersionDocs`), `proposal_id`, `created_by`, `created_at` |
| `releases` | `release_id` PK, `release_hash`, `agent_id`, `base_release_id`, `proposal_id`, `published_by`, `published_at` |
| `release_entities` | `(release_id, kind, id, version)` |
| `approvals` | `proposal_id`, `candidate_hash`, `actor`, `decision` (`approved` o `rejected`), `reason`, `at` |
| `eval_runs` | `eval_run_id` PK, `proposal_id`, `candidate_hash`, `base_release_id`, `suite_ref`, `report` (JSON, §6.4), `verdict` (`pass`, `fail` o `failed_infra`), `at` |
| `registry_events` | bitácora de auditoría (§12) |

**Mutables y controladas:**

| Tabla | Qué cambia |
|---|---|
| `release_status` | `release_id → active` o `revoked`. Está separada de `releases` para que `releases` sea estrictamente inmutable. Solo cambia por `revoke`. |
| `aliases` | `(agent_id, alias) → release_id` (`staging`, `prod`). Solo cambia por `publish` (`staging`) y `promote`. |
| `alias_log` | Solo inserción: cada cambio de alias con actor, motivo y release anterior. |
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
| `candidate` → `evaluated` | `evaluate(suite_ref)` | constructor | síncrono (§6). `pass` → `evaluated`; `fail` → `draft` y responde `gate_failed`; `failed_infra` → sigue en `candidate` |
| `evaluated` → `approved` | `approve(candidate_hash)` | aprobador humano | exige una evaluación `pass` con ese hash. Si el hash es distinto → `candidate_changed` |
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
6. Cada `eval_suite` referenciada pertenece al agente de la propuesta.

Devuelve `list[Violation]` (la de M1): `rule`, `entity`, `path` y un mensaje en lenguaje claro para personas no técnicas.

### 5.3 `SnapshotRegistry`

Es la implementación de `RegistryPort` en memoria y de solo lectura, construida desde una candidata (una `Release` más sus entidades). Solo la usa el evaluador. El `RegistryPort` de producción (`PostgresRegistry`) nunca ve candidatas.

### 5.4 `publish` (una transacción)

1. `SELECT … FOR UPDATE` sobre `aliases(agent_id, 'staging')`. Si no apunta a `base_release_id` → `proposal_stale`, y la propuesta vuelve a `draft` con la base actualizada (hay que congelar y evaluar de nuevo).
2. Rearma la candidata y verifica que su hash sea igual a `candidate_hash`; si no, `candidate_changed`.
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
repetitions: 3                 # k corridas por escenario y release
noise_margin: 0.05             # tolerancia de la métrica principal
floor: 0.70                    # mínimo cuando no hay release base
scenarios:
  - id: disputa-cargo-duplicado
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
```

Los escenarios de negocio de la demo los escribe otra persona del equipo; este paquete define el formato y su validación.

### 6.2 `ScenarioEvaluator` (implementa `EvalPort`)

Para la candidata y para la base (si existe), con la misma suite congelada:

1. Compone el motor a través de un `ScenarioHarness` (protocolo del paquete) que implementa `agent_core.composition`: un `SnapshotRegistry` construido desde la candidata o desde la release base publicada (así la base no depende de que un alias se mueva durante la evaluación), el LLM gateway real y las tools apuntadas al sandbox (§6.3).
2. Corre cada escenario `repetitions` veces. Cada corrida recibe su propio entorno de sandbox sembrado con el `seed` del escenario.
3. **Califica desde los eventos del motor.** Una corrida pasa si cumple todo el `expect`. La métrica principal es la proporción de corridas que pasan.
4. **Guardarraíles** (conteos sobre los eventos): afirmación de éxito sin `verify`, escritura sin verificación y datos de vista `full` en la respuesta o en un evento. Los emiten M2, M3, M6 y M8.
5. Corre en paralelo con un tope de concurrencia configurable.
6. Una falla del gateway durante una corrida (la detecta una sonda del harness, porque el motor la absorbe) o del sandbox → `failed_infra`, igual que el tiempo vencido. Toda la evaluación queda `failed_infra`. Nunca hay un pase parcial.

Costo orientativo: 2 releases × N escenarios × k corridas. Con 10 escenarios y k = 3, son 60 conversaciones por evaluación.

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
- **Respaldo de la entrega:** `LocalSandbox` en `agent-core` implementa el puerto sobre un esquema Postgres efímero por corrida. Sustituirlo por el de la unidad 3 no toca el evaluador.

### 6.4 Veredicto y reporte

- `fail` si algún guardarraíl de la candidata supera al de la base (sin base, si alguno es mayor que 0).
- `fail` si hay base y `principal_candidata < principal_base − noise_margin`.
- `fail` si no hay base y `principal_candidata < floor`.
- En otro caso, `pass`.

`EvalReport` guarda cada métrica con su valor, su base y su umbral, y el resultado por escenario y corrida (para que quien aprueba vea *qué caso mejoró o empeoró*), más las notas del juez si las hay.

**Juez LLM opcional:** un hook `Judge` que recibe las transcripciones y devuelve notas por escenario. Va al reporte como información y **no entra al veredicto**. Lo implementa otra persona del equipo; sin juez, el reporte omite esa sección.

**No hay excepción manual.** Un gate fallido devuelve la propuesta a `draft`; nadie puede aprobar sobre él.

## 7. Interfaz pública

### 7.1 `RegistryPort` (runtime, sin cambios en M0)

`resolve_release`, `release_status` y `get`. Tiene dos implementaciones en el paquete: `PostgresRegistry` (producción) y `SnapshotRegistry` (evaluación). Ambas pasan la suite de contrato de `InMemoryRegistry`.

`PostgresRegistry`: `resolve_release` sigue el alias (`prod` por defecto, `staging` si el selector lo pide) y cachea la release por `release_id`. `release_status` se consulta con un TTL corto para que una revocación surta efecto rápido. `get` verifica el hash al leer; si no coincide, `IntegrityError` y el motor escala.

### 7.2 `RegistryService` (gestión)

```python
class RegistryService:
    # Construcción (rol constructor)
    def create_proposal(self, actor, agent_id, origin, title) -> Proposal
    def put_draft(self, actor, proposal_id, changes: list[EntityDraft], expected_rev: int) -> Proposal
    def validate(self, actor, proposal_id) -> ValidationReport
    def freeze(self, actor, proposal_id) -> Candidate
    def evaluate(self, actor, proposal_id, suite: EntityRef) -> EvalReport
    def reopen(self, actor, proposal_id) -> Proposal

    # Decisiones (rol aprobador, principal humano)
    def approve(self, actor, proposal_id, candidate_hash: str) -> Approval
    def reject(self, actor, proposal_id, reason: str) -> Proposal
    def publish(self, actor, proposal_id, idempotency_key: str) -> ReleaseDetail
    def promote(self, actor, agent_id, alias, release_id) -> AliasChange
    def revoke(self, actor, release_id, reason: str) -> ReleaseDetail
    def import_seed(self, actor, root: Path) -> list[ReleaseDetail]

    # Lecturas (cualquier principal autenticado)
    def get_proposal(self, proposal_id) -> ProposalDetail            # cambios, violaciones, candidata, último reporte
    def get_entity(self, kind, entity_id, version: str | None) -> EntityVersion
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
    def run(self, suite: EvalSuite, candidate: EvalTarget, base: EvalTarget | None) -> EvalReport

class Judge(Protocol):                                               # opcional
    def score(self, suite: EvalSuite, transcripts: list[ScenarioTranscript]) -> list[JudgeNote]
```

Más `SandboxPort` (§6.3) y, para el linaje, la lectura del `release_id` de un run por la interfaz pública de M11.

### 7.4 API REST (`/v1/registry`)

Se monta en la app FastAPI de M9 con la misma autenticación JWS, `problem+json` y `trace_id` en toda respuesta.

| Método y ruta | Operación |
|---|---|
| `POST /proposals` · `GET /proposals/{id}` | crear · ver |
| `PUT /proposals/{id}/draft` | `put_draft` |
| `POST /proposals/{id}/validate` · `/freeze` · `/reopen` | construir |
| `POST /proposals/{id}/evaluate` | `evaluate` (síncrono; devuelve `EvalReport`) |
| `POST /proposals/{id}/approve` · `/reject` | decidir |
| `POST /proposals/{id}/publish` | `publish` (exige `Idempotency-Key`) |
| `POST /aliases/{agent}/{alias}` · `POST /releases/{id}/revoke` | promover · revocar |
| `GET /entities/{kind}/{id}` · `GET /entities/{kind}/{id}/{version}` | leer entidades |
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
| `forbidden_role` | 403 | falta el rol o el principal no es humano donde se exige |
| `integrity_error` | 500 | el hash de un contenido no coincide |

### 7.5 CLI (`agentcore registry …`)

`import`, `export`, `propose`, `draft <propuesta> <archivos-o-carpeta>` (YAML con el formato de M1), `validate`, `freeze`, `evaluate`, `approve`, `reject`, `publish`, `promote`, `revoke`, `diff` y `lineage`. Es un cliente delgado sobre `RegistryService`, sin lógica propia.

Formato de cada archivo de `draft` (una entidad por archivo YAML; una carpeta se recorre de forma recursiva): `{kind, docs: {description, rationale, changelog}, content: {...}}`, donde `content` es la entidad en el formato de M1. Las opciones globales son `--dsn` (o `AGENTCORE_REGISTRY_DSN`), `--credential` (o `AGENTCORE_CREDENTIAL`, el JWS del principal), `--verifier` y `--harness` (rutas `modulo:atributo`; por defecto las de `testing.registry_demo`, que son dobles de PRUEBA). `lineage` por CLI construye el servicio sin `RunReleaseReader` y responde `not_found` hasta que se cablee un lector de runs.

El ciclo manual queda así: `export` → editar en el editor → `draft` → `freeze` → `evaluate` → `approve` → `publish`.

## 8. Roles y seguridad

| Rol | Puede |
|---|---|
| (cualquier principal autenticado) | leer entidades, releases, diffs, propuestas y reportes |
| `constructor` | crear propuestas, editar borradores, validar, congelar, evaluar y reabrir |
| `aprobador` | aprobar, rechazar, publicar, promover, revocar e importar la semilla |

- **Los roles son acumulables.** Una persona con `constructor` y `aprobador` recorre el ciclo completo sola y puede aprobar su propia propuesta. `approvals` y el linaje lo registran ("construido por X, aprobado por X").
- **Barrera de actor humano, verificada en el servidor:** las operaciones de `aprobador` exigen además un principal humano. En M0 no existe un tipo `agent` (ADR 0006): el agente constructor autentica como `builder` con su **propia credencial** de rol `constructor`, y el principal del run viaja solo como actor de auditoría, nunca como fuente de permisos (ADR 0019). Una persona con rol `aprobador` que chatea con el constructor no le presta ese rol. Un principal es humano solo si su credencial firmada trae `attrs.actor = "human"`; si falta o tiene otro valor, se trata como no humano (falla cerrado) y recibe `forbidden_role` aunque tenga el rol. Nunca se confía en el prompt del agente.
- **Contenido no confiable:** todo lo que propone un agente o un LLM se valida contra el esquema estricto, nunca se ejecuta y tiene límites de tamaño y cantidad.
- **Lo que el constructor lee** (trazas, documentación, páginas) es dato, no instrucción (ADR 0008, M12).
- **Datos:** las suites y los `seed` del sandbox usan solo datos sintéticos.

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
- En la entrega, `lineage_for_run` exige un principal autenticado. La autorización fina por dueño del run (M9) es de fase 2.

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
| T-REG-08 | Gate: un guardarraíl que empeora hace fallar aunque la métrica principal mejore |
| T-REG-09 | Gate: la métrica principal dentro del margen pasa; fuera del margen falla |
| T-REG-10 | Gate: sin base, se compara contra `floor` |
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
| T-REG-24 | El evaluador se niega a correr con un `ActionsConfig` no marcado como sandbox |
| T-REG-25 | Calificación: `expect` se evalúa desde los eventos, y los guardarraíles se cuentan bien con eventos sintéticos |
| T-REG-26 | `import` de la carpeta YAML de la demo crea releases publicadas; `export` seguido de `import` conserva los `content_hash` |
| T-REG-27 | **E2E:** `import` → propuesta (cambio de prompt) → `freeze` → `evaluate` (`LocalSandbox`, LLM falso determinista) → `approve` → `publish` → un run nuevo usa la release nueva → el linaje muestra la entidad cambiada con su `VersionDocs` |

## 14. Definición de terminado

- `RegistryService`, `BlobStore`, `EvalPort`, `SandboxPort`, `Judge`, `PostgresRegistry` y `SnapshotRegistry` exportados y tipados.
- T-REG-01 a T-REG-27 en verde.
- `lint-imports`, `mypy` y `ruff` en verde.
- API montada en M9 y CLI funcionando.
- La composición del motor usa `PostgresRegistry`.
- `contracts/` regenerado si se tocó M0.
- Sin TODO sin issue.

## 15. Cambios en otros módulos

Todos **aditivos**.

- **M0** (aplicado el 2026-09-29, `SCHEMA_VERSION` 0.2.0): `EntityKind.knowledge_snapshot`, `KnowledgeSnapshot` y `Release.knowledge_snapshot`. No cambia `RegistryPort`, `EngineEvent` ni `ProblemCode`.
- **M0:** `IdKind.proposal` y `IdKind.eval_run` (`SCHEMA_VERSION` 0.5.0).
- **M1:** exporta `entity_ref_sites`, `RefSite` y `kind_of`.
- **M1** (aplicado el 2026-09-29, rev. 3): `ReleaseDecl.knowledge`, `pin_release` con snapshot, `load_registry` con manifiestos. **Pendiente de verificar:** que `flows/__init__.py` exporte todo lo que el registry reutiliza (validación de flow, chequeos por agente y de release, `pin_release`, `Violation`, `AuthoringRegistry` construible en memoria) y que exista un volcado a YAML para `export`.
- **M3:** sin cambios (el sandbox entra como `ToolExecutor`).
- **M9:** `ApiDeps.extensions`: cada extensión recibe la app y un `authenticate(request, authorization)`.
- **M11:** expone por su interfaz pública la lectura del `release_id` de un run.
- **`composition`:** una fábrica que compone el motor con un `RegistryPort` y un `ActionsConfig` dados (la usan el evaluador y la API).
- **`.importlinter`:** `registry` puede usar `domain`, `ports`, la interfaz pública de `flows` y, solo el submódulo del evaluador, `composition`. Ningún módulo del motor importa `registry`.
- **M12:** el `KnowledgeSource` respaldado por el registry sustituye a `FileKnowledgeSource`.
- **Unidad 3:** implementa `SandboxPort`. Hasta entonces se usa `LocalSandbox`.
- **Unidad 6 / otra persona del equipo:** escenarios de negocio de la demo y `Judge`.

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
3. **Límites concretos** de tamaño y cantidad por entidad y por propuesta.
4. **Retención** de `registry_events`, `eval_runs` y propuestas.

## 18. Dependencias del motor y de los agentes internos (ADR 0019)

> Rev. 2: la entrega cubre #1 en parte (`put_draft` con `expected_rev`; sin `readback_by`), #3 con el código `forbidden_role` propio del paquete (no se añade a `ProblemCode`), #4 con `RegistryService` y su API (no hay puerto nuevo en M0) y #5 con los roles `constructor` y `aprobador` en `Principal.roles` más `attrs.actor`. El resto queda como estaba.

Lo que el motor y los agentes internos (constructor, copiloto del asesor) necesitan de este registry y que hoy no existe. Nada de esto está construido.

| # | Dependencia | Quién la necesita | Nota |
|---|---|---|---|
| 1 | **Borradores reversibles e idempotentes** (`put_draft` con `expected_rev`, reintento con la misma `idempotency_key` sin duplicar) y **`readback_by`** para verificar la escritura | tools `write_draft` del constructor | La clase `write_draft` solo es admisible si ninguna release publicada lee un borrador (§2 regla 7). Si no se garantiza, el constructor vuelve a `confirm → act → verify` |
| 2 | **Adaptador de `ToolExecutor`** que envuelva la API de §6 (crear propuesta, editar borrador, validar, congelar, evaluar) con su propia credencial de rol `constructor` | constructor | `FakeToolExecutor` cubre las pruebas mientras tanto |
| 3 | **`forbidden_role` en `ProblemCode`** (M0) y su verificación en el servidor | constructor | Hoy no existe en `agent_core` |
| 4 | **Puerto de escritura de propuestas.** `RegistryPort` (M0) es solo lectura | constructor | Un puerto nuevo cambia M0 y `contracts/` |
| 5 | **Roles del registry frente a `Principal.roles`** (lista libre) y sus scopes | constructor, `aprobador` | Definir cómo se emiten y quién los firma |
| 6 | **Validación de referencias del nodo `agent`** (`prompt_ref`, `tools_allowed`) en el gate G0 | copiloto y constructor | Al levantar G0-01 para `agent` |
| 7 | **Clase de riesgo de las tools del constructor** declarada en su `ToolDef` (`write_draft`) y comprobada por AG-02 | constructor | m01 §3.13 |
| 8 | **Publicación de conocimiento aprobado**, si el copiloto lo consulta | copiloto | Depende también de M12 y de habilitar `knowledge_refs` (G0-01) |
| 9 | **Catálogo de campos y plantillas de handoff como entidades versionadas**, solo si se elige esa vía | M7, M10 | Hoy son valores por defecto en código |
| 10 | **Topes del agente autónomo** (§17.4) | constructor por señal | Sin valores, el constructor `task` no debería activarse |
