# Registry operado por servicios y contrato de eventos salientes: diseño y plan

- Estado: **borrador para decidir. Aquí no hay nada decidido.** Las opciones marcadas como "recomendada" son hipótesis de partida para que el usuario decida, no decisiones.
- Fecha: 2026-10-02 · Rama: `feat/registry-service-y-eventos` (igual a `origin/main`, `59e53c2`).
- Alcance: (A) que un principal `service` (otro sistema, no una persona) **opere el registry**: leer, crear y avanzar propuestas, mientras aprobar y publicar siguen siendo solo de personas con `step_up` (registry, decisión 7); (B) un **contrato de eventos salientes**: el esquema del sobre se publica en `contracts/`, **sin mecanismo de entrega**, porque el transporte es de la unidad 4 (ADR 0013).
- Por qué: los equipos de ciencia de datos, ingeniería de datos e ingeniería de software van a construir sobre el núcleo.
- Regla de CLAUDE.md que se aplica: si el spec es ambiguo, contradice un ADR o toca un "Abierto", **se detiene y se pregunta**. La §2 lista esos puntos; la §3 los convierte en decisiones; la §4 es el plan *sujeto a las decisiones de §3*.

---

## 1. Lo que existe hoy (hechos con archivo:línea)

### 1.1 Principales, roles y scopes

- `PrincipalType` tiene cuatro valores: `customer`, `advisor`, `service` y `builder` (`agent_core/domain/identity.py:10-15`). `Principal` lleva `roles`, `scopes`, `attrs` y `auth`; solo un `customer` puede ser anónimo (`identity.py:75-93`).
- Roles del registry: lista cerrada `constructor`, `aprobador` y `admin` (`agent_core/registry/roles.py:11-14`). `is_human` exige `id` y `attrs.actor == "human"` (`roles.py:18-19`).
- `require_builder` rechaza a todo principal que no sea `builder` con `forbidden_role` (`roles.py:28-32`). `require_approver` y `require_admin` exigen además el rol, `is_human` y `auth.level >= step_up` (`roles.py:41-54`).
- En el runtime (M9), `service` y `builder` son los únicos que pueden fijar una versión o un alias distinto de `prod` (`agent_core/api/authorization.py:26` y `:59-60`). El subject lo piden ellos en el body y se autoriza por scopes (`authorization.py:130-131`; m09 §3, línea 80).
- **Scopes:** el núcleo no define ningún vocabulario. El doble `TableAuthz` usa `subject:<kind>` y `subject:*` como "convención propia del doble, no del spec" (`testing/fakes/authz.py:38-40` y `:81-88`). Un `builder` solo llega a datos de clientes si es el administrador (`authz.py:28-32` y `:82-84`).

### 1.2 Qué puede hacer hoy un `service` en el registry: nada

| Operación | Hoy, con un `service` | Dónde |
|---|---|---|
| Cualquier ruta `/v1/registry/*`, lecturas incluidas | `403 forbidden_role` | `who()` llama a `require_builder` (`agent_core/registry/http.py:105-114`) |
| Cualquier método de `RegistryService` | `forbidden_role` | `require_constructor`, `require_approver` y `require_admin` empiezan con `require_builder` (`roles.py:35-46`) |
| Lecturas sin actor (`get_proposal`, `get_entity`, `get_release`, `diff_releases`) | El servicio no comprueba nada; solo filtra la capa HTTP | `service.py:382`, `:664`, `:674` y `:693`, sin `actor` |
| Prueba que lo fija | `test_only_a_builder_operates_the_registry_even_with_every_role` (parametrizada con `customer`, `advisor` y `service`) | `tests/registry/test_roles_matrix.py:68-70` |

Además, en `serve --registry-api` el `who()` verifica **solo con las claves del staff** (`http.py:108-112`; `agent_core/composition/serve.py:47-48`; `serve_ports.py:211-226`). Por eso una credencial de `service` firmada por el emisor de clientes ni siquiera verifica (401). Para recibir 403, tendría que estar firmada por el emisor del staff.

**Lo que ya funciona sin tocar código:** un sistema externo puede operar el registry **hoy** si se le emite una credencial `builder` **no humana** con el rol `constructor`, igual que al bot constructor (`testing/fakes/identity.py:154-155`; prueba `test_the_bot_builds_but_never_decides_even_if_its_credential_claims_the_roles`, `test_roles_matrix.py:76-79`). Esa credencial puede crear, editar, validar, congelar, evaluar, reabrir y leer, pero nunca aprobar, publicar, promover ni revocar. La auditoría lo vería como `principal_type = builder`, sin distinguirlo del bot interno ni de otro equipo.

### 1.3 Emisión de credenciales

