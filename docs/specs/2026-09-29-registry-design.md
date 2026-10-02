# Spec — Registry (unidad 2: entidades, versionado y publicación)

<<<<<<< HEAD
- Estado: **rev. 2 implementada (entrega), 2026-09-30.** Pendiente en §14: composición del motor con `PostgresRegistry` (raíz de composición del servidor, con el LLM gateway). §5.2 enmendado por el ADR 0020
- Fecha: 2026-09-29 (rev. 1) · 2026-09-30 (rev. 2)
=======
- Estado: **borrador para revisión** (§5.2 enmendado por el ADR 0020)
- Fecha: 2026-09-29
>>>>>>> 4f7743d4a85e3b3adef7e19fa6fa75624985b796
- Repo: `agent-core`
- Paquete: `agent_core.registry`
- ADRs: 0017 (Postgres como única fuente de verdad), 0018 (propuestas y gate de publicación); además 0002 (contratos), 0006 (principal), 0007 (acciones), 0009 (políticas protegidas), 0015 (conocimiento)
- Usa: M0 (tipos y `RegistryPort`), M1 (funciones puras de validación y `pin_release`) · Lo usan: el motor (por `RegistryPort`), la plataforma, el agente constructor, el detector automático, M12
- Autor: Juan Zapata, con Claude

## 1. Propósito y límites

El registry guarda las **entidades** del sistema de decisión (agentes, flows, modelos de decisión, políticas, plantillas, prompts, tools, perfiles de modelo, configuración de idioma, reglas de injection y snapshots de conocimiento), las versiona, las agrupa en **releases inmutables** y controla cómo se publican.

Lo usan tres clientes con necesidades distintas:

- **El motor en runtime:** solo lee releases publicadas, por el `RegistryPort` de M0.
- **El agente constructor** (conversacional, manual o autónomo) y el **detector automático de casos:** crean y mejoran entidades mediante *propuestas* que se validan, evalúan y aprueban.
- **La plataforma:** muestra a personas no técnicas qué versión de cada entidad corrió en una ejecución (linaje), qué cambió entre releases y quién lo aprobó.

**No hace:**

- Ejecutar flows ni evaluar el comportamiento del motor (M2/M4). Las evaluaciones las corre la unidad 6 a través de `EvalPort`.
- Ingesta ni mantenimiento automático del conocimiento (unidad 7). Las páginas nuevas entran como propuestas.
- Autenticar identidades (servicio de identidad) ni decidir permisos de datos de clientes (unidad 3).
- Aprobar cambios por sí mismo: la aprobación es siempre una decisión humana (§4).

## 2. Decisiones de diseño

| # | Decisión | Detalle |
|---|---|---|
| 1 | **Postgres es la única fuente de verdad** | Esquema `registry`. Git no interviene; el YAML de M1 es formato de importación y exportación. ADR 0017. |
| 2 | **Contenido detrás de `BlobStore`** | Direccionado por hash. Hoy se implementa sobre Postgres; pasar a S3 es un cambio de adaptador. |
| 3 | **Entidad versionada + release** | Cada entidad tiene semver propio y su propia evaluación. Publicar crea una release que fija todas las versiones exactas. La traza guarda solo el `release_id`. |
| 4 | **Una sola vía de cambio** | Toda modificación es una *propuesta* con el mismo ciclo, venga del constructor conversacional, del detector o de una persona. |
| 5 | **Gate de evaluación sin excepción manual** | Guardarraíles que no pueden empeorar y métrica principal mayor o igual que la vigente. ADR 0018. |
| 6 | **Aprobación solo humana y manual** | El constructor y el detector nunca aprueban, publican, promueven ni revocan. La aprobación automática queda como tema abierto (§17). |
| 7 | **El motor solo lee snapshots publicados** | Nunca borradores ni propuestas. Un error del constructor no puede afectar una ejecución en curso. |

## 3. Modelo de datos

### 3.1 Tipos de entidad

Los diez de M0 (`agent`, `flow`, `decision_model`, `policy`, `template`, `prompt`, `tool`, `language_detection`, `injection_ruleset`, `model_profile`) más:

- **`knowledge_snapshot`** (nuevo en M0, §15): manifiesto inmutable de un conjunto de páginas. Cada página guarda `path`, `hash` del contenido, `audience`, `status`, `lang`, vigencia y `source_refs` (ADR 0015). El contenido de cada página vive en el `BlobStore`. Se cita como `ruta@snapshot#ancla`.
- **`eval_suite`** (solo del registry, no entra en M0 ni en una release): suite de evaluación versionada, ligada a un tipo de entidad. Declara guardarraíles, métrica principal, margen de ruido y piso mínimo (§5.2).

