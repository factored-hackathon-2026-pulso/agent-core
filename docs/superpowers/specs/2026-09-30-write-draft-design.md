# `write_draft` y el agente constructor — diseño

- Fecha: 2026-09-30
- Estado: **aprobada; fases 1 a 5 implementadas (2026-09-30)**; fases 6 a 8 pendientes
- Gobierna: tema #17 (y #16 en lo que aplica los topes) de `docs/specs/TEMAS-ABIERTOS-PENDIENTES.md`
- ADRs: 0019 (agentes internos), 0007 (protocolo de escritura), 0006 (principales), 0014 (`await_approval`), 0004 (nodos)
- Specs que toca: m00, m01 §3.13, m02 §3.3 y §3.7, m03, m11, registry §2, §7.2, §8, §17 y §18, spec del gateway (G0-25)

## 1. Intención y criterio de éxito

**Intención.** Que el agente constructor pueda **escribir borradores** en el registry sin `confirm`, porque el gate humano es la aprobación de la propuesta (ADR 0019 §5), y dejar construidas las piezas que lo rodean. Hoy el constructor solo puede correr de solo lectura.

**Criterio de éxito.** Un flow con `invocable_by ⊆ {builder}` crea una propuesta y escribe un borrador con `act → verify`, sin `confirm`, de forma idempotente, con el evento de auditoría de cada escritura y con replay reproducible. Cualquier otra clase de escritura sigue exigiendo `confirm → act → verify`. Un agente que use `write_draft` y no cumpla AG-02 se rechaza al validar.

## 2. Decisiones (todas tomadas con el usuario el 2026-09-30)

| # | Decisión |
|---|---|
| D1 | **Condición de admisibilidad de `write_draft`.** "Leer" un borrador significa servirlo como definición ejecutable a un run por `RegistryPort` (`resolve_release`, `get`). Las tools del constructor sí pueden leer borradores **como datos** (readback del `verify`, `get_proposal`). La condición es la regla 8 de registry §2 ("el motor solo lee lo publicado"); la cita "regla 7" de registry §18 #1 era un error y se corrige. |
| D2 | **Garantía de D1 por invariante con pruebas** (§4.4). Los permisos de base de datos por rol quedan para la fase 2 de producción. |
| D3 | **Sin puerto nuevo en M0.** El adaptador del constructor envuelve `RegistryService` directamente. |
| D4 | **Idempotencia con tabla de claves** `reg_draft_writes` (ADR 0007 §5 al pie de la letra). |
| D5 | **Son `write_draft`:** `create_proposal`, `put_draft`, `freeze`, `reopen` y `evaluate`. `validate` es `compute`. |
| D6 | **`Action` se reutiliza** con los campos de confirmación opcionales (no hay un tipo nuevo). |
| D7 | **El replay de M11 graba las respuestas del `AgentPort`** en el fixture (`agent_steps`). El modo `audit` sigue sin construirse. |
| D8 | **El orden de construcción es por capas**, con un hito temprano (§14). |
| D9 | **Excepción acotada a G0-22:** el `tool.args` de un `write_draft` puede leer la salida de un nodo `agent` si su `output_schema` es el esquema del borrador. Enmienda el ADR 0019 §1. |
| D10 | **Dos flows de entrada duplicados** (uno por agente); comparten tools, prompts y plantillas. Enmienda el ADR 0019 §2. `subflow` queda fuera. |
| D11 | **AG-02 exige `subject_kinds == []`** (los `subject_kinds` son cadenas libres; una lista de "datos de clientes" no es comprobable). |
| D12 | **Topes del constructor autónomo en el servicio**, para propuestas con `origin = auto_detect`: 10 creadas por día (ventana móvil de 24 h con el `Clock`) y 20 evaluaciones por propuesta. El tope de costo por propuesta **queda diferido**. |
| D13 | **Trazabilidad:** el principal del run viaja como actor de auditoría en `reg_draft_writes`, nunca como fuente de permisos. |
| D14 | **`await_approval` entra como la última fase**, solo en flows `conversational` (G0-16 no se relaja; se corrige m01 línea 201). |
| D15 | **Quién firma la aprobación:** la verificación JWS de M9 con las barreras del registry (tipo y roles del nodo, `actor = human`, `step_up`) y un aprobador **distinto** del principal del run. |