- **No hay servicio de identidad en este repo.** El núcleo solo verifica: `JwsIdentityVerifier` (`agent_core/adapters/jws_identity.py:44-68`) con claves por `kid` cargadas de un archivo (`--identity-keys` para clientes y asesores, `--staff-keys` para el staff).
- El servicio de identidad real figura como pieza pendiente "sin unidad asignada" (TEMAS #13, `docs/specs/TEMAS-ABIERTOS-PENDIENTES.md:93`).
- Emisores de prueba: `TestIdentityIssuer` (cliente, asesor, anónimo, vencido, elevado) y `TestStaffIssuer` (supervisor, administrador y bot) (`testing/fakes/identity.py:128-155`; `testing/demo_identities.py:28-43`). **Ninguno emite un `service`.**
- **El verificador no ata el tipo de principal a la clave:** cualquier `type` firmado con una clave conocida verifica (`jws_identity.py:56-61`). La separación entre clientes y staff depende solo de que cada API cargue un juego de claves distinto.

### 1.4 Límites, topes, auditoría de rechazos y vencimiento

- **Límites de M9** (30 turnos por 60 s y USD 5 al día por principal): `LimitGuard.check` dentro de `admit` (`agent_core/api/limits.py:27-42`; `agent_core/api/app.py:124-141`). Las extensiones reciben `authenticate = admit(...)` (`app.py:295-299`), pero **con `verifier` del staff la API del registry no pasa por `admit` y no tiene límites** (`http.py:106-112`). Es decisión explícita de la spec de `serve --registry-api` (línea 24: "El staff no pasa por los límites por principal de M9 (fuera de alcance)"), que deja como abierto "Autenticación reforzada y límites del staff en la API" (línea 48).
- **Topes del constructor autónomo:** `Quotas(proposals_per_day=10, evals_per_proposal=20)` (`agent_core/registry/quotas.py:10-14`). Solo aplican si `origin is Origin.auto_detect` (`service.py:274-279` y `:450-453`), y la cuenta es **global por `origin`, no por principal** (`registry/memory.py:159-161`; `registry/postgres/store.py:310-315`).
- **Origen declarado por quien llama:** por HTTP, `origin` lo declara el cliente y vale `manual` por defecto (`http.py:21-24`). Un bot que manda `manual` o `import` se salta los topes. Las tools del constructor sí lo restringen a `builder_chat` y `auto_detect` (`agent_core/composition/builder_tools.py:53-55` y `:151-153`).
- **Rechazos:** M9 registra cada rechazo en el log de seguridad y, si hay run, como `access_denied` (`agent_core/api/denials.py:34-58`). **La API del registry no registra sus rechazos en ningún lado:** `RegistryError` se traduce a `problem+json` (`http.py:65-79` y `:94-96`) sin log de seguridad.
- **Vencimiento (hallazgo, previo a este trabajo):** M9 rechaza un principal vencido en el gate (`agent_core/api/gate.py:79-80`). En cambio, `who()` del registry con verificador del staff no mira `exp`, y `JwsIdentityVerifier.verify` tampoco. **Verificado** con un script de solo lectura en el scratchpad: una credencial de bot del staff vencida una hora antes del `Clock` crea una propuesta con `201`. Es un hueco a cerrar antes de abrir la API a sistemas externos (tarea A0).

### 1.5 Huecos de la API HTTP del registry para un cliente externo

- **Sin `Idempotency-Key`** en `create`, `draft`, `freeze`, `reopen` ni `evaluate`. El servicio sí la acepta (`service.py:265-266`, `:297-299`, `:343-344`, `:368-369` y `:438-440`), pero las rutas no la pasan (`http.py:116-145`); solo `publish` la exige (`http.py:157-164`). Un sistema que reintenta tras un timeout duplica la propuesta.
- **Sin listados:** no hay `GET /proposals` (por creador, agente o estado). `list_versions` y `get_write` existen en el servicio (`service.py:686` y `:393`) pero no en HTTP.
- **Sin `AuditContext` por HTTP:** el modelo existe (`registry/models.py:147-151`), pero solo lo usan las tools del constructor (`builder_tools.py:143-148`).
- **El OpenAPI del registry no se publica:** `contracts/openapi.json` se genera con `create_app` **sin extensiones** (`agent_core/api/openapi.py:16-37`), así que las rutas `/v1/registry/*` no están en el contrato. Hoy `openapi.json` trae solo siete rutas de `/v1` (runs, sesiones y handoffs).
- **Sin dueño:** cualquier `constructor` edita, congela, evalúa o reabre **cualquier** propuesta. `put_draft` no compara el actor con `Proposal.created_by` (`service.py:297-324`; `models.py:68-78`).
- **Tipos de entidad editables:** todos los de M0 (`agent`, `flow`, `decision_model`, `policy`, `template`, `prompt`, `tool`, `language_detection`, `injection_ruleset`, `model_profile` y `knowledge_snapshot`; `agent_core/domain/refs.py:42-54`) más `eval_suite` (`registry/entities.py:13-17`). Solo se vetan los guardarraíles de plataforma (`platform_edits`, `service.py:305-309`).

### 1.6 Auditoría del registry

- `RegistryEvent` guarda `type`, `actor` (id), `principal_type`, `origin`, `proposal_id`, `candidate_hash`, `release_id` y `at` (`registry/models.py:133-141`; lo escribe `_event`, `service.py:178-183`). No guarda `attrs` del actor, `trace_id`, emisor ni `kid`.
- Tabla `reg_events (seq bigserial, event_json)` (`registry/postgres/schema.sql:33`), **sin cadena de hash**: la cadena es de la fase 2 (registry §16).
- Tipos emitidos: `proposal_created`, `draft_updated`, `frozen`, `reopened`, `evaluated`, `approved`, `rejected`, `proposal_staled`, `published`, `promoted`, `revoked` e `imported` (`service.py:293-776`). **La spec §12 omite `proposal_staled`**, que el código sí emite (`service.py:558`): es una pequeña deriva entre spec y código.

### 1.7 Eventos del motor, outbox y contratos

- **Cadena por run:** `EngineEvent {event_id, run_id, turn_id?, session_id?, release, ts, seq?, prev_hash?, hash?}` (`agent_core/domain/events.py:26-38`). Son 26 tipos en `AnyEvent` (`events.py:551-578`), cada uno con su emisor en `EVENT_EMITTERS` (`events.py:593-621`). Los payloads están en vista `audit`. `run_started` solo lleva `subject_kind`, **nunca el `subject.ref`** (`events.py:43-50`).
- **Persistencia:** `audit_events`, de solo inserción impuesta por trigger, con `PRIMARY KEY (run_id, seq)` y `event_id UNIQUE` (`agent_core/adapters/sql/audit_events.sql:2-13`). **No hay cursor global:** `AuditSink.read(run_id)` solo lee por run (`agent_core/ports/audit.py:7-12`).
- **Outbox:**
  - `OutboxMessage {message_id, type: Literal["handoff_created"], run_id, payload, created_at}` (`agent_core/domain/shared.py:61-67`).
  - Se escribe con `UnitOfWork.enqueue_outbox` en la transacción del turno (`agent_core/ports/uow.py:69`; `agent_core/adapters/postgres_uow.py:242-244` y `:311-313`).
  - Tabla `outbox (message_id PK, seq bigserial, message_json, delivered_at)` (`adapters/sql/schema.sql:62-69`).
  - `PostgresOutbox` entrega at-least-once en orden de inserción, y una entrega marcada no se reencola (`postgres_uow.py:345-363`).
  - El puerto `Outbox` "lo consume la unidad 4" (`ports/audit.py:15-20`). **En el núcleo nadie llama a `pending` ni a `mark_delivered`.**
- **Único evento saliente hoy:** `handoff_created`, que construye M10 (`agent_core/handoff/service.py:104-113`) con `HandoffCreatedPayload {handoff_ref, run_id, target_queue, priority, reason_code, language, reportable_attrs}` (`events.py:380-391`). `reportable_attrs` sale de `principal.attrs` filtrado por la lista de la política (`handoff/service.py:103` y `:109-110`).
- **Contratos:**
  - `agentcore contracts` genera un JSON Schema por cada modelo o enum exportado en `agent_core.domain` y `agent_core.ports`, en `contracts/schemas/<Tipo>.json`, más `contracts/VERSION` (`agent_core/contracts.py:28-54`), y `openapi.json` (`cli.py:325-334`).
  - Gestiona solo `schemas/` y `VERSION` (`contracts.py:57-65`): una carpeta `contracts/events/` necesitaría cambiar el generador.
  - Hoy hay 193 esquemas y `VERSION = 1.3.0` (`agent_core/domain/version.py:6`).
- **Regla de evolución (m00 §9):** un tipo de evento nuevo o un campo opcional es versión menor; cambiar un campo existente es mayor. Riesgo conocido de la cadena (m11 §11, riesgo 4): agregar un campo a un evento **de la cadena** cambia los hashes recalculados de las cadenas históricas.
- **Exportación para la unidad 6:** `export_events(sink, run_ids, release)` (`agent_core/audit/export.py:12-20`).

### 1.8 Lecturas de runs para un `service`

- `GET /v1/runs/{id}`, `/transcript`, `/v1/sessions/{id}/lineage` y `/v1/handoffs/{ref}` pasan por `admit` (límites incluidos).
- `authorize_read` deja leer al dueño o a quien `AuthzPort.authorize_subject` autorice. A un `service`, "por scope" (`authorization.py:89-97`). Un run sin subject solo lo lee su dueño.
- El transcript se renderiza con los permisos del lector (M7, ADR 0013). El `AuthzPort` real es de la unidad 3.

---

## 2. Qué está abierto o decidido en la documentación (citas textuales)

**Lo que (A) choca con algo ya decidido (hay que reabrirlo):**
- Registry, §8, tabla de roles (`docs/specs/2026-09-29-registry-design.md:375`): "(cualquier `builder` autenticado) | leer entidades, releases, diffs, propuestas y reportes. Un `customer`, un `advisor` o un `service` no consultan el registry".
- Registry, §8 (línea 380): "Todos son `builder`: no hay un tipo de principal nuevo."
- Registry, §8 (línea 384): "**Solo un `builder`:** `require_builder` se aplica a toda ruta y operación, lecturas incluidas; un principal de otro tipo recibe `forbidden_role` aunque su credencial traiga los roles. La lista de roles es cerrada (`constructor`, `aprobador`, `admin`)".
- TEMAS #14, **resuelto** el 2026-09-30 (`TEMAS-ABIERTOS-PENDIENTES.md:109`): "**Solo un `builder` opera el registry**, lecturas incluidas; la lista de roles es cerrada."
- TEMAS #14 (línea 111): "**Emisores:** el staff tiene su propio emisor y su propia clave; la API del registry solo verifica esas claves".

→ Abrir el registry a `service` **reabre una decisión cerrada** (tema #14 y registry §8). No se resuelve aquí: es la decisión A1.

**Lo que está documentado como abierto:**
- m09 §11, Abiertos (`docs/specs/motor/m09-acceso-y-api.md:218`): "**Copiloto y constructor (ADR 0019):** el vocabulario de `purpose` para el copiloto y los scopes del `builder` siguen sin definir; la prueba de contrato fija solo las negaciones."
- m09 §11 (línea 219): "**`AuthzPort` sin especificar:** qué significa `subject=None` en `authorize_agent`, las claves de `bind_params`, el vocabulario de `purpose` y los scopes de `service`/`builder` (`TableAuthz` usa `subject:<kind>`/`subject:*` como convención propia del doble)."
- Registry §18, fila 5 (línea 582): "**Roles del registry frente a `Principal.roles`** (lista libre) y sus scopes | constructor, `aprobador` | Definir cómo se emiten y quién los firma". La rev. 2 (línea 572) la da por cubierta "con los roles `constructor` y `aprobador` en `Principal.roles` más `attrs.actor`", pero **los scopes nunca se definieron**.
- Spec de `serve --registry-api` §7 (línea 48): "Autenticación reforzada y límites del staff en la API."
- TEMAS #16 (línea 123): "Sigue sin fijar el **monto del tope de costo por propuesta** (USD)".
- Registry §16, fase 2: "**Presupuestos del agente autónomo** (borradores, evaluaciones y costo por propuesta)", "**Rol `lector` explícito** y autorización del linaje por dueño del run (M9)", "**Aprobación de cuatro ojos** configurable (prohibir la autoaprobación)" y "**Cadena de hash** en `registry_events`".
- ADR 0018 punto 6: "La aprobación automática y la autoaprobación por el mismo humano quedan como temas abiertos". La enmienda 2026-09-30, punto 4, ya cerró la autoaprobación (se permite). Este trabajo no la cambia.

**Lo que (B) toca:**
- ADR 0013: "El destino de entrega (webhook, cola o tabla) se define en la unidad 4 y se itera después."
- Spec general §14 (línea 731), unidad 4: "outbox de eventos salientes y su entrega (destino por definir en esa unidad)".
- ADR 0002: "Publica `contracts/` con el OpenAPI de sus APIs y los JSON Schemas de entidades y eventos, versionados con **semver**", y "el núcleo solo conoce … los eventos que exporta". Hay un solo `VERSION` para todo `contracts/`.
- ADR 0002, Consecuencias: "La auto-mejora interactúa solo mediante eventos (lectura) y PRs a `agent-registry` (escritura)". Los ADR 0017 y 0018 ya reemplazaron los PRs por propuestas por API; la parte de "eventos (lectura)" sigue vigente y es lo que (B) concreta.
- m00 §9: "Agregar un tipo de evento: versión menor; M11 y la unidad 4 lo aceptan sin cambios" y "Cambiar un campo existente … versión mayor".
- m11 §11, riesgo 4 (línea 269): "agregar cualquier campo a un evento de M0 cambia el hash recalculado de todos los eventos guardados … Abierto para la regla de evolución de M0".
- TEMAS #18: `privileged_read` está decidido, pero su esquema está "pendiente de construcción". Si se quisiera hacerlo público, depende de cerrarlo primero. No se propone hacerlo público.

---

## 3. Decisiones abiertas (las decide el usuario)

### 3.A Registry operado por servicios

| # | Decisión | Opciones | Ventajas y costos | Recomendación (hipótesis) |
|---|---|---|---|---|
| A1 | **Qué tipo de principal opera el registry desde otro sistema.** Reabre TEMAS #14 y registry §8 | (a) **No cambiar el tipo:** cada sistema recibe un `builder` no humano con `constructor`, firmado por el emisor del staff. (b) **Admitir `service`** en el registry, con permisos por scope. (c) Un `PrincipalType` nuevo | (a) Cero código, y la regla "un `builder` no ve datos de clientes" ya aplica. Pero mezcla staff interno con sistemas de otros equipos, la auditoría no los distingue (`principal_type = builder`) y el mismo sistema necesita otra credencial `service` para el runtime. (b) Respeta ADR 0006 (`service` se autoriza por scopes) y distingue el actor en la auditoría, pero reabre #14 y cambia `require_builder`. Riesgo: un `service` con `subject:*` reúne datos de clientes y escritura en el registry en una sola credencial (canal de fuga hacia entidades legibles por todos). (c) Cambia M0 y `contracts/` (ADR 0019 ya descartó `agent` por esto) | **(b)**, con una condición dura: una credencial con scopes del registry **no puede** traer scopes `subject:*` (el registry la rechaza). Si se prefiere no reabrir #14, (a) con una etiqueta de equipo firmada (A4) es la alternativa barata |
| A2 | **Qué operaciones puede hacer un `service`** | Leer: propuestas, entidades, versiones, releases y diff. Construir: `create_proposal`, `put_draft`, `validate`, `freeze`, `reopen`. Evaluar: `evaluate`. Linaje de un run (`lineage_for_run`). "Retirar" una propuesta: **no existe** (`abandoned` es de la fase 2; hoy solo `reopen`). Nunca: `approve`, `reject`, `publish`, `promote`, `revoke`, `import_seed` | Todo lo de construir ya lo hace el bot. Evaluar cuesta LLM y es síncrono. El linaje expone autoría y aprobación (hoy exige `constructor`, `service.py:711`). Un "retirar" nuevo es un estado nuevo (fase 2) o un `reopen` | Leer y construir: sí. Evaluar: sí, con scope aparte (por costo). Linaje: solo con el scope de lectura y, si se decide A4, solo de agentes del equipo. Retirar: **no** en este trabajo (sigue en la fase 2). `reject` sigue humano (es de `aprobador`) |
| A3 | **Cómo se nombran los permisos** | (a) Reusar `roles` (`constructor`) también para `service`. (b) Scopes nuevos: `registry:read`, `registry:write`, `registry:evaluate`. (c) Ambos | (a) Mezcla los grupos del proveedor de identidad del staff con permisos de sistemas. (b) Sigue ADR 0006 (`service` por scopes) y abre un vocabulario que m09 §11 deja sin definir, así que hay que fijarlo en un spec. (c) Dos fuentes de verdad | **(b)**, como lista cerrada (un scope desconocido no concede nada, igual que los roles). Para un `service`, los roles `aprobador` y `admin` se ignoran **siempre** (falla cerrado) |
| A4 | **Dueño, equipo y espacio de nombres** | (a) Nada (como hoy: cualquiera edita cualquier propuesta). (b) `attrs.team` firmado y obligatorio para `service`, guardado en la propuesta; solo el mismo equipo (o un humano) edita, congela, evalúa o reabre. (c) (b) más una lista de agentes por scope (`registry:agent:<id>`) | (a) Un sistema de otro equipo puede pisar el borrador ajeno. (b) Poco código en el registry (`Proposal.owner_team`, registry-local, no M0). (c) Más fino, pero hace falta decidir quién es dueño de un `agent_id` y no hay tabla de dueños | **(b)** ahora; **(c)** cuando haya dueños por agente. Sin `attrs.team`, el `service` recibe `forbidden_role` |
| A5 | **Qué tipos de entidad puede proponer un `service`** | (a) Todos, como el bot. (b) Lista permitida: `agent`, `flow`, `prompt`, `template`, `decision_model` y `eval_suite`. Fuera: `policy`, `tool`, `injection_ruleset`, `model_profile`, `language_detection` y `knowledge_snapshot` | (a) Un sistema puede proponer ampliar `tools_allowed`, `invocable_by` o una política, y el único freno es que el humano lo note al aprobar. (b) Reduce la superficie. Costo: un equipo que necesite un `model_profile` pasa por una persona | **(b)**. Además, la vista de aprobación debe resaltar cambios a `invocable_by`, `tools_allowed` y `eval_suite` (va a la plataforma, fuera del núcleo) |
| A6 | **Impedir que un `service` apruebe o se escale solo** | Defensas: (1) `is_human` exige `type == builder` además de `actor = "human"`; (2) A3: un `service` nunca obtiene `aprobador` ni `admin`; (3) aflojar la vara en `eval_suite` ya exige `accept_yardstick_loosened` humano; (4) guardarraíles de plataforma ya vetados; (5) A5; (6) el contenido del borrador es dato no confiable para el aprobador y para el constructor (ADR 0008) | (1) y (2) son baratas y se prueban con una matriz. La aprobación de cuatro ojos (prohibir que el mismo humano que opera el sistema apruebe) es de la fase 2 | (1), (2) y la prueba de matriz extendida. **No** activar cuatro ojos aquí; queda en la fase 2 |
| A7 | **Atributos de auditoría del actor** | (a) Como hoy (`actor`, `principal_type`). (b) Agregar a `RegistryEvent` y `DraftWrite`: `actor_team`, `credential_kid` y `trace_id`. (c) (b) más `attrs.actor` | El cambio es registry-local (no toca M0). El `kid` sirve para rastrear una credencial filtrada. `attrs.actor` de un `service` siempre falta, por diseño | **(b)**. Además, registrar los rechazos del registry en el `SecurityLog` (código, tipo de principal y `trace_id`, sin el detalle) |
| A8 | **Quién emite la credencial de un `service`** | (a) El emisor del staff (mismo juego de claves). (b) Un emisor de servicios con su archivo de claves (`--service-keys`) y **atado al tipo**: lo que firman esas claves solo vale si es `type = service`. (c) El emisor de clientes | (a) Un error del emisor del staff podría producir un `service` con roles humanos. (b) Separa claves y permite revocar por `kid`, pero exige cambiar el verificador o envolverlo en `composition`. (c) Amplía la superficie del registry a las claves de clientes | **(b)**, implementado como envoltorio en `composition` (no se toca `JwsIdentityVerifier`). **No se sabe** quién operará ese emisor (TEMAS #13: "sin unidad asignada") |
| A9 | **Límites y topes** | (a) Ninguno (como hoy con el staff). (b) Por principal en la API del registry: tasa por ventana más topes de propuestas por día y evaluaciones por propuesta **por actor**, no por `origin`. (c) (b) más un tope de costo en USD | Hoy los topes son globales y el `origin` lo declara quien llama (§1.4). (c) depende del monto que TEMAS #16 deja sin fijar | **(b)**: los topes de `Quotas` se aplican a todo principal no humano por actor, y por HTTP un no humano no puede declarar `origin = manual` ni `import`. El monto de (c) lo decide el usuario |
| A10 | **Huecos de HTTP para clientes externos** | `Idempotency-Key` en las escrituras; `GET /proposals` (filtros: `agent_id`, `state`, `created_by`, `owner_team`); `GET /entities/{kind}/{id}/versions`; `GET /writes/{key}`; OpenAPI del registry en `contracts/` | Sin idempotencia, un reintento duplica. Sin OpenAPI publicado, otros equipos dependen del código (contra ADR 0002) | Todo, salvo que el usuario recorte. El OpenAPI del registry iría como `contracts/openapi-registry.json`, o dentro de `openapi.json` montando la extensión al generar (sub-decisión) |
| A11 | **Vencimiento en la API del registry** (§1.4) | Comprobar `exp` contra el `Clock` en `who()` (o en un envoltorio del verificador) | Es un hueco ya existente y aplica también al staff | Corregirlo **antes** de A1–A10 (tarea A0). Cambia el comportamiento con el staff: requiere el visto bueno del usuario |

### 3.B Contrato de eventos salientes

| # | Decisión | Opciones | Ventajas y costos | Recomendación (hipótesis) |
|---|---|---|---|---|
| B1 | **Qué eventos son públicos** | (a) Todos los de la cadena. (b) **Lista cerrada.** (c) Solo `handoff_created` | (a) Expone la estructura interna (`node_entered`, `rule_evaluated`), señales de seguridad (`injection_flagged`, `access_denied`) y ata a los consumidores a cada cambio de M0. (c) No cubre "run cerrado" ni "propuesta publicada" | **(b)**. Versión 1: del motor, `run.started`, `run.closed`, `handoff.created`, `handoff.resolved` y `run.transferred`; del registry, `release.published`, `release.promoted` y `release.revoked` (opcional: `proposal.evaluated`). Fuera: eventos de turno, decisión, acción, seguridad y medición |
| B2 | **Campos del sobre** | Mínimo: `spec_version`, `event_id`, `type`, `occurred_at`, `source` (`engine` o `registry`), `data`. Correlación opcional: `run_id`, `session_id`, `turn_id`, `release_id`, `agent: EntityRef`, `subject_kind`. Opcional: `audit_ref {run_id, seq, hash}` | Nombres al estilo CloudEvents (`id`, `source`, `type`, `time`, `specversion`): facilitan Kafka o EventBridge, pero el `subject` de CloudEvents choca con nuestro `subject` (el cliente). `trace_id` no viaja en la cadena; para incluirlo habría que tomarlo en la emisión | Sobre propio con nombres cercanos a CloudEvents, **sin campo `subject`**: solo `subject_kind`. `event_id` = el `event_id` del evento de la cadena que lo origina (para el registry, `reg-<seq>`), que sirve de clave de deduplicación. `occurred_at` = `ts` del evento (`Clock`). Sin `trace_id` en la v1 |
| B3 | **PII y datos sensibles** | `data` es una proyección **explícita** por tipo (modelos cerrados, `extra = forbid`) de payloads que ya están en vista `audit`. Nada de texto libre, `subject.ref`, slots ni texto del usuario. Puntos dudosos: `run_transferred.reason` es texto literal del flow (lo escribe un autor, no el cliente); `handoff_created.reportable_attrs` son atributos gruesos de la lista de la política (p. ej. `country`); `handoff_resolved.resolution_code` lo escribe el asesor | Cada campo dudoso es una decisión | Excluir `reason` y `notes`. Incluir `reportable_attrs` **solo** si el usuario lo confirma: ya salen hoy en `handoff_created`, pero hacerlos públicos amplía la audiencia. Agregar una prueba que recorra los ejemplos sintéticos y falle si aparece un token PII o un valor de la vista `full` (como `tests/m04/test_pii.py`) |
| B4 | **Versionado y compatibilidad** | (a) La versión única de `contracts/VERSION` (ADR 0002). (b) Una versión propia del contrato de eventos. (c) Una versión por tipo | (a) Cualquier cambio de M0 mueve la versión que miran los consumidores externos (ruido). (b) Contradice "un solo semver" de ADR 0002 salvo enmienda. (c) La más fina, la más costosa | **(a)** más `spec_version` (entero mayor del sobre). Regla publicada: los consumidores ignoran campos y tipos desconocidos; un campo opcional o un tipo nuevo es menor; quitar o cambiar un campo es mayor o un tipo nuevo `*.v2`. Una prueba de compatibilidad congela los esquemas v1 (deriva, §5) |
| B5 | **Dónde vive** | (a) **En M0:** `agent_core/domain/outbound.py`, y el generador los publica en `contracts/schemas/` sin cambios. (b) Fuera de M0: un paquete nuevo (p. ej. `agent_core/outbound`) con su generador y `contracts/events/*.json` | (a) Cambio de M0 (tipos nuevos: **versión menor 1.3.0 → 1.4.0**, regla 7 de CLAUDE.md: regenerar y avisar). Coherente con `HandoffCreatedPayload`, que ya está en M0. (b) No toca M0, pero exige un contrato nuevo en `.importlinter`, cambiar `contracts.py:57-65` y decidir si M4/M10 pueden importarlo | **(a)**, con un índice generado `contracts/events/catalog.json` (lista de tipos públicos, su esquema y `spec_version`), que es un cambio pequeño en `contracts.py`. Si el usuario prefiere no tocar M0 en esta etapa, (b) |
| B6 | **Garantías que promete el contrato** | At-least-once; deduplicar por `event_id`; orden garantizado **solo dentro de un run** (los turnos de un run están serializados por lease) y dentro del registry por `seq`; sin orden global entre runs; sin promesa de latencia ni de transporte | Prometer orden global ataría a la unidad 4 | Lo de la columna anterior, escrito en el spec. La entrega es de la unidad 4 |
| B7 | **Relación con la cadena de auditoría y con el outbox** | (E1) Proyectar **en la transacción**: M4 y M10 encolan un `OutboxMessage` por evento público, y el registry escribe en una tabla `reg_outbox` en su propia transacción. (E2) Un *relay* (unidad 4) proyecta desde `audit_events` y `reg_events` con cursor. (E3) **Solo contrato y proyector puro**; la emisión queda para después | (E1) Atómico y sigue el patrón de `handoff_created`, pero cambia M4, M10 y el registry, y amplía `OutboxMessage.type` (M0). (E2) No toca M4, pero `AuditSink` no tiene cursor global (puerto nuevo o una consulta solo de Postgres). (E3) Cumple el alcance pedido ("sin mecanismo de entrega") y deja E1 o E2 a la unidad 4 | **(E3)** en este trabajo: esquemas más `project(event) -> OutboundEvent \| None` puro, probado con *golden files*. E1 o E2 es una decisión aparte, de la unidad 4 |
| B8 | **`handoff_created` actual** | (a) Se mantiene `OutboxMessage` y el sobre público es lo que publicará la unidad 4 (el proyector lo envuelve). (b) Reemplazar `OutboxMessage` por el sobre | (b) Cambia un tipo existente (M0 **mayor**). Hoy no hay consumidor, pero no se sabe si la unidad 4 ya lo usa fuera del repo | **(a)** |
| B9 | **Si el registry usa el mismo sobre** | (a) El mismo, con `source = registry`. (b) Un contrato aparte | `OutboxMessage.run_id` es obligatorio y los eventos del registry no tienen run. El sobre nuevo lo hace opcional sin tocar `OutboxMessage` | **(a)**. Si se elige E1 para el registry, la tabla `reg_outbox` es propia del registry (registry-local) |

---

## 4. Plan de tareas (TDD) — **sujeto a las decisiones de §3**

Escrito como si se aceptaran las recomendaciones. Si una decisión cambia, la tarea correspondiente se reescribe. Cada tarea: pruebas primero (que fallen), implementación y, al cerrar, `uv run pytest tests/<área>`, `uv run lint-imports`, `uv run mypy`, `uv run ruff check .` y, si toca M0, `uv run agentcore contracts` y `--check`. Cada cambio de comportamiento actualiza el spec en el mismo cambio.

### Fase A — Registry para `service`

**A0. Vencimiento en la API del registry** (A11; requiere visto bueno porque cambia el comportamiento con el staff)
- Pruebas: `tests/registry/test_http.py`: una credencial del staff vencida (`TestStaffIssuer(clock, ttl=-1h)`) recibe `401` (código `principal_expired` o `credentials_invalid`, sub-decisión) en cada ruta. Una vigente sigue en 2xx.
- Implementación: `registry_extension(service, verifier, clock)` compara `principal.exp <= clock.now()` en `who()`. `serve.py:47-48` pasa `ports.clock`.
- Revisión: que no se pida la hora del sistema (CLAUDE.md, regla 2) y que la ruta sin verificador siga pasando por `admit`.

**A1. Spec primero:** enmiendas a `docs/specs/2026-09-29-registry-design.md` §8 (tabla de roles, nueva fila `service`, scopes), §7.4 (rutas y códigos nuevos) y §12 (agregar `proposal_staled`); TEMAS #14 (reabierto y cerrado de nuevo con la decisión del usuario); m09 §11 (vocabulario de scopes `registry:*`); ADR 0006 (enmienda: `service` en el registry) y, si aplica, ADR 0019 §4.

**A2. Autorización por tipo y scope** (`agent_core/registry/roles.py`)
- Interfaz nueva:
  - `REGISTRY_SCOPES = frozenset({"registry:read", "registry:write", "registry:evaluate"})`;
  - `require_reader(p)`;
  - `require_constructor(p)` acepta `builder` + `constructor` **o** `service` + `registry:write`;
  - `require_evaluator(p)`: `builder` + `constructor`, o `service` + `registry:evaluate`;
  - `is_human(p)` exige además `p.type is PrincipalType.builder`.
  - Un `service` con scopes `registry:*` y algún scope `subject:` → `forbidden_role` (A1). Un `service` sin `attrs.team` → `forbidden_role` (A4).
- Pruebas: `tests/registry/test_roles_matrix.py` crece con:
  - `service` + todos los scopes y todos los roles → construye y evalúa, nunca aprueba, publica, promueve, revoca, importa ni rechaza, **aunque traiga `actor = "human"` y `step_up`**;
  - `service` con scope desconocido → nada;
  - `service` con `registry:read` → solo lecturas;
  - `customer` y `advisor` siguen sin nada.
- La prueba actual `test_only_a_builder_operates_the_registry_even_with_every_role` se reescribe: `service` sale del parámetro y pasa a la matriz nueva. **Es un cambio de un invariante probado: revisión con lupa.**
- Lecturas: hoy `get_proposal` y las demás no reciben `actor` (`service.py:382` y siguientes). Opción mínima: la capa HTTP llama a `require_reader`. Opción más robusta (y recomendada): que el servicio reciba `actor` también en las lecturas, porque A4 lo necesita para filtrar.
- Revisión: falla cerrado, sin rutas que omitan `who()`, y la barrera humana probada con un `service` que finge ser humano.

**A3. Dueño y equipo** (`registry/models.py`, `service.py`, `memory.py`, `postgres/store.py`, `postgres/schema.sql`)
- `Proposal.owner_team: str | None = None` (registry-local; el JSON de `reg_proposals` lo admite sin migración de columnas; las filas viejas quedan en `None`).
- Regla: si la propuesta tiene `owner_team`, un `service` de otro equipo recibe `forbidden_role` en `put_draft`, `freeze`, `evaluate`, `reopen` y `validate`. Un `builder` humano sí puede (sub-decisión: ¿el bot interno también?).
- Pruebas: `tests/registry/test_service_ownership.py` (memoria) y su caso en `tests/integration/test_registry_postgres.py`.

**A4. Tipos permitidos para `service`** (`registry/validation.py` o `service.py`)
- `SERVICE_EDITABLE_KINDS`. Un borrador de un `service` con otro tipo → `forbidden_role` con la lista de rutas (como `platform_edits`).
- Pruebas: un `service` que propone `tool` o `policy` es rechazado; un `builder` humano no.

**A5. Topes por actor y origen forzado** (`quotas.py`, `service.py`, `store.py`)
- `RegistryTx.count_created_by_after(actor_id, after)` (memoria y Postgres, consulta sobre `reg_events`).
- Los topes aplican a todo principal no humano por actor. Por HTTP, un no humano solo puede declarar `auto_detect` o un `Origin.service` nuevo (sub-decisión; `Origin` es registry-local, no M0).
- Pruebas: un `service` que manda `origin = manual` igual cuenta; dos servicios no se comen el tope del otro; `evaluate` número 21 → `quota_exceeded`.
- Revisión: que la ventana use el `Clock`, y que las consultas de Postgres usen el mismo filtro que la memoria (suite de contrato).

**A6. Límites de tasa en la API del registry** (composition)
- La extensión recibe un `limit: Callable[[Principal], None]`. En `serve` se construye con `LimitGuard` sobre `CostCounters` y una `RateLimitConfig` propia del registry (la evaluación no registra `add_usage` en los contadores de M9: **no se sabe** si debe hacerlo; sub-decisión).
- Pruebas: la llamada N+1 dentro de la ventana → `429 rate_limited`.

**A7. Auditoría del actor y rechazos**
- `RegistryEvent` y `DraftWrite` ganan `actor_team: str | None`, `credential_kid: str | None` y `trace_id: str | None` (registry-local).
- Hoy `Principal` no trae el `kid`: habría que agregarlo en `attrs` o devolverlo aparte desde el verificador. Esto es una **sub-decisión que podría tocar M0**; si no se quiere tocar M0, el `kid` queda fuera.
- `registry_extension` recibe un `on_denied(code, principal_type, trace_id)` que en `serve` es el `SecurityLog`.
- Pruebas: cada `RegistryError` de autorización deja una entrada en el log de seguridad sin `detail`; `reg_events` lleva `actor_team`.

**A8. HTTP para clientes externos** (`registry/http.py`)
- Encabezado `Idempotency-Key` opcional en `create`, `draft`, `freeze`, `reopen` y `evaluate`, pasado al servicio.
- `GET /proposals?agent_id=&state=&owner_team=` (filtrado por equipo para un `service`); `GET /entities/{kind}/{id}/versions`; `GET /writes/{key}`.
- Pruebas: un reintento con la misma clave no duplica; misma clave con otro cuerpo → `409 idempotency_conflict`.

**A9. Emisor y verificador de servicios** (composition y testing)
- `TestServiceIssuer` en `testing/fakes/identity.py` (claves de PRUEBA, `kid` propio) y `demo_identities.py` emite `registry_service`.
- `composition`: `TypedVerifier(inner, allowed_types={service})` y `CompositeVerifier([staff, service])`. `serve --service-keys`.
- Pruebas:
  - una credencial `builder` firmada con la clave de servicios → 401;
  - una `service` con la clave del staff → 401 (si se elige atar por tipo en los dos sentidos);
  - una `service` con la clave de clientes → 401.

**A10. OpenAPI del registry en `contracts/`** (`agent_core/api/openapi.py` o `composition`)
- Generar con la extensión montada sobre un servicio sin cablear. `agentcore contracts --check` lo cubre.
- Revisión: que generar no ejecute ningún handler.

### Fase B — Contrato de eventos (sin entrega)

**B0. Spec primero:** una spec nueva `docs/specs/2026-10-xx-eventos-salientes-design.md` con la lista cerrada, el sobre, la proyección por tipo, las garantías, la regla de compatibilidad y la relación con la cadena. Agregar una fila en m00 §2.10 y m00 §9, la nota de la unidad 4 en la spec general §14 y un ADR o una enmienda a ADR 0013 (el contrato es del núcleo; la entrega, de la unidad 4).

**B1. Tipos en M0** (`agent_core/domain/outbound.py`, exportados en `domain/__init__.py`)
- Modelos:
  - `OutboundEvent` (`spec_version: Literal[1]`, `event_id`, `type: OutboundType`, `occurred_at: UtcDatetime`, `source: Literal["engine", "registry"]`, `run_id?`, `session_id?`, `turn_id?`, `release_id?`, `agent: EntityRef | None`, `subject_kind?`, `audit_ref: AuditRef | None`, `data`);
  - `data` es una unión discriminada por `type` de payloads cerrados (`RunClosedData`, `HandoffCreatedData`, `ReleasePublishedData`, …), cada uno con solo identificadores, enums y referencias.
- `SCHEMA_VERSION` 1.3.0 → **1.4.0** (menor, tipos nuevos). Correr `uv run agentcore contracts` y **avisar a todos los módulos** (CLAUDE.md, regla 7).
- No se toca ningún evento de la cadena (riesgo de hash, m11 §11, riesgo 4) ni `OutboxMessage`.
- Pruebas `tests/m00/`: los esquemas existen en `contracts/schemas/`; `extra` se rechaza; ningún payload tiene un campo `str` libre fuera de la lista permitida (prueba por introspección de los modelos).

**B2. Proyector puro** (`agent_core/domain/outbound.py`: `project_engine(event: EngineEvent) -> OutboundEvent | None`)
- Lugar: en M0, para que M4 y M10 o la unidad 4 lo usen sin cruzar fronteras. Sub-decisión: si se prefiere `agent_core/audit`, M11 ya es dueño de la lectura de cadenas.
- La proyección del registry vive en `agent_core/registry` (`project_registry(e: RegistryEvent, seq: int)`) porque `RegistryEvent` no es de M0.
- Pruebas *golden*:
  - para cada fixture de `tests/fixtures/runs*`, la lista de eventos públicos proyectados es la esperada;
  - proyectar dos veces da los mismos bytes (`canonical_bytes`);
  - un tipo fuera de la lista da `None`;
  - prueba de PII: ningún valor proyectado aparece entre los valores de la vista `full` del mundo sintético ni contiene `⟦pii:`.

**B3. Índice publicado** (`agent_core/contracts.py`)
- `contracts/events/catalog.json` con `{spec_version, types: {type: schema_file}}`, generado y verificado por `--check`. `_existing` pasa a gestionar también `events/`.
- Prueba de compatibilidad: una copia congelada de los esquemas v1 en `tests/contracts/outbound_v1/` (o un hash) y una prueba que falla si un campo obligatorio desaparece o cambia de tipo sin subir `spec_version`.

**B4 (fuera de alcance, solo si el usuario lo pide): emisión**
- E1: `OutboxMessage.type` gana los tipos públicos (M0, menor) y M4 encola `run.closed` en `closing.py`. Ojo: los ids de `message` no se graban en el replay (`testing/replay/runner.py:33-37`), así que no cambian los fixtures; **verificarlo con T-M11-15**. El registry escribe en `reg_outbox`.
- E2: cursor sobre `audit_events` y `reg_events` para la unidad 4.

---

## 5. Riesgos

- **Escalada de privilegios.**
  - Un `service` que llegue a aprobar es el riesgo principal (ADR 0018). Se mitiga con `is_human` atado a `builder`, roles ignorados para `service`, emisor separado y atado al tipo, y una matriz de pruebas que recorra todas las operaciones.
  - Riesgo residual: un emisor mal configurado que firme `builder` + `actor = "human"` para un sistema. **El núcleo no puede detectarlo**; depende del servicio de identidad, que no existe aquí.
- **Fuga de datos de clientes hacia el registry.** Un `service` con `subject:*` que además escribe borradores podría copiar datos reales a prompts o suites, y esas entidades las lee cualquier `builder`. Se mitiga con scopes mutuamente excluyentes (A1/A2) y la regla de datos sintéticos (CLAUDE.md, regla 5). **No hay escáner de secretos ni de PII en `validate`** (registry §16, fase 2).
- **Contenido hostil:** todo borrador de un sistema es no confiable. El esquema estricto y los límites ya existen (`validation.py`). El aprobador humano y el agente constructor pueden ser blanco de *prompt injection* desde el texto de un borrador (ADR 0008).
- **Costo:** `evaluate` es síncrono y llama al LLM real. Sin A5 y A6, un bucle de un sistema externo quema presupuesto. El tope en USD sigue sin monto (TEMAS #16).
- **PII en eventos:** cada campo público es una decisión (B3). Riesgo de deriva: alguien agrega un campo a un payload de la cadena y el proyector lo copia. Se mitiga con modelos `data` explícitos (nunca `model_dump` del payload) y la prueba de PII.
- **Deriva del contrato:**
  - los esquemas se generan desde M0, así que no pueden desalinearse del código, pero sí romper a los consumidores. Se mitiga con la regla de compatibilidad y la prueba de esquemas congelados;
  - el OpenAPI del registry hoy **no** está en `contracts/` (A10);
  - la spec del registry §12 ya está desalineada (`proposal_staled`).
- **Determinismo y replay:** B1 a B3 no tocan la cadena ni el motor. Una emisión E1 sí tocaría M4 (verificar fixtures).
- **Postgres sin verificar:** sin docker en esta sesión, las rutas de Postgres de A3, A5 y A8 se probarían solo con `docker compose up -d postgres && uv run pytest tests/integration`.

## 6. Lo que no se sabe

- Quién emite y rota las credenciales de servicio, cómo se revocan (¿`kid`?, ¿lista de revocación?) y si habrá un servicio de identidad para sistemas (TEMAS #13: sin unidad asignada).
- Si la unidad 4 ya consume `OutboxMessage` fuera de este repo (decide si B8 puede cambiarlo).
- Qué transporte elegirá la unidad 4. El contrato evita suponerlo, pero un transporte con orden por partición (p. ej. por `run_id`) es compatible con la garantía B6.
- Si las evaluaciones deben contar contra `CostCounters` de M9 o contra un presupuesto propio del registry.
- Qué operaciones de lectura necesitan de verdad los equipos (no hay requisitos escritos de ciencia de datos ni de ingeniería de datos). La lista de A2 y B1 es una suposición razonable, no un requisito levantado.
- Si `kid` y `trace_id` en la auditoría del registry requieren cambiar `Principal` (M0) o se pueden pasar por fuera.

## 7. Decisiones que necesita el usuario (resumen)

1. **A1:** ¿reabrir TEMAS #14 y admitir `service` en el registry, o seguir con `builder` no humano por sistema?
2. **A2:** operaciones permitidas a un `service` (¿evaluar?, ¿linaje?); confirmar que "retirar" sigue en la fase 2.
3. **A3:** vocabulario de scopes `registry:read`, `registry:write` y `registry:evaluate` (cierra en parte el Abierto de m09 §11).
4. **A4:** `attrs.team` obligatorio y propiedad por equipo de las propuestas; ¿lista de agentes por scope?
5. **A5:** lista de tipos de entidad que un `service` puede proponer.
6. **A6:** confirmar que cuatro ojos sigue en la fase 2.
7. **A7:** atributos de auditoría (`actor_team`, `credential_kid`, `trace_id`) y si el `kid` justifica tocar M0.
8. **A8:** emisor de servicios separado y atado al tipo (`--service-keys`); quién lo opera.
9. **A9:** topes por actor, origen forzado para no humanos y monto del tope de costo.
10. **A10:** endpoints nuevos y si el OpenAPI del registry va en el mismo `openapi.json` o en otro archivo.
11. **A11 / A0:** corregir ya el vencimiento no comprobado en la API del registry (hallazgo).
12. **B1:** lista cerrada de eventos públicos (v1).
13. **B2/B3:** campos del sobre y campos dudosos (`reportable_attrs`, `reason`, `resolution_code`).
14. **B4:** versión única de `contracts/` más `spec_version`, o versión propia.
15. **B5:** tipos en M0 (bump menor a 1.4.0 con aviso) o paquete fuera de M0.
16. **B7:** solo contrato y proyector ahora (E3), o también emisión (E1 o E2).
17. **B8/B9:** mantener `OutboxMessage` y usar el mismo sobre para el registry.