### 3.2 Tablas (esquema `registry`)

<<<<<<< HEAD
**Inmutables** (el rol de la aplicación solo tiene `INSERT` y `SELECT`, y un trigger rechaza `UPDATE` y `DELETE`):

> Nota (ADR 0020): `releases` registra además `eval_suite_refs` (las suites usadas en el gate; metadato de gobierno que el motor no lee). Pendiente de incorporarse al esquema al rediseñar el gate sobre este registry.
=======
| Tabla | Contenido | ¿Mutable? |
|---|---|---|
| `entity_versions` | `(kind, id, version)`, contenido (referencia al `BlobStore`), `content_hash`, documentación (§3.3), `created_by`, `origin`, `created_at` | **Nunca**: solo `INSERT` |
| `releases` | `release_id`, lista exacta de `kind/id@version`, `knowledge_snapshot`, `eval_suite_refs` (suites usadas; metadato de gobierno que el motor no lee, ADR 0020), `proposal_id` de origen, `published_by`, `published_at`, `status` | Solo `status` (`active` ↔ `revoked`) |
| `release_entities` | `(release_id, kind, id, version)`; permite `where_used` | Nunca |
| `aliases` | `(agent_id, alias) → release_id` (`staging`, `prod`) | Solo por `promote`; toda promoción queda en la bitácora |
| `alias_log` | historial de promociones y reversiones | Solo inserción |
| `proposals` | `proposal_id`, `origin`, `state`, `base_release`, `title`, `created_by`, `rev`, `candidate_hash` | Sí (máquina de estados) |
| `proposal_changes` | copia de trabajo de cada entidad modificada, por propuesta | Sí, hasta `freeze` |
| `eval_runs` | candidata frente a base, métricas por suite, veredicto, `status` | Solo inserción; el `status` avanza |
| `approvals` | `(proposal_id, candidate_hash, actor, role, decision, at)` | Nunca |
| `registry_events` | eventos de auditoría encadenados por hash (§11) | Solo inserción |
| `blobs` | `(hash, bytes)` (implementación Postgres de `BlobStore`) | Solo inserción |
>>>>>>> 4f7743d4a85e3b3adef7e19fa6fa75624985b796

La inmutabilidad se hace cumplir en la base de datos: el rol de la aplicación no tiene `UPDATE` ni `DELETE` sobre las tablas inmutables, y hay *triggers* como segunda barrera.

### 3.3 Documentación por versión

Cada `entity_versions` lleva una envoltura (no forma parte del contenido de la entidad, así que no cambia los tipos de M0):

```python
class VersionDocs(BaseModel, frozen=True):
    description: str       # qué es y para qué sirve
    rationale: str         # por qué se hizo este cambio
    changelog: str         # qué cambió respecto de la versión anterior
```

La plataforma la muestra junto a la versión en el linaje. El agente constructor la escribe al proponer el cambio; el aprobador la ve antes de aprobar.

### 3.4 Versionado

- Cada entidad tiene su semver. Publicar una entidad modificada crea una versión nueva y una release nueva que fija todas las versiones exactas (como exige M0 §2.2).
- En autoría se permiten rangos (`^1`, `~1.2`); al publicar se reescriben a versiones exactas con la lógica de `pin_release` de M1. En runtime el motor nunca resuelve rangos.
- El salto de versión (patch/minor/major) lo propone el servicio a partir del diff y lo confirma el aprobador (§17, abierto).

## 4. Ciclo de vida de una propuesta

```
draft → validated → candidate → evaluated → approved → published
   ↑________________________________________|   (gate falla o hay cambios → vuelve a draft)
```

| Estado | Qué ocurre | Quién lo dispara |
|---|---|---|
| `draft` | Copia de trabajo mutable de las entidades que cambian. `put_draft` con `expected_rev`. | constructor, detector o persona |
| `validated` | Pasan el gate G0 de M1 por flow, los chequeos por agente y los chequeos de release (§5.1). Los fallos vuelven como lista estructurada. | constructor o detector |
| `candidate` | **Se congela:** se calcula el `candidate_hash` y se arma una release candidata con referencias exactas. No sirve tráfico de producción. Cualquier edición posterior crea una candidata nueva e invalida evaluaciones y aprobaciones previas. | constructor o detector |
| `evaluated` | Se corren las suites sobre la candidata y sobre la release base, con la misma suite congelada, y se calcula el veredicto (§5.2). | constructor o detector |
| `approved` | Una persona con rol `aprobador` aprueba en la plataforma. Exige veredicto positivo vigente. La aprobación queda atada a `candidate_hash`. | solo humano |
| `published` | En **una transacción**: se insertan las versiones, se crea la release y se apunta el alias `staging`. | solo humano |