## 3. Alcance

**Dentro:** las ocho fases de §14.

**Fuera (decidido):** `subflow` (la pila de flows y los invariantes a través de su frontera no están especificados; trabajo del tamaño de #17), dejar el constructor utilizable en `serve` (fase 7b: cableado, release de demo y prueba de humo con el LLM real; la decide el usuario tras probar), el tope de costo, el modo `audit` del replay, los permisos de base de datos por rol (A2), el catálogo de campos y plantillas como entidades (registry §18 #9), el copiloto del asesor y la publicación de conocimiento (registry §18 #8).

**El constructor `task` no se cablea en `serve`** mientras no exista el monto del tope de costo (TEMAS #16).

## 4. Fase 1 — Registry

### 4.1 Tabla de escrituras idempotentes

`reg_draft_writes(idempotency_key PK, op, proposal_id, rev_after, request_hash, run_id NULL, on_behalf_of NULL, created_at)`, con su equivalente en el store en memoria. La clave se guarda en la **misma transacción** que el efecto.

### 4.2 Operaciones con clave

`create_proposal`, `put_draft`, `freeze`, `reopen` y `evaluate` reciben `idempotency_key` opcional y un parámetro `audit` opcional (`run_id`, `on_behalf_of` = `tipo:id` del principal del run). Sin clave se comportan como hoy.

- **Misma clave y mismo `request_hash`** (hash JCS de los argumentos): devuelve el resultado guardado y no repite el efecto. Un reintento de `put_draft` no sube `rev` ni da `proposal_stale`; uno de `evaluate` no vuelve a gastar LLM.
- **Misma clave con otro contenido:** `idempotency_conflict` (409), nuevo en el enum del paquete `registry` (no entra en `ProblemCode` de M0).

### 4.3 Readback

`get_write(idempotency_key) -> WriteRecord | None` (`op`, `proposal_id`, `rev_after`, `request_hash`). Lectura abierta a cualquier `builder` autenticado, como las demás. La clave de idempotencia ya ata el contenido (una clave con otro contenido da `idempotency_conflict`), así que el `verify` del constructor solo comprueba que el registro exista con la operación esperada (`readback.op == "<op>"`; para `evaluate`, `readback.verdict == "pass"`). `get_write` devuelve además `verdict`, `run_id` y `on_behalf_of`.

### 4.4 Regla 8: invariante con pruebas (D1, D2)

1. **Contrato** sobre `InMemoryRegistry` y `PostgresRegistry`: tras un `put_draft`, una entidad o versión que solo existe en el borrador nunca aparece en `resolve_release` ni en `get`.
2. **Composición:** ningún módulo fuera de `agent_core/registry/` menciona `SnapshotRegistry`, y dentro del paquete solo lo hacen `service.py`, `snapshot.py` y `__init__.py`. (La candidata sirve a un run durante la evaluación, pero no es una release publicada y corre en el sandbox: no viola la regla.)
3. **Estática:** `registry/postgres/runtime.py` solo llama a un conjunto cerrado de métodos del `tx` (`get_release`, `release_status`, `get_alias`, `latest_release_for_agent_version`, `get_version` y `blobs`); en particular nunca a `get_proposal`, `get_changes` ni a `get_draft_write`.

### 4.5 Topes (D12)

Se aplican en `RegistryService`, para propuestas con `origin = auto_detect`:
- `create_proposal`: si ya hay 10 creadas en las últimas 24 h (`Clock`), `quota_exceeded` (429, nuevo en el enum del paquete).
- `evaluate`: si la propuesta ya tiene 20 evaluaciones (incluidas `failed_infra`), `quota_exceeded`.

## 5. Fase 2 — M0 (cambio de interfaz para todos los módulos)

- `RiskClass.write_draft`. `ToolDef.is_write` la cuenta como escritura, así que le exige `readback_by`.
- `Action`: `confirm_node_id`, `confirmation_token_hash` y `token_exp` opcionales, con un validador "los tres o ninguno" (ninguno solo en una acción `write_draft`).
- **Nodo:** `WriteToolConfig` gana la forma `draft: true` con `tool` y `args` propios (`action_from` pasa a opcional; se declara uno u otro). El discriminador `node_kind` trata `tool` con `draft: true` como `tool_write`: así reutiliza las ramas `ok/uncertain/denied`, G0-06 y la recuperación. Un nodo `tool` normal (`ToolConfig`) con una tool de escritura sigue siendo violación de G0-05.1.
- **`Action`** gana `write_node_id` (el nodo `draft` que la creó). `confirm_node_id`, `confirmation_token_hash` y `token_exp` pasan a opcionales con un validador: una acción con `write_node_id` no lleva ninguno de los tres; una sin él, los tres.
- **No hay evento nuevo:** `action_dispatched`, `tool_called` y `action_verified` son la auditoría de cada escritura.
- `SCHEMA_VERSION` 1.1.0 → 1.2.0 (menor; la 1.1.0 la usó `input_view` del nodo `agent` en `main`), `uv run agentcore contracts` y `contracts --check`. **Aviso de cambio de interfaz al terminar la fase.**

## 6. Fase 3 — M1

- **G0-05.1:** un nodo `tool` (`ToolConfig`) admite `read` y `compute`; una escritura va en un nodo con `action_from` (con `confirm`) o con `draft: true` (solo `write_draft`, G0-23).
- **G0-23:** un nodo con `draft: true` usa una tool `write_draft` con `readback_by: idempotency_key`; `next.ok == next.uncertain == V`, con V un `verify` con `by: idempotency_key` y un `readback` de clase `read`; ningún otro nodo de escritura tiene a V como destino de `ok` o `uncertain`. El flow puede volver a W (iterar el borrador) **solo desde la rama `verified` de V**.
- **Reclamos:** `respond.claims` y `derive_claims` referencian el id del nodo de escritura cuando no hay `confirm`; el invariante de G0-05.8 no cambia.
- **AG-02:** un agente cuyos flows usan un `write_draft` tiene `invocable_by ⊆ {builder}` y `subject_kinds == []` (D11).
- **G0-25:** el prompt del `prompt_ref` de un nodo `agent` tiene un perfil `structured: prompted`.
- **G0-22 con la excepción de D9:** ninguna ruta `facts.<save_as>` de un nodo `agent` se lee en `rule.expr`, `verify.predicate`, `confirm.action.args`, `escalate.priority_expr` ni `end.output_map`, ni en el `tool.args` de una tool que no sea `write_draft`. En el `tool.args` de un `write_draft` se puede leer solo `facts.<agente>.value.changes`, y solo si el `output_schema` de todos los agentes con ese `save_as` coincide con el esquema del borrador; el `save_as` de la escritura draft queda marcado como salida de agente (ajuste tras la revisión final, 2026-09-30).
- **G0-16 no cambia.**

## 7. Fase 4 — M3 y M2

- **M3:** `ActionManager.freeze_draft_write(state, write_node, resolved_args, tool_def, ctx)` crea la acción directamente en `confirmed` (`action_id`, `args_hash`, `idempotency_key = action_id`, sin token). Lanza `IllegalTransition` si la tool no es `write_draft`. Una acción activa por nodo. Desde ahí siguen `execute_write` y `verify` sin cambios, con los dos commits y la recuperación `executing → verify`.
- **M2:** el manejador del nodo `tool` mira el `risk_class`: con `write_draft` llama a `freeze_draft_write` y luego a `execute_write`, y manda `ok` y `uncertain` al `verify` enlazado. `denied` y `step_up_required` siguen las ramas del nodo. Sin `ctx.actions`, `IllegalTransition` (error de cableado).

## 8. Fase 5 — `BuilderToolExecutor`

En `agent_core/composition/`, implementa `ToolExecutor` sobre `RegistryService`.

- **Credencial:** de servicio, con rol `constructor` y sin `attrs.actor = "human"`, verificada con el verificador del staff. El permiso lo decide el servicio con esa credencial; el principal del run solo viaja en `audit` (D13).
- **`origin`:** `registry/create_proposal` solo admite `builder_chat` y `auto_detect` (con `manual` o `import` el constructor esquivaría los topes de `auto_detect`).
- **Tools y su clase:**
  - `write_draft`: `registry/create_proposal`, `registry/put_draft`, `registry/freeze`, `registry/reopen`, `registry/evaluate`.
  - `compute`: `registry/validate`.
  - `read`: `registry/get_proposal`, `registry/get_entity`, `registry/list_versions`, `registry/get_write`.
- Pasa el `idempotency_key` de M3 a cada operación de escritura y cada `ToolDef` lleva `description` y `args_schema` (G0-24).
- **No existe ninguna tool de aprobar, publicar, promover ni revocar.**
- **Hito 1 (fin de la fase 5):** una tool `write_draft` de prueba escribe un borrador de punta a punta.

## 9. Fase 6 — M11

- El fixture gana `agent_steps`: los `AgentStepResult` que devolvió el puerto, en orden. `agentcore record` los llena; mantiene su rechazo de datos no sintéticos.
- `RecordedAgentPort` (en `testing/replay/recorded.py`) los sirve en orden; si el motor pide uno más, `ReplayDesync`.
- El replay `fixture` compara la secuencia completa (incluidos `agent_step`, `tool_called`, `action_dispatched`, `action_verified`). No hay `IdKind` nuevo.
- Escenarios nuevos en `testing/replay/scenarios.py`: `builder_write_draft` y `builder_write_draft_uncertain`. Los fixtures se regraban y `test_el_fixture_committeado_esta_vigente` los vigila.
- M11 recibe la decisión 24. El replay `audit` sigue sin existir.

## 10. Fase 7 — Los dos agentes del constructor

- **Agentes:** `constructor-chat` (`conversational`) y `constructor-task` (`task`), con `invocable_by: [builder]` y `subject_kinds: []`. Supuestos a confirmar: `supported_locales: [es]` y el `min_auth_level` más bajo existente.
- **Flows** (uno por agente, sin bucle de iteración; iterar es otro run):
  1. `chat`: `collect` del objetivo; `task`: entrada estructurada (título y objetivo).
  2. Nodo `agent` de solo lectura (`get_entity`, `list_versions`, `get_proposal`, `validate`) con `output_schema` igual al esquema del borrador.
  3. `create_proposal` con `origin` **literal** en el flow (`builder_chat` o `auto_detect`) y su `verify`.
  4. `put_draft` con los cambios del agente (excepción de D9) y su `verify`.
  5. `validate`, `freeze` y `evaluate`, cada escritura con su `verify`.
  6. `respond` con el resultado y la leyenda "pendiente de aprobación humana en el registry". Las ramas de fallo (`validation_failed`, `gate_failed`, `quota_exceeded`, `uncertain` sin efecto) terminan en un `respond` seguro; en `task`, con `end(failed)`.
- **Prompts:** uno compartido para el nodo `agent` con `structured: prompted` y las plantillas de `respond`, en `es`. Lo que el constructor lee (trazas, páginas) entra como dato, no como instrucción. El texto se redacta con escenarios sintéticos y se revisa en el PR.
- **Dónde viven:** como semilla de pruebas junto a los demás flows de prueba; **no se publican en ninguna release** ni llaman al LLM real.
- **Pruebas:** validación con el validador real; un recorrido de punta a punta por agente con `ScriptedAgent` y un registry en memoria; un agente con `invocable_by` que incluya `customer` falla AG-02; el `agent` no puede alimentar `put_draft` si su `output_schema` no es el del borrador.

## 11. Fase 8 — `await_approval` (ADR 0014)

Nodo para que un run `conversational` espere la decisión de un tercero.

- **Nodo:** `approver: {principal_type, roles[]}`, `summary_template`, `timeout`; resultados `approved`, `rejected`, `timeout`. G0-01 deja de rechazarlo.
- **Solo en flows `conversational` (D14).** En un flow `task` es violación de G0-16. Se corrige m01 línea 201.
- **Evento externo:** `POST /v1/runs/{run_id}/events {type: approval, decision, approver}`. M9 aplica las barreras de D15: el `principal.type` está en `approver.principal_type`; los `roles` incluyen `approver.roles`; `attrs.actor = "human"`; `auth.level = step_up`; y el aprobador es **distinto** del principal del run. Incumplir una condición da `403` y `access_denied`.
- **Auditoría:** la aprobación aceptada queda en la cadena del run con el id del aprobador (nunca su credencial).
- **No reemplaza al `confirm`:** una escritura aprobada sigue `confirm → act → verify` (ADR 0014).
- Tocan M0 (evento de aprobación), M1 (G0-01 y alcance), M2 (manejador), M4 (reanudar), M9 (endpoint) y M11 (replay).
- **Detalles que se cierran con el usuario antes de implementar esta fase** (son "Abiertos", no los decido yo): esquema del evento de aprobación, cómo se representa la espera en `RunState`, semántica del `timeout` con el `Clock`, y la interacción con `inactivity_ttl`.

## 12. Pruebas

Cada fase corre `uv run pytest`, `uv run lint-imports`, `uv run mypy` y `uv run ruff check .`. Las pruebas que tocan Postgres usan `docker compose up -d postgres`. La fase 2 añade `agentcore contracts --check`. Se escribe primero la prueba y se confirma que falla. Todos los datos son sintéticos. No hay `datetime.now()`, `uuid4()`, `random` ni `float` para dinero o cifras; el JSON entra con `agent_core.domain.loads`.

## 13. Documentos que se actualizan en el mismo cambio de cada fase

m00, m01 (§3.13), m02 (§3.3 y §3.7), m03, m11, registry (§2 regla 8, §7.2, §8, §17.4 y §18, incluida la nota de que §18 #3 ya se resolvió con el código propio del paquete), ADR 0007 (la enmienda pasa de "diseño" a implementada), ADR 0019 (estado; excepción de G0-22 en §1; flows duplicados en §2; nota de que `LLMAgentPort` ya existe), spec del gateway (G0-25 deja de ser abierto), TEMAS #16 y #17 y el índice si hace falta.

## 14. Fases, entrega y plazo

| Fase | Contenido |
|---|---|
| 1 | Registry (§4) |
| 2 | M0 (§5) |
| 3 | M1 (§6) |
| 4 | M3 y M2 (§7) |
| 5 | `BuilderToolExecutor` (§8). **Hito 1** |
| 6 | M11 (§9) |
| 7 | Agentes (§10) |
| 8 | `await_approval` (§11) |

- Un commit por unidad lógica con el formato del repo y la línea `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`. Rama local nueva `feat/write-draft`. Sin push ni PR sin autorización.
- **Plazo (la demo se congela el 02/10; hoy es 30/09):** lo realista son las fases 1 a 5 y, con suerte, la 6. Las fases 7 y 8 probablemente no caben. Se informa con resultados reales al cierre de cada fase.

## 15. Abiertos y riesgos

- **Resueltos al preparar el plan (2026-09-30):** `D-A` el esquema de nodos cambia (§5); `D-B` `Action.write_node_id` (§5); `D-C` `get_write` y `verify` por existencia (§4.3); `D-D` los ids de tools llevan `/` (§8, un `EntityId` no admite `.`); `D-E` M4 (`turn/recovery.py`) también cambia: `position_at_verify` encuentra el nodo de escritura de una acción `draft` por `write_node_id`.

- **Detalles a cerrar en el plan de cada fase:** cómo expresa `tool.args` la lectura de `facts.<agente>` (D9); el reintento tras `step_up` en un `write_draft` (el bot no debería recibirlo); los nombres exactos de las tools; cómo se reproducen los resultados de tools del bucle en el replay; el campo y el lugar de los fixtures y de la semilla de los agentes.
- **Fase 8:** los cuatro detalles de §11.
- **Pendiente futuro (fuera de alcance):** `subflow`, fase 7b, tope de costo, modo `audit`, A2.
- **Riesgo aceptado (ADR 0019):** `write_draft` debilita `confirm → act → verify` para esa clase; se compensa con que los borradores no se sirven (D1, D2), con AG-02 y con la aprobación humana de la propuesta.
- **Riesgo de D9:** un modelo escribe contenido libre en un borrador sin `confirm`. Lo acotan el esquema estricto y los límites del registry (50 cambios, 262 144 bytes por entidad, 200 nodos por flow), el gate (`validate`, `evaluate`) y la aprobación humana.
- **Riesgo de ids en el replay:** si un módulo crea ids en un orden distinto al de los eventos aparece una divergencia falsa (riesgo 9 de M11); lo vigilan los escenarios `uncertain`.

## 16. Definición de terminado

Por fase: pruebas escritas primero y vistas fallar, luego en verde; `pytest`, `lint-imports`, `mypy` y `ruff` en verde; `contracts --check` en verde tras la fase 2; documentos de §13 actualizados; commit por unidad lógica. Global: el criterio de éxito de §1 demostrado por una prueba de punta a punta (hito 1) y, si se llega, por el replay y los agentes.