Estados terminales o laterales: `rejected` (un aprobador la rechaza), `abandoned` (nadie la retoma) y `stale` (la release base dejó de ser la vigente antes de publicar; hay que reconstruir y reevaluar contra la nueva base).

Después de `published`, la **promoción a `prod`** es un paso aparte (`promote`), solo de un `aprobador`. Revocar y volver a una release anterior son operaciones directas (§5.3).

### 4.1 Reglas de la máquina de estados

- Toda transición exige el rol adecuado, verificado en el servidor (§7). Una transición inválida lanza `IllegalTransition` (bug del cliente, `409`).
- Las propuestas son independientes. Si dos tocan la misma entidad, la segunda en congelarse queda `stale` cuando la versión base ya cambió.
- Un `aprobador` puede rechazar con un motivo, que el constructor recibe como entrada para iterar.

## 5. Gates

### 5.1 Validación (`validate`)

Ejecuta, sobre la candidata en memoria y sin escribir en Postgres:

1. El gate G0 de M1 sobre cada flow modificado (`parse_flow` y reglas G0-01 a G0-16).
2. Los chequeos de M1 por agente.
3. Los chequeos de release (spec general §6.2): dueño único por intención, `tools_allowed` cubre las tools de los flows, outcomes de `end` compatibles con el `mode` del agente, interrupciones válidas.
4. **Políticas protegidas:** un cambio a una política exige la aprobación de su dueño (ADR 0009).
5. **Conocimiento:** una página `approved` debe haber sido aprobada por un principal humano (`approved_by`); el constructor y el detector solo la dejan en `draft`.
6. **Escáner de secretos:** rechaza claves, tokens y credenciales dentro de cualquier entidad.
7. **Límites:** tamaño y cantidad de elementos por entidad (evita entidades desbocadas escritas por un agente).

Devuelve una lista de `Violation` (la de M1) con `rule`, `entity`, `path` y un mensaje en lenguaje claro para personas no técnicas.

### 5.2 Gate de evaluación (`evaluate`)

<<<<<<< HEAD
Devuelve `list[Violation]` (la de M1): `rule`, `entity`, `path` y un mensaje en lenguaje claro para personas no técnicas.

> **Enmendado por el ADR 0020** (2026-09-30): las métricas las declara cada agente (`Agent.metrics`), puede haber N métricas `gate` evaluadas por separado y el gate aplica una doble vara (la de la base y la de la candidata). El algoritmo vigente está en `docs/specs/2026-09-30-evaluacion-y-metricas-design.md` §6; el gate actual de este registry se reconcilia con él en un cambio posterior.
=======
> **Enmendado por el ADR 0020** (2026-09-30): las métricas las declara cada agente (`Agent.metrics`), puede haber N métricas `gate` evaluadas por separado en lugar de una sola métrica principal, y el gate aplica una doble vara (suite y métricas de la base, más las de la candidata). El algoritmo vigente está en `docs/specs/2026-09-30-evaluacion-y-metricas-design.md` §6; el texto de abajo queda como contexto histórico.

Cada entidad relevante lleva una `eval_suite` versionada. El registry ejecuta, vía `EvalPort`, la suite sobre la candidata y sobre la release base, y decide:
>>>>>>> 4f7743d4a85e3b3adef7e19fa6fa75624985b796

1. **Guardarraíles (tolerancia cero):** ninguna métrica de seguridad o invariante empeora. Incluye como mínimo fugas de PII, afirmaciones de éxito sin `verify`, escrituras sin verificar y citas a páginas no aprobadas.
2. **Métrica principal:** la de la candidata debe ser **mayor o igual** que la de la base, dentro del margen de ruido que declara la suite.
3. **Sin release base:** se compara contra el **piso mínimo** que declara la suite.

Ambas mediciones usan la misma suite congelada y el mismo `candidate_hash`. El veredicto guarda cada métrica con su valor, su base y su umbral.

**No hay excepción manual.** Si el gate falla, la propuesta vuelve a `draft`. Ni el `aprobador` puede publicar sobre un gate fallido.

Si el evaluador cae o vence el tiempo, la ejecución queda `failed_infra`: no cuenta como pase ni como fallo, se puede reintentar y sin veredicto no se puede aprobar.

### 5.3 Revocación y reversión

- **Revocar** una release (`active → revoked`) la hace un `aprobador`, de inmediato y sin gate. Los runs fijados a una release revocada escalan con `release_revoked` (M4).
- **Volver a una release anterior** es promover de nuevo una release que ya pasó el gate. No necesita gate nuevo.

## 6. Interfaz pública

### 6.1 `RegistryPort` (runtime, sin cambios)

Es el puerto de M0: `resolve_release`, `release_status`, `get`. El adaptador Postgres pasa la misma suite de contrato que `InMemoryRegistry`. Solo lee releases publicadas.

### 6.2 `RegistryService` (gestión)

```python
class RegistryService:
    # Propuestas (rol constructor)
    def create_proposal(self, actor, origin, base_release, title) -> Proposal
    def put_draft(self, actor, proposal_id, changes: list[EntityDraft], expected_rev: int) -> Proposal
    def validate(self, actor, proposal_id) -> ValidationReport
    def freeze(self, actor, proposal_id) -> Candidate                  # candidate_hash + release candidata
    def start_evaluation(self, actor, proposal_id) -> EvalRunId        # asíncrona
    def get_evaluation(self, actor, eval_run_id) -> EvalReport

    # Decisiones humanas (rol aprobador; dueño_politica donde aplica)
    def approve(self, actor, proposal_id, candidate_hash: str) -> Approval
    def reject(self, actor, proposal_id, reason: str) -> Proposal
    def publish(self, actor, proposal_id) -> Release
    def promote(self, actor, agent_id, alias, release_id) -> AliasChange
    def revoke(self, actor, release_id, reason: str) -> Release

    # Lecturas y linaje (rol lector)
    def get_entity(self, ref: EntityRef) -> EntityVersion
    def list_versions(self, kind, entity_id) -> list[VersionSummary]
    def diff(self, a: EntityRef, b: EntityRef) -> EntityDiff
    def get_release(self, release_id) -> ReleaseDetail
    def diff_releases(self, a: str, b: str) -> ReleaseDiff
    def where_used(self, ref: EntityRef) -> list[str]                  # release_ids
    def lineage_for_run(self, actor, run_id) -> RunLineage
```

`origin`: `builder_chat`, `auto_detect` o `manual`.

### 6.3 Puertos que consume

```python
class BlobStore(Protocol):
    def put(self, data: bytes) -> str                                  # devuelve el hash
    def get(self, hash: str) -> bytes                                  # verifica el hash; si no coincide, IntegrityError

class EvalPort(Protocol):                                              # lo implementa la unidad 6
    def run(self, suite: EntityRef, release: Release) -> EvalReport
```

### 6.4 API REST (`/v1/registry`)

Sigue el patrón de M9: `problem+json`, `trace_id` en toda respuesta e `Idempotency-Key` en las operaciones que escriben.

| Método y ruta | Operación | Rol |
|---|---|---|
| `POST /proposals` | `create_proposal` | constructor |
| `PUT /proposals/{id}/draft` | `put_draft` | constructor |
| `POST /proposals/{id}/validate` | `validate` | constructor |
| `POST /proposals/{id}/freeze` | `freeze` | constructor |
| `POST /proposals/{id}/evaluations` | `start_evaluation` | constructor |
| `GET /evaluations/{id}` | `get_evaluation` | lector |
| `POST /proposals/{id}/approve` | `approve` | aprobador |
| `POST /proposals/{id}/reject` | `reject` | aprobador |
| `POST /proposals/{id}/publish` | `publish` | aprobador |
| `POST /aliases/{agent}/{alias}/promote` | `promote` | aprobador |
| `POST /releases/{id}/revoke` | `revoke` | aprobador |
| `GET /releases/{id}`, `GET /entities/...`, `GET /runs/{id}/lineage` | lecturas | lector |

**Errores tipados** (enum propio del paquete; no se añaden a `ProblemCode` de M0):

| Código | HTTP | Cuándo |
|---|---|---|
| `validation_failed` | 422 | `validate` con violaciones (incluye la lista) |
| `gate_failed` | 409 | el veredicto no permite avanzar (incluye métricas) |
| `proposal_stale` | 409 | `expected_rev` desactualizado o release base cambiada |
| `candidate_changed` | 409 | la candidata cambió después de aprobar |
| `forbidden_role` | 403 | el rol no permite la operación |
| `integrity_error` | 500 | el hash de un contenido no coincide |

## 7. Roles y seguridad

| Rol | Puede |
|---|---|
| `lector` | leer entidades, releases, diffs y linaje |
| `constructor` | además, crear propuestas, editar borradores, validar, congelar y evaluar |
| `aprobador` | además, aprobar, rechazar, publicar, promover y revocar |
| `dueño_politica` | aprobar cambios de políticas protegidas (ADR 0009) |

<<<<<<< HEAD
Quién es quién (decisión 2026-09-30, tema #14): el **supervisor** es una persona con `constructor` y `aprobador`; el **administrador** es una persona con `constructor`, `aprobador` y `admin`; el **agente constructor** autentica con una credencial de servicio propia que solo puede traer `constructor` y nunca `attrs.actor = "human"`. Todos son `builder`: no hay un tipo de principal nuevo.

- **Los roles son acumulables.** Una persona con `constructor` y `aprobador` recorre el ciclo completo sola y puede aprobar su propia propuesta. `approvals` y el linaje lo registran ("construido por X, aprobado por X").
- **Barrera de actor humano, verificada en el servidor:** las operaciones de `aprobador` exigen además un principal humano. En M0 no existe un tipo `agent` (ADR 0006): el agente constructor autentica como `builder` con su **propia credencial** de rol `constructor`, y el principal del run viaja solo como actor de auditoría, nunca como fuente de permisos (ADR 0019). Una persona con rol `aprobador` que chatea con el constructor no le presta ese rol. Un principal es humano solo si su credencial firmada trae `attrs.actor = "human"`; si falta o tiene otro valor, se trata como no humano (falla cerrado) y recibe `forbidden_role` aunque tenga el rol. Nunca se confía en el prompt del agente.
- **Solo un `builder`:** `require_builder` se aplica a toda ruta y operación, lecturas incluidas; un principal de otro tipo recibe `forbidden_role` aunque su credencial traiga los roles. La lista de roles es cerrada (`constructor`, `aprobador`, `admin`): un rol desconocido no concede nada.
- **`admin` y `aprobador` exigen persona y autenticación reforzada:** además de `attrs.actor = "human"`, el principal debe traer `auth.level = step_up` (ADR 0010; el OTP de la demo es simulado). Con un nivel menor el servicio responde `step_up_required`. Construir (`constructor`) solo exige una sesión.
- **Quién firma:** las credenciales del staff (supervisor, administrador y bot) las emite un emisor propio, con una clave y un `kid` distintos de los del emisor de clientes y asesores. La API del registry se monta con un `authenticate` construido solo con esas claves, así que una credencial de cliente ni siquiera verifica. Los roles salen de los grupos del proveedor de identidad del staff al emitir la credencial; el núcleo solo los valida. En la demo los emite `TestStaffIssuer`, etiquetado como de prueba.
- **Contenido no confiable:** todo lo que propone un agente o un LLM se valida contra el esquema estricto, nunca se ejecuta y tiene límites de tamaño y cantidad.
- **Lo que el constructor lee** (trazas, documentación, páginas) es dato, no instrucción (ADR 0008, M12).
- **Datos:** las suites y los `seed` del sandbox usan solo datos sintéticos. La fuente `dataset` para datos reales está diseñada y desactivada hasta un ADR aparte (ADR 0020).

## 9. Linaje por ejecución
=======
- **Verificación en el servidor.** El agente constructor autentica como principal de tipo `builder` (ADR 0006; no existe un tipo `agent`) con rol `constructor`. El adaptador de tools del registry decide por su **propia credencial** con solo ese rol; el principal del run viaja como actor de auditoría y nunca como fuente de permisos (ADR 0019): una persona con rol `aprobador` que chatea con el constructor no le presta ese rol. Nunca recibe las herramientas de aprobar, publicar, promover ni revocar, y aunque se le pidiera, el servidor las rechaza con `forbidden_role`.
- **Solo aprobación humana.** El campo `actor` de `approve` debe ser una persona. La aprobación automática queda fuera hasta que se especifique (§17).
- **Contenido no confiable.** Todo lo que propone un agente o un LLM se valida contra el esquema estricto, nunca se ejecuta, y tiene límites de tamaño y de cantidad. Las expresiones regulares de reglas de injection pasan por una guarda contra ReDoS.
- **Lo que el constructor lee** (trazas, documentación, páginas) es dato y no instrucción. Las páginas de conocimiento se tratan como `untrusted_text` (ADR 0008, M12).
- **Secretos y PII.** El escáner de §5.1 impide credenciales dentro de entidades. Las suites de evaluación usan solo datos sintéticos (fuente `scripted`). La fuente `dataset` para datos reales está diseñada y desactivada hasta un ADR aparte (ADR 0020).
- **Presupuestos del agente autónomo.** Cada propuesta tiene un tope de borradores, de evaluaciones y de costo, para impedir bucles. Los valores concretos están abiertos (§17).

## 8. Linaje por ejecución
>>>>>>> 4f7743d4a85e3b3adef7e19fa6fa75624985b796

```python
class EntityInRelease(BaseModel, frozen=True):
    ref: EntityRef                       # kind/id@version exacta
    content_hash: str
    docs: VersionDocs
    changed_vs_previous: bool            # frente a la release anterior del mismo agente

class RunLineage(BaseModel, frozen=True):
    run_id: str
    release_id: str
    entities: list[EntityInRelease]
    knowledge_snapshot: EntityRef | None
    proposal_id: str | None
    eval_verdict: EvalVerdict | None
    approved_by: str | None              # actor humano
    published_at: datetime
```

- La traza ya guarda `release_id`; el motor no cambia. `lineage_for_run` lo expande.
- Como las releases son inmutables, el resultado **no cambia con el tiempo**.
- La consulta por `run_id` pasa por la autorización de lectura de runs de M9 (el run pertenece a un principal y a un subject). La consulta por `release_id` solo exige rol `lector`, porque no contiene datos de clientes.
- `diff_releases` y `where_used` responden "qué cambió" y "dónde se usa esta versión".

## 9. Comportamiento en runtime

- `resolve_release` devuelve la release a la que apunta el alias (`prod` por defecto, o `staging` para pruebas). Un run fija su release al iniciar y no la cambia.
- Como una release es inmutable, el motor puede **cachearla por `release_id`** sin riesgo. Lo único que se consulta con TTL corto es `release_status`, para que una revocación surta efecto rápido.
- Al leer cada contenido se verifica su hash. Si no coincide, `get` lanza `IntegrityError` y el motor escala; nunca sirve contenido dudoso.
- El motor no accede a `proposals`, `proposal_changes` ni a releases candidatas.

## 10. Fallas

| Falla | Comportamiento |
|---|---|
| Postgres falla a mitad de `publish` | Se revierte todo. No queda una release parcial. Reintento seguro por `Idempotency-Key`. |
| Dos `publish`/`promote` simultáneos sobre un alias | Bloqueo por alias; el segundo falla con `proposal_stale` o espera. |
| `EvalPort` caído o con tiempo vencido | Ejecución `failed_infra`; sin veredicto no se puede aprobar. |
| `expected_rev` desactualizado | `409 proposal_stale`. |
| Release base cambió antes de publicar | La propuesta pasa a `stale`; hay que reconstruir y reevaluar. |
| Hash de contenido distinto al guardado | `IntegrityError` y alerta. |
| Aprobación sobre una candidata que cambió | `409 candidate_changed`. |

## 11. Eventos y observabilidad

Eventos en `registry_events`, encadenados por hash (`sha256(JCS(evento) ‖ hash_previo)`, como en M11) y **separados** de la cadena de los runs:

`proposal_created`, `draft_updated`, `validated`, `frozen`, `evaluated`, `approved`, `rejected`, `published`, `promoted`, `revoked`, `proposal_staled`.

Cada uno lleva `actor`, `role`, `origin`, `proposal_id`, `candidate_hash` y la marca de tiempo del `Clock`. Los eventos no llevan el contenido de las entidades, solo referencias y hashes.

Spans OTel con prefijo `agentcore.registry.*` (`agentcore.registry.publish`, `.evaluate`…).

## 12. Pruebas

| ID | Caso |
|---|---|
| T-REG-01 | Un `UPDATE` o `DELETE` sobre `entity_versions` falla por permisos y por trigger |
| T-REG-02 | `publish` con una falla inyectada a mitad no deja versiones ni release parciales |
| T-REG-03 | El gate falla si un guardarraíl empeora, aunque la métrica principal mejore |
| T-REG-04 | Sin release base, el gate usa el piso de la suite |
| T-REG-05 | La métrica principal dentro del margen de ruido pasa; fuera del margen no |
| T-REG-06 | Una aprobación se invalida si cambia el `candidate_hash` (`candidate_changed`) |
| T-REG-07 | El rol `constructor` no puede aprobar, publicar, promover ni revocar (`forbidden_role`), comprobado en el servidor |
| T-REG-08 | Una propuesta queda `stale` cuando la release base cambió |
| T-REG-09 | Sin veredicto (evaluación `failed_infra`) no se puede aprobar |
| T-REG-10 | El cambio de una política protegida exige la aprobación de su dueño |
| T-REG-11 | Una página `draft` no la puede aprobar un principal no humano |
| T-REG-12 | `lineage_for_run` coincide exactamente con la lista de entidades de la release del run |
| T-REG-13 | `lineage_for_run` respeta la autorización de M9 sobre el run |
| T-REG-14 | `diff_releases` y `where_used` son consistentes con `release_entities` |
| T-REG-15 | El adaptador Postgres de `RegistryPort` pasa la suite de contrato de `InMemoryRegistry` |
| T-REG-16 | Un contenido con hash alterado lanza `IntegrityError` al leerlo |
| T-REG-17 | El escáner de secretos rechaza una entidad con una clave de ejemplo |
| T-REG-18 | Una entidad por encima de los límites de tamaño se rechaza |
| T-REG-19 | Importar y exportar YAML es determinista (mismo contenido → mismos bytes) |
| T-REG-20 | Dos `publish` concurrentes sobre el mismo alias: solo uno gana, el otro `proposal_stale` |
| T-REG-21 | La cadena de `registry_events` verifica; alterar un evento la rompe |
| T-REG-22 | El motor no puede leer una release candidata ni un borrador por `RegistryPort` |
| T-REG-23 | Un bucle de borradores supera el tope y se corta (presupuesto del agente) |
| T-REG-24 | Revocar una release hace que los runs nuevos no la resuelvan y que los abiertos escalen |

## 13. Evaluación

<<<<<<< HEAD
- [x] `RegistryService`, `BlobStore`, `EvalPort`, `SandboxPort`, `Judge`, `PostgresRegistry` y `SnapshotRegistry` exportados y tipados (`agent_core/registry/__init__.py`; `mypy` strict en verde).
- [x] T-REG-01 a T-REG-27 en verde (suite completa con Postgres: 2956 pasaron, 1 omitida por `AGENT_CORE_PERF`, ajena al registry). Trazabilidad abajo.
- [x] `lint-imports`, `mypy` y `ruff` en verde.
- [x] API montada en M9 (`registry_extension`, `ApiDeps.extensions`) y CLI (`agentcore registry …`).
- [x] La composición del motor usa `PostgresRegistry` (`agentcore serve`, 2026-09-30, tema #13). `serve --registry-api` monta `registry_extension` con evaluador real y el verificador del staff (`registry_extension(service, verifier)`), spec `2026-09-30-serve-registry-api-design.md`.
- [x] `contracts/` regenerado (`agentcore contracts --check` en verde).
- [x] Sin TODO sin issue (no hay `TODO` en `agent_core/`, `testing/` ni `tests/`).
- [x] Módulos de evaluación del ADR 0020 (`agent_core.registry.evaluation.suite`, `.platform`, `.yardstick`, `.double_gate`; pruebas en `tests/registry/yardstick/`): T-EVAL-05 a 08, 11, 12, 13 y 15 en verde; T-EVAL-10 solo en su parte estructural `MT-05`. Pendiente: reconciliarlos con el gate del registry (`evaluation/gate.py`) y el servicio (T-EVAL-09, 16 y 17).
=======
Métricas que consume la unidad 6 y la plataforma:
>>>>>>> 4f7743d4a85e3b3adef7e19fa6fa75624985b796

- tiempo de propuesta a publicación;
- tasa de `gate_failed`, desglosada por métrica;
- propuestas `stale` y abandonadas;
- iteraciones por propuesta del agente autónomo;
- aceptación por `origen` (`builder_chat`, `auto_detect`, `manual`);
- reversiones y revocaciones;
- latencia de `lineage_for_run` y de `resolve_release`.

## 14. Puntos de iteración

- Pasar `BlobStore` a S3 sin tocar el resto (si aparecen adjuntos binarios, corpus grandes o datasets de evaluación pesados).
- Export a git de solo lectura como derivado desechable (no cuenta como fuente de verdad).
- Aprobación automática para ciertos tipos de cambio, cuando se especifique (§17).
- Extraer el registry a un servicio propio: la frontera es `RegistryPort` más `RegistryService`.

## 15. Cambios en otros módulos

Todos **aditivos**; ninguno bloquea lo que está en curso.

### M0 (aplicado el 2026-09-29, `SCHEMA_VERSION` 0.2.0)

<<<<<<< HEAD
- **M0** (2026-09-30, `SCHEMA_VERSION` 1.2.0): `Agent.metrics` y los tipos del DSL de métricas (ADR 0020).

## 16. Fase 2 (diseño de producción, fuera de la entrega)
=======
1. `EntityKind.knowledge_snapshot` en `domain/refs.py`, y un modelo mínimo `KnowledgeSnapshot` (manifiesto de páginas con su metadata y hash) en `domain/entities.py`. Se añade a la unión `RegistryEntity` y al mapa `ENTITY_KIND`.
2. `Release.knowledge_snapshot: EntityRef | None = None` (opcional; `require_exact_refs` ya lo cubre).
3. Regenerar `contracts/` y subir `SCHEMA_VERSION` (versión menor) si ya se generó.
4. `testing/fakes/registry.py` y `InMemoryRegistry` aceptan el tipo nuevo.
5. `Agent.metrics` y los tipos del DSL (`SCHEMA_VERSION` 0.5.0, ADR 0020).
>>>>>>> 4f7743d4a85e3b3adef7e19fa6fa75624985b796

**No cambia:** `RegistryPort`, `EngineEvent`, `ProblemCode` ni las entidades existentes. `eval_suite`, la documentación por versión y los códigos de error del registry viven en el paquete `agent_core.registry`.

<<<<<<< HEAD
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
=======
### M1 (aplicado el 2026-09-29, rev. 3 de M1)

1. `ReleaseDecl`: campo opcional `knowledge`.
2. `pin_release`: incluir el snapshot en la clausura y fijar `Release.knowledge_snapshot` (si aún no está implementado, hacerlo así desde el principio).
3. `load_registry`: lee `knowledge_snapshots/<id>@<versión>.yaml` (manifiestos; el texto de las páginas no viaja en el YAML).
4. **Frontera:** exportar en `flows/__init__.py` las funciones puras que el registry reutiliza (validación de flow, chequeos por agente, `derive_claims`, `pin_release`, `Violation`), y verificar que `AuthoringRegistry` pueda construirse desde objetos en memoria sin pasar por disco.
5. `.importlinter`: contrato nuevo para `agent_core.registry` (usa `domain`, `ports` y la interfaz pública de `flows`).
6. Reglas `MT-01` a `MT-06` en `validate_agent`.

**No cambia:** las reglas G0 ni el mensaje "conocimiento no habilitado (tema #10)": el nodo `knowledge` pertenece a M12.

### Otros documentos

- **M12:** `KnowledgeSource` respaldado por el registry sustituye a `FileKnowledgeSource` para el MVP.
- **`00-descomposicion-y-repos.md`:** el repo `agent-registry` con su CI deja de ser el modelo (ver ADR 0017).
- **`00-indice.md`:** enlace a esta spec.
- **Spec general §6.2:** el gate de release lo ejecuta esta unidad.

## 16. Definición de terminado

- Interfaces `RegistryService`, `BlobStore` y `EvalPort` exportadas y tipadas.
- `T-REG-01` a `T-REG-24` en verde (las que requieren Postgres, en `tests/integration/`).
- El adaptador Postgres pasa la suite de contrato de `RegistryPort`.
- `import-linter`, `mypy` y `ruff` en verde.
- Los eventos de `registry_events` validan contra su esquema.
- Cambios de §15 aplicados en M0 y M1.
- `agent_core.registry.evaluation` con `EvalSuite`, `suite_problems`, `classify_yardstick_change` y `evaluate_gate` (en verde: T-EVAL-05 a 08, 11, 12, 13 y 15; T-EVAL-10 solo en su parte estructural `MT-05`; pendientes del servicio: T-EVAL-09, 16 y 17).
- Sin TODO sin issue.
>>>>>>> 4f7743d4a85e3b3adef7e19fa6fa75624985b796

## 17. Abiertos

1. **Aprobación automática:** por ahora solo aprobación humana. Decidir más adelante si algún tipo de cambio de bajo riesgo puede aprobarse solo.
2. **Autoaprobación por el mismo humano:** si quien pidió un cambio por chat puede aprobarlo él mismo. Hoy no se prohíbe; el agente constructor y el detector nunca aprueban.
3. **Asignación del salto semver:** que el servicio lo proponga a partir del diff y lo confirme el aprobador, o que lo fije el constructor.
4. **Topes del agente autónomo:** valores de borradores, evaluaciones y costo por propuesta.
5. **Contrato con la unidad 6:** `EvalPort` y el formato de `eval_suite` y `EvalReport` los define esta spec como lo que el registry exige; la unidad 6 no tiene spec propia.
6. **Retención** de `registry_events` y de propuestas abandonadas.
7. **Export a git** de solo lectura: si se activa y con qué frecuencia.
8. **Sembrado inicial:** cómo se importa al registry el contenido YAML de la demo (herramienta `agentcore registry import`).

## 18. Dependencias del motor y de los agentes internos (ADR 0019)

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
