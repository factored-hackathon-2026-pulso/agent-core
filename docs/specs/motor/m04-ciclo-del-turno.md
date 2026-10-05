# M4 — Ciclo del turno

- Estado: rev. 6 (2026-10-01: la base impone un run abierto por sesión con `runs_one_open_per_session`, §3.8 punto 5; Postgres sin verificar) · rev. 5 (2026-10-01: el run destino de una transferencia abre y responde en el mismo turno, §3.8 y paso 12b) · rev. 4 (2026-10-01: transferencia entre agentes, validación y rama `rejected`, §3.8) · rev. 3 (2026-09-29) · Fase 2 · Fase A implementada con dobles · Fase B: grabador y cadena de M11 verificados y Understand cableado a M5 · Fase C (Postgres) implementada
- Paquete: `agent_core.turn`
- Origen: spec general §4.1 (estado, release, revocación, recuperación, abandono), §4.4 (uso del resultado de Understand), §4.5, §4.6, §4.8, §4.9, §4.10, invalidación de §4
- ADRs: 0004 (intenciones e interrupciones), 0007 (precedencia con `confirm` pendiente), 0013 (cierre por escalamiento), 0021 (transferencia entre agentes)
- Usa: M0, M2, M3, M5, M6, M10, M11 · Lo usa: M9

## 1. Propósito y límites

Orquesta un turno (modo conversacional) o una corrida (modo task) **después** de que M9 validó credenciales, límites y autorización: carga el estado, corre guardas y Understand, aplica manejadores globales, elige el flow, llama al intérprete, cierra el run si corresponde y persiste todo en una transacción.

**No hace:** credenciales ni HTTP (M9), ejecutar nodos (M2), la detección de idioma (M6), el modelo de Understand (M5), el paquete de handoff (M10).

## 2. Interfaz pública

```python
class TurnEngine:
    def start_run(self, principal, on_behalf_of, run_input: RunInput) -> RunResult       # task o inicio conversacional (C5)
    def handle_turn(self, principal, on_behalf_of, turn: TurnInput) -> TurnResult
    def sweep(self, now: datetime) -> SweepReport                                        # barrido periódico
```

Dependencias por constructor (solo por nombre): `uow_factory`, `registry`, `clock`, `ids`, `guards` (`GuardsPort`, M6), `understand` (`UnderstandPort`, M5), `actions` (`ActionManager`, M3), `handoff` (`HandoffService`, M10), `recorder` (`TurnRecorderPort`, M11), `chain` (`EventChain`, M11), `audit` (`AuditSink`), `runtimes` (`RuntimeFactory`, puente con M7), `trace` (`TraceIds`), `config` (`TurnConfig`) y, opcionales, `authz` (`reportable_attrs()` para `run_started` y, al transferir, `authorize_agent` y `authorize_subject` sobre el destino, §3.8) y `telemetry` (`TurnTelemetry`, §3.9; sin ella, `NoTurnTelemetry`). M2 son funciones (`advance`, `start_flow`, `begin_turn`), no una clase `Interpreter`.

## 3. Comportamiento

### 3.1 Pipeline de `handle_turn`

1. **Deduplicación:** si `client_turn_id` ya tiene resultado guardado, se devuelve ese `TurnResult` sin reprocesar. Se busca bajo el run que devuelve `find_run_by_session` y, si no está, bajo los demás runs de la sesión (`list_runs_by_session`): tras una transferencia, el reintento de un turno que respondió el run origen devuelve su resultado y no se procesa en el destino.
2. **Cargar:** `uow.find_run_by_session(session_id)` (la sesión puede tener varios runs; se carga el abierto o, si no hay, el más nuevo — ADR 0021; `list_runs_by_session` los lista en orden de creación); `uow.acquire_turn(run_id, turn_id, now, ttl)` toma el lease del turno **antes** de trabajar (M0 §2.9): si otro turno lo tiene → `TurnInProgress` → `409 turn_in_progress`. Run cerrado → `410 run_closed`. Cada commit usa `save_run(expected_version)` como defensa; el lease se libera en el commit final. Si el turno falla con una excepción, el motor libera el lease con una UoW propia para que el reintento no espere al TTL; si esa liberación también falla deja un `WARNING` (`agent_core.turn`: `run_id`, `turn_id` y solo el **tipo** de la excepción) y el lease vence por TTL.
3. **Release:** si está `revoked` → `escalate(release_revoked)` sin ejecutar nodos.
4. **Abandono:** si `now − last_activity_at > inactivity_ttl` → M4 cierra el run con `abandoned` (ver 3.6), invalida las acciones y responde `410 run_closed` **sin procesar el mensaje**. La app abre un run nuevo (M9); el motor no reabre el run por su cuenta (decisión P2, 2026-09-29).
5. **Recuperación:** `actions.pending_recovery(state)` no vacío → posicionar el flow en el `verify` correspondiente y avanzar desde ahí antes de procesar el mensaje. El nodo de escritura de una acción se encuentra por `write_node_id` si es una escritura `draft` y por `action_from` si lleva `confirm`; una acción draft nunca se asocia a una escritura con confirm (spec write-draft §7).
6. **Tokens vencidos:** `actions.expire_tokens(state, turn_id=…)` (devuelve el estado y los eventos `action_cancelled`). Si cancela la propuesta de un `confirm` pendiente, el turno avanza con `resume = none` y el `confirm` re-propone con token nuevo (§13).
7. **Guardas (M6):** idioma, tamaño, injection. `unsupported` → plantilla en `default_locale`, sin Understand ni flow. Actualiza `state.locale`. `injection_flagged` → `degraded = true` para este turno.
8. **Understand:**
   - Si el request trae `confirm: {token, answer}` → no se llama a Understand; `resume = confirm_answer(answer)`.
   - Si no, `understand.run(model_view_text, state, locale)` → `UnderstandResult`, y se emite `command_emitted`.
9. **Manejadores globales** (3.2) → pueden terminar el turno.
10. **Flow e intenciones** (3.3).
11. **Avanzar:** `interpreter.advance(state, ctx, resume)`.
12. **Cierre:** si `StepOutcome.transfer` → validar la transferencia (3.8); si `StepOutcome.escalation` → `handoff.escalate(...)`; si `end_outcome` → cerrar el run (3.6). Al terminar un flow con pendientes, ofrecer la primera (3.3).
12b. **Transferencia** (3.8, ADR 0021): si la transferencia fue válida, el run origen termina su turno (pasos 13 a 14 sin `commit`) y el **mismo turno** sigue en el run destino: se crea, se emiten `run_started{origin}` y `transfer_received` y se procesa el texto del cliente desde el paso 3. Los dos runs, sus cadenas de eventos, el uso (`add_usage`) y el resultado por `client_turn_id` van en la misma UoW: un único `commit` los guarda todos o ninguno. El lease es solo el del run origen; el destino es un run nuevo, sin commitear, que nadie más ve. Quedan **fuera** de esa UoW el transcript (M11 `TurnRecorder`) y las escrituras de M3 (sus propios commits), como en cualquier turno (3.8 punto 7). El `TurnResult` del turno es el del destino (`run_id` y `agent` del especialista) con los mensajes del origen primero; se guarda bajo `(run destino, client_turn_id)`.
13. **Responder y registrar:** `recorder.record_turn(...)` (transcript + `response_emitted`, M11).
13b. **Medir:** emitir `turn_completed` (3.7) como último evento del turno.
14. **Persistir:** una transacción con `state` (`state_version + 1`), eventos del turno (los que devuelve `execute_write` ya los persistió el `EventRecorder` dentro de los commits de M3 y no se vuelven a agregar; el recorder de M4 vuelca primero los eventos pendientes del turno), outbox y resultado por `client_turn_id`.

`start_run` hace: crear `RunState` (release fijada, principal sin secretos, `locale` inicial = `lang` del request si es soportado, si no `default_locale`), emitir `run_started` con `reportable_attrs`, arrancar `entry_flow` y, en modo task, avanzar hasta un terminal (M1 G0-16 garantiza que un flow task no tiene nodos que esperan; si aun así M2 devuelve una espera, es un bug y el run escala con `validation_failed`).

`start_run` entrega en `RunResult.suggestions` (M0 rev. 16, ADR 0026) las que acumuló el flow (`StepOutcome.suggestions` → `TurnFrame.suggestions`), **solo si el run terminó `completed`**: de un run que falló o escaló no sale una lista. La lista se guarda con el resultado idempotente (la repetición devuelve las mismas). `GET /v1/runs/{id}` no las publica (tampoco `output`): la plataforma las lee de la respuesta de `POST /v1/runs`.

`start_run` es **idempotente por `(principal.key, idempotency_key)`** (cambio pedido por M9, 2026-09-29): antes de crear nada busca el registro; con el mismo body (hash JCS del `RunInput` sin la clave) devuelve el `RunResult` guardado y con otro body lanza `409 idempotency_conflict`, y con la clave reservada por otra petición aún en curso lanza `409 idempotency_in_progress`. El registro se escribe en la misma transacción que el run (`put_run_idempotency` antes del `commit`), así que no hay un run sin su clave ni una clave sin su run. Concurrencia: antes de ejecutar nada, `start_run` **reserva** la clave con `UnitOfWork.reserve_run_idempotency` (inmediata y fuera de la transacción, como el lease; TTL = `lease_ttl`). Una segunda petición con la clave reservada y vigente recibe `409 idempotency_conflict` sin ejecutar efectos; si el run no llega a commitear, la reserva se suelta (`release_run_idempotency`) y, si el proceso muere, vence sola. El `commit` completa la reserva con el resultado. El resultado guardado incluye un `confirmation.token` en claro si el primer turno pide confirmación.

### 3.2 Manejadores globales

Entrada: `UnderstandResult` con `command`, `p_cal` y la marca `below_threshold` por campo (la calcula M5).

**Sin `confirm` pendiente** (en orden, gana el primero que aplique):

| # | Condición | Acción |
|---|---|---|
| 1 | Interrupción: `command = interrupt` sobre umbral **o** su `signal_policy` da `true` (por `priority`) | invalidar acciones; ejecutar `action` de la interrupción (`escalate` o `start_flow`) |
| 2 | `cancel` | cerrar el flow activo, invalidar acciones (`cancel`) |
| 3 | `handoff` | `escalate(customer_request)` |
| 4 | `out_of_scope` | plantilla de abstención + `end(abstained)` |
| 5 | `clarify`, o `command`/`flow` bajo umbral | plantilla de aclaración; `clarifications_used += 1`; al superar `max_clarifications` → `end(clarify_exhausted)` o `escalate(low_confidence)` según el agente |
| 6 | ninguno | seguir a 3.3 |

**Con `confirm` pendiente:**

| Entrada | Resultado |
|---|---|
| Botón `yes`/`no` | `resume = confirm_answer(yes|no)`; nunca `unclear` |
| `affirm` / `deny` sobre umbral | `resume = confirm_answer(yes|no)` |
| Interrupción, `cancel`, `handoff` | igual que sin `confirm` pendiente |
| `start_flow` o `additional_flows` | a `pending_intents` con acuse + `resume = confirm_answer(unclear)` |
| Cualquier otro comando, `out_of_scope`, `clarify`, o bajo umbral | `resume = confirm_answer(unclear)` |

**Tope global de reparación:** después de cada turno, `repair_turns_used` = aclaraciones + reintentos de `collect` + `unclear` de `confirm`. Si supera `max_repair_turns_per_run` (8 por defecto) → `escalate(low_confidence)`.

**Modo degradado:** marca el turno en `degraded_turns` y pasa `degraded = true` al intérprete.

### 3.3 Flow activo e intenciones pendientes

- Sin flow activo y `command = start_flow` → `interpreter.start_flow(flow)`; `additional_flows` van a `pending_intents`.
- Con flow activo: `start_flow` y `additional_flows` van a `pending_intents`, ordenadas por `flow.priority` desc y, en empate, por `mention_order`; se agrega un acuse al mensaje.
- Una intención pendiente no invalida una acción esperando confirmación.
- Al terminar el flow activo con pendientes: se ofrece la primera (plantilla) y el run queda esperando; solo arranca con `affirm`. Con `deny` se descarta esa intención y se ofrece la siguiente; si era la última, no se ofrece nada y la conversación sigue en modo normal (plantilla del agente). Si la respuesta no es `affirm` ni `deny`, la oferta sigue vigente una vez más y luego se descarta; cada uno de esos turnos suma a `repair_turns_used`. El `deny` nunca escala: la escalada es solo el tope global `max_repair_turns_per_run` (decisión P1, 2026-09-29).
- `continue`: se reanuda el nodo actual con `resume = slot_answer(texto)` si el nodo es `collect`.

### 3.4 Invalidación de acciones

M4 llama `actions.invalidate(state, reason, turn_id=…)` ante: `cancel`, abandono, interrupción y escalamiento. El vencimiento de tokens lo resuelve `expire_tokens`.

### 3.5 Escalamiento

`handoff.escalate(state, request, events_so_far, uow=uow, turn_id=turn_id)` devuelve `(estado, [escalated], OutboxMessage, Message)` y ya persistió el paquete con `uow.put_handoff`. M4 debe haber invalidado antes las acciones `proposed`/`confirmed`. El estado con `status = escalated`, `outcome = escalated`, el evento `escalated` y el mensaje de outbox entran en la **misma** transacción del turno. M4 emite `run_closed{closed_by: escalation}` (M4 es el único emisor de `run_closed`). Turnos posteriores → `410 run_closed`.

Después de `record_turn`, M4 rellena `response_emitted.payload.transcript_fp` con `model_copy` antes de pasar los eventos a M11 (M0 §2.10). `RunState.awaiting` se deriva del `Stop` de M2 (`awaiting_user` → `input`); la oferta de intención pendiente también es `input` con `pending_offer`.

### 3.6 Cierre y barrido

- Cerrar un run: `status = closed`, `outcome`, `run_closed`. El outcome `abandoned` solo lo asigna M4; `escalated` solo M10.
- `sweep(now)`: selecciona con `uow.list_inactive(now, limit)` (por lotes) runs `open` con `inactive_after < now` (M4 mantiene `inactive_after = last_activity_at + inactivity_ttl` en cada turno) (30 min por defecto en conversacional), los cierra con `abandoned`, invalida sus acciones y emite `expiry_evaluated{instante usado, ttl}` por cada evaluación.

### 3.7 Medición del turno (`turn_completed`)

- Todo `start_run` y todo `handle_turn` que llega al paso 14 emite **un** `turn_completed`, incluidos los que terminan antes: guardas (`unsupported`), release revocada o abandono. No lo emiten los turnos deduplicados (paso 1), los `409` ni los `410`; `sweep` tampoco.
- `duration_ms`: desde que M4 recibe el turno (antes del paso 2) hasta antes de persistir. No incluye el commit ni la puerta de M9: esos tiempos solo están en los spans.
- `stages`, cada uno medido con `Clock.monotonic_ns()`; una etapa que no corrió queda en `None`:
  - `guards_ms`: paso 7 (M6);
  - `understand_ms`: paso 8 (M5); `None` si la respuesta llegó por botón;
  - `flow_ms`: pasos 5, 9–12 (recuperación, manejadores, intérprete, acciones y escalamiento). Incluye las tools, cuya latencia individual está en `tool_called`;
  - `response_ms`: paso 13 (generación, validación y transcript).
- `awaiting`: `RunState.awaiting` al terminar el turno; `none` si el run cerró. `degraded`: si el turno corrió en modo degradado.
- Los campos de medición no participan en ninguna decisión ni en la comparación del replay (M0 §2.10).

### 3.8 Transferencia entre agentes (ADR 0021; spec `2026-09-30-transferencia-entre-agentes-design.md` §5.2)

El nodo `transfer` **no** es terminal en M0 (`TERMINAL` no lo incluye, decisión R2): M2 se detiene con `Stop.terminal`, deja el puntero en el nodo `transfer` y devuelve un `TransferRequest` (`StepOutcome.transfer`). `Closer.apply_outcome` lo guarda en `TurnFrame.pending_transfer` y `TurnEngine._resolve_transfer` (`agent_core/turn/transfer.py`, `Transferer`) lo resuelve dentro del mismo turno, de la misma etapa `flow` y de la misma UoW:

1. **Validación**, en el orden de la spec §5.2; el primer fallo da el `reason_code` de `transfer_rejected`:
   - `no_turn` (decisión P6 del plan, antes de las demás): la transferencia se alcanzó en `start_run`; no hay texto del cliente para continuar;
   - `not_in_directory`: no hay decisión (o es `none`), no hay hecho `directory_from` legible, o el destino no está en `choices` del directorio que leyó este run, aunque exista y esté publicado;
   - `no_active_release`: `agent_id@prod` no resuelve (el `KeyError` del registry se convierte en rechazo, no en un 500), la release está `revoked`, no fija al agente o fija una versión que el registry no sirve (`KeyError`/`TypeError` de `get`);
   - `not_eligible`: `transfer_ineligibility` de M0 (modo, contrato, `invocable_by`, `subject_kinds`, locale, `min_auth_level`) y, si hay `authz`, `authorize_agent(principal, destino, subject)` y `authorize_subject(principal, on_behalf_of, subject)`;
   - `accepts_mismatch`: `packet_problem` contra el `accepts` del agente de la release resuelta **ahora**;
   - `transfer_limit`: `(origin.depth o 0) + 1 > TurnConfig.max_transfers_per_session` (1 por defecto, decisión P5).
2. **Rechazo:** `transfer_rejected {transfer_id, to_agent, reason_code, directory, directory_hash}` en el run origen; el puntero sigue por `next["rejected"]` y `advance` continúa en el mismo turno (en la recepción típica, `escalate`). `to_agent` solo repite el destino si es uno de los `choices` del directorio que leyó el run (si no, `None`): nunca texto libre de una decisión. La recursión está acotada: `rejected` no vuelve al mismo `transfer` sin pasar por un nodo que espera (M1 G0-04).
3. **Transferencia válida:** se arma el `TransferPacket` (`reason`, `trigger` = texto del turno en vista `model`, slots `validated`), su huella `packet_fp` es la HMAC con clave de M7 (`views.project(...).fingerprint` del `StepContext`, ADR 0008) y se emite `run_transferred {transfer_id, to_agent, to_release_id, to_run_id, reason, packet_fp, directory, directory_hash, candidates}`; luego `run_closed {outcome: transferred, closed_by: transfer}`. `closed_by="transfer"` conserva `active_flow` (el linaje muestra dónde ocurrió). El plan validado queda en `TurnFrame.transfer_plan`.
4. **Sin valores:** ningún evento lleva valores de slots ni el texto del turno (T-TR-15): el paquete aparece solo como `packet_fp` y como nombres de slots. Efecto lateral conocido: `project` tokeniza las hojas del paquete en el vault del run origen (igual que la salida de un nodo `agent`); la huella no depende de eso.

5. **Apertura del destino** (`TurnEngine._continue_in_target`, paso 12b). Cuando `_finish` del origen ya guardó el run (`save_run`) y encadenó sus eventos, el turno sigue en un `RunState` nuevo construido con `_new_state`, igual que en `start_run`:
   - `run_id` = `to_run_id`, con la misma `session_id`, el mismo principal (con el `auth` del turno), `on_behalf_of`, subject y locale;
   - la release resuelta al validar y el agente que esa release fija;
   - `origin = RunOrigin{transfer, transfer_id, from_run_id, from_agent, from_release_id, from_event_hash, depth}`. `from_event_hash` es el hash del `turn_completed` del origen, el último evento de su cadena al cerrar el turno (decisión P2: cubre `run_transferred` y `run_closed` por encadenamiento). Si la cadena no tiene hash (sin M11), es un error de configuración (`IllegalTransition`).
   - **Siembra:** cada slot del paquete entra como `Slot(value, status="validated", source_turn=1)`. M0 no tiene un campo `source` en `Slot`: la procedencia la dan `RunState.origin` y los `accepted_slots` de `transfer_received` (spec de transferencia §5.2.5). Hechos, decisiones y acciones del origen no viajan.

   Los eventos de apertura son `run_started{origin}` (con los `reportable_attrs` de `start_run`) y `transfer_received{transfer_id, accepted_slots (solo nombres, ordenados), packet_fp}`. Van como **preludio** del buffer (`EventBuffer.add_prelude`), antes del `turn_started` del destino. Después, el destino procesa el turno desde el paso 3 (release, abandono, recuperación, tokens, guardas, Understand, flow) con el mismo `turn_id` y el texto del cliente (decisión P4). Abre su propio runtime: su vault es el de su `run_id` y los tokens del origen no pasan. Se guarda con `save_run` **después** del origen.
   - **La base impone un run abierto por sesión** con el índice único parcial `runs_one_open_per_session` (`adapters/sql/schema.sql`: `ON runs (session_id) WHERE status = 'open' AND session_id IS NOT NULL`; los runs `task` quedan fuera). El doble en memoria hace la misma comprobación sobre el estado final del commit.
   - **La UoW aplica primero las escrituras que cierran:** el índice no se puede diferir y Postgres lo comprueba fila a fila, así que `PostgresUoW._apply` ordena los runs de forma estable por `status == "open"` (el `UPDATE` que cierra el origen va antes del `INSERT` del destino), sea cual sea el orden de los `save_run`.
   - **Una violación es `VersionConflict`** con mensaje propio, `"la sesión ya tiene un run abierto (runs_one_open_per_session)"` (el adaptador la distingue por `UniqueViolation.diag.constraint_name`). No se aplica nada. **No hay reintento:** ni M4 ni M9 capturan `VersionConflict`, así que llega al manejador genérico de M9 y el cliente recibe `500 internal_error` (m09 §3.5).
   - **La ruta de Postgres está sin verificar** (fase 7, sin docker): el índice, el orden real de las sentencias y la traducción del `UniqueViolation` solo se probaron con el doble y con una conexión simulada (`tests/m04/test_postgres_uow_one_open_run.py`). Falta `docker compose up -d postgres && uv run pytest tests/integration tests/contracts`. Una base existente con dos runs abiertos en una sesión hace fallar la creación del índice; la consulta previa está en el comentario de `schema.sql`.
6. **Resultado e idempotencia:** todo `TurnResult` lleva `agent` = el agente del run que respondió (`build_turn_result`). El `TurnResult` del origen no se guarda por separado. El del turno es el del destino, con `messages` = mensajes del origen (p. ej. "te comunico con…") seguidos de los del destino, y queda guardado bajo `(run destino, client_turn_id)`. Un reintento lo encuentra porque `find_run_by_session` devuelve el destino abierto: devuelve el mismo resultado y no abre un tercer run (Review Focus 1).
7. **Atomicidad (alcance):**
   - **Dentro** de la UoW del turno: los dos `save_run`, las dos cadenas de eventos, el uso y el resultado. Una caída en el `commit` no deja el origen cerrado sin destino, ni un destino sin origen, ni eventos bajo ningún `run_id` (T-TR-07).
   - **Fuera** de ella:
     - El transcript: `TurnRecorder.record_turn` escribe en su propio almacén durante el paso 13 de cada run. Tras la caída quedan entradas huérfanas del origen y de un destino que nunca existió (`test_crash_on_commit_leaves_nothing` lo fija).
     - Las escrituras de M3, con sus dos commits propios (3.8 Abierto, §11).
   - El reintento con el mismo `client_turn_id` rehace la transferencia completa. El `to_run_id` sale otra vez del `IdSource`, así que **cambia**, y el transcript del origen recibe otra vez las entradas del turno.
   - Si el origen no tiene cadena con hash (sin M11), `IllegalTransition` se lanza antes del `commit`: no se guarda nada y el lease se libera. Si el destino transfiere a su vez (solo con `max_transfers_per_session > 1`), el paso 12b se repite y el resultado se guarda una vez, bajo el último run.

`transfer_id` y `to_run_id` salen del `IdSource` (`IdKind.transfer`, `IdKind.run`); el `transfer_id` se pide antes de validar, así que un rechazo también lo lleva.

La resolución abre `TurnSpan.transfer` (§3.9): el span cubre la validación y informa el desenlace (`transferred` o `rejected`).

### 3.9 Telemetría del turno (ADR 0003 #4; plan de observabilidad 2026-10-02, U4, F9 y F12)

M4 informa cada turno por un puerto local, `TurnTelemetry` (`agent_core/turn/ports.py`): `turn(scope, links=())` abre el contexto del turno y entrega un `TurnSpan` con `record(events)` y `transfer(transfer_id)`. M4 **nunca importa OpenTelemetry ni `agent_telemetry`**: `.importlinter` prohíbe `agent_telemetry` en el contrato `turn` y `test_m4_never_imports_opentelemetry` cubre el paquete externo. Por defecto, `NoTurnTelemetry` (no hace nada: pruebas, replay, `agentcore record`, evaluación del registry). La implementación real es `OtelTurnTelemetry` (composition, sobre `agent_telemetry`): un span `invoke_agent` en vivo por turno, bajo el contexto activo (en `serve`, el `agentcore.api.request` del request).

- **`TurnScope`:** `run_id`, `turn_id`, `session_id`, `release`, `agent` (`EntityRef`), `entry` (`start_run` | `turn`), `principal_type` y `locale` del estado al abrir el turno. Solo ids y valores de lista cerrada; nunca un valor de payload ni texto del cliente. `OtelTurnTelemetry` lo traduce a los atributos de `invoke_agent`: los de `bind` (`run_id`, `turn_id`, `session_id`, `agentcore.release`, `agentcore.agent = "id@versión"`), `gen_ai.operation.name = "invoke_agent"`, `gen_ai.agent.name = <id>`, `agentcore.entry`, `agentcore.principal_type`, `agentcore.locale` y, si el turno termina con un `EngineError`, `agentcore.problem_code` (solo el código).
- **Dónde se abre** (pregunta 6 del plan):
  - `handle_turn`: después del lease y de releer el run; cubre `_process` y el `commit`. Un `EngineError` que el turno commitea (abandono, paso 4) se lanza dentro del span, así que el span lleva su código **y se cierra con estado `ERROR` y `error.type = EngineError`**, aunque el vencimiento sea un desenlace normal (decisión pendiente del usuario, TEMAS #19);
  - `start_run`: cubre el alta, `advance`, `_finish` y el `commit`;
  - `_continue_in_target` (transferencia): cubre el `_process` del destino; el `commit` es el único del turno y ocurre después, dentro del span del origen. El `invoke_agent` del destino es **hermano** del del origen (su padre es el contexto en que se abrió el del origen) y lleva un *span link* al span de la transferencia (ver «Transferencia» abajo).
- **Transferencia (ADR 0021 D9; spec de transferencia §8):**
  - `_resolve_transfer` abre `TurnSpan.transfer(transfer_id)`, hijo del `invoke_agent` del origen, y lo cierra con `finish(TransferOutcome)`: `transferred` (con el id del agente destino y el `release_id`) o `rejected` (con el `reason_code` y el destino solo si `transfer_rejected` lo repite: es `event_target(request)`, la misma función que llena el evento).
  - `TransferSpan.link` es un handle opaco para M4. Si la transferencia es válida, `_continue_in_target` lo pasa en `links` al abrir el turno del destino; M4 no sabe qué contiene (en composition, `TransferLink`: el contexto del span y el contexto en que se abrió el `invoke_agent` del origen). Un rechazo no abre turno de destino.
  - Atributos `agentcore.transfer.*` (ids y enums): `OtelTurnTelemetry` los pone con la lista cerrada. No se persiste nada.
  - Las fallas del span de la transferencia (abrir, `link`, `finish`) se contienen igual que las del turno (`_GuardedTransferSpan`): sin enlace y sin cambiar eventos ni resultado.
- **Sin span:** los turnos deduplicados (paso 1), los `409`, los `410` de un run ya cerrado y un `start_run` repetido por su `Idempotency-Key` (los mismos que no emiten `turn_completed`, §3.7). `sweep` no abre spans.
- **`record(events)`** se llama después de **cada** `EventChain.append` del turno, con la lista que se encadenó y en orden: en `_finish` (con `turn_completed`) y en `TurnEventSink.record` (`observe`), por donde pasan los vuelcos de M3 (`tool_called`). Recibe los eventos que se pasaron a `EventChain.append` (los mismos ids y el mismo orden que quedan en la cadena), no lo que ese método devuelve: llegan **sin** `seq`, `prev_hash` ni `hash`, que asigna la cadena. A la telemetría solo le hacen falta ids, `ts` y `latency_ms`. Los dicts de payload se comparten con la cadena (sin copiar): la telemetría los lee, no los modifica.
- **Mejor esfuerzo, sin deduplicar:** los hijos se exportan cuando se encadenan los eventos, antes del `commit`. Si el turno luego se revierte (el `commit` falla) y se reintenta, los spans del primer intento ya salieron y el reintento exporta otros: no hay deduplicación. Es telemetría, no registro: la verdad es la cadena de auditoría.
- **La telemetría no cambia nada:** solo lee eventos ya construidos; no consume `Clock` ni `IdSource` y no cambia el orden de los ids. Con los mismos puertos, los eventos y los hashes son los mismos con telemetría o sin ella (`test_telemetry_does_not_change_events_or_ids`).
- **Una falla de la telemetría nunca rompe el turno** (I4): el motor abre cada turno con `observed_turn` (`agent_core/turn/telemetry.py`). Si abrir, `record`, `transfer`, `finish` o cerrar lanza una `Exception`, el turno sigue (con `NO_SPAN` si falló al abrir) y queda un `WARNING` en el logger `agent_core.turn` con el lugar y **solo el tipo** de la excepción (su mensaje puede llevar un endpoint o una cabecera). La telemetría tampoco puede tragarse ni reemplazar la excepción del propio turno: el valor de retorno de su `__exit__` se ignora.

## 4. Invariantes

- Un turno produce **una** transacción de estado (más las dos por escritura de M3).
- `client_turn_id` repetido nunca reprocesa. Debe ser único **por sesión**, no solo por run: tras una transferencia, la deduplicación (paso 1) busca en todos los runs de la sesión, así que reusar el `client_turn_id` de un turno del origen devuelve aquel resultado.
- Nunca hay más de un flow activo.
- Con un `confirm` pendiente, ningún comando cierra el run salvo interrupción, `cancel` y `handoff`.
- Todo instante sale del `Clock`.
- Solo un run abierto por sesión: una transferencia cierra el origen y abre el destino en la misma transacción. Lo garantizan el motor y la base (índice `runs_one_open_per_session`, §3.8 punto 5; un segundo run abierto es `VersionConflict` al commitear). La ruta de Postgres está sin verificar (sin docker).

## 5. Fallas

| Falla | Comportamiento |
|---|---|
| Turnos concurrentes | `409 turn_in_progress` (MVP; buzón en producción) |
| Turno en run cerrado o escalado | `410 run_closed` |
| Turno concurrente con una transferencia | `find_run_by_session` se lee antes del lease. Si otro turno transfiere y commitea en medio, el estado fresco del origen está cerrado y la respuesta es `410 run_closed`, aunque la sesión ya tenga el destino abierto. El reintento del cliente encuentra el destino. No se cambia (borde conocido). |
| Caída a mitad del turno | la transacción no se commitea; el reintento con el mismo `client_turn_id` rehace el turno |
| Release revocada | `escalate(release_revoked)` |
| Idioma no soportado | plantilla en `default_locale`; el flow no avanza |
| Falla de la telemetría del turno | el turno sigue sin ella; `WARNING` en `agent_core.turn` con el lugar y solo el tipo de la excepción (§3.9) |

## 6. Eventos que emite

`run_started` (con `origin` en un run transferido), `turn_started` (con la salida de M6 y el instante del `Clock`), `command_emitted`, `expiry_evaluated`, `turn_completed`, `run_closed`, `run_transferred` y `transfer_rejected` (run origen, §3.8) y `transfer_received` (run destino, después de su `run_started` y antes de su `turn_started`, §3.8 paso 5). `injection_flagged` lo construye M6 y M4 lo agrega.

## 7. Pruebas

Con todos los dobles, `FakeClock` y un flow de prueba con dos intenciones y una interrupción.

| ID | Caso | §13 |
|---|---|---|
| T-M4-01 | "bloquea mi tarjeta y disputa este cargo": arranca el de mayor prioridad, el otro queda pendiente con acuse | 9 |
| T-M4-02 | Intención nueva durante `confirm` → pendiente + `unclear`, la acción sigue `proposed` | 9, 11 |
| T-M4-03 | Interrupción de fraude se antepone al flow activo e invalida acciones | 9 |
| T-M4-04 | Con `confirm` pendiente, `out_of_scope` da `unclear` y no cierra; `cancel`, `handoff` e interrupción sí aplican | 11 |
| T-M4-05 | Respuesta por botón no pasa por Understand y nunca da `unclear` | 11 |
| T-M4-06 | Superar `max_repair_turns_per_run` escala, sumando `unclear` de `confirm` | 11 |
| T-M4-07 | Inactividad → `abandoned` con acciones canceladas y `expiry_evaluated` | 10 |
| T-M4-08 | `escalate` emite `handoff_created` y el turno siguiente recibe `410` | 10 |
| T-M4-09 | Release revocada escala sin ejecutar nodos | 10 |
| T-M4-10 | Turnos concurrentes → `409` | 12 |
| T-M4-11 | `client_turn_id` repetido devuelve el mismo resultado sin reprocesar | 5 |
| T-M4-12 | Recuperación: run con acción en `executing` va a `verify` antes de procesar el mensaje | 3 |
| T-M4-13 | Idioma no soportado: plantilla, flow sin avanzar | 8 |
| T-M4-14 | Al terminar el flow se ofrece la pendiente y solo arranca con `affirm` | — |
| T-M4-15 | `clarify` agotado → `end(clarify_exhausted)` o `escalate(low_confidence)` según el agente | — |
| T-M4-16 | Con `FakeClock` que avanza dentro de cada etapa, `turn_completed` reporta `duration_ms` y `stages` exactos; respuesta por botón deja `understand_ms = None` | — |
| T-M4-17 | Un turno deduplicado, un `409` y un `410` no emiten `turn_completed`; un turno con idioma `unsupported` sí, con `flow_ms = None` | — |
| T-M4-18 | `start_run` repetido con la misma clave y body devuelve el mismo `RunResult` sin crear otro run; otro body → `409 idempotency_conflict`; la clave es por principal; run y clave se confirman en una transacción (crash en el commit no deja ninguno) | 12 |
| T-M4-19 | Transferencia (`tests/m04/test_transfer_validation.py`): destino fuera del directorio leído (aunque publicado) o `none` → `not_in_directory`; especialista revocado → `no_active_release`; principal no elegible, sin contrato o denegado por `authorize_agent`/`authorize_subject` → `not_eligible`; paquete fuera del contrato → `accepts_mismatch`; `origin.depth` en el tope → `transfer_limit`; el orden de §3.8; en `start_run` → `no_turn`. Todo rechazo sigue por `rejected` en el mismo turno | — |
| T-M4-20 | Transferencia válida: `run_transferred` con `packet_fp` HMAC verificable del paquete y `run_closed{transferred, transfer}`. T-TR-15 con PII sintética como disparador y slot: no aparece en ningún evento del turno (transferencia válida o rechazada), ni los valores que quedaron en el vault del origen. También: contrato de la release resuelta ahora (no el del directorio leído), alias `prod` ausente y versión fijada inexistente → `no_active_release`, `to_agent` solo para ids del directorio leído | — |
| T-M4-21 | Run destino en el mismo turno (`tests/m04/test_transfer.py`, T-TR-01, 02, 07 y 08). El especialista responde con la release del especialista, el mismo `turn_id` y slots sembrados `validated`; `run_started{origin}` → `transfer_received` → `turn_started`. `origin.from_event_hash` es el hash del `turn_completed` del origen; las dos cadenas verifican y alterar un evento del origen rompe la suya. Los mensajes del origen van antes que los del destino. Hay un solo `commit`, el origen se guarda antes que el destino y el destino abre su propio vault. Un reintento del turno de la transferencia, o de un turno anterior del origen, devuelve lo guardado sin abrir un tercer run. Una caída en el `commit` no deja nada. Principal, subject y `on_behalf_of` se conservan. El turno siguiente va al destino. Revisión: una caída en el `commit` no deja eventos bajo ningún run y deja entradas huérfanas en el transcript; sin cadena con hash → `IllegalTransition`, sin nada guardado; el destino escala en su primer turno (el resultado queda bajo el destino y el reintento lo devuelve); una transferencia pedida por el destino → `transfer_limit`, rama `rejected`, sin tercer run; el destino solo puede proponer una escritura en ese turno. Buffer: `test_prelude_goes_before_turn_started` | — |
| T-M4-22 | Un run abierto por sesión (fase 7). Contrato de `UnitOfWork` (`tests/contracts/test_uow_contract.py`): un segundo run abierto en la sesión, o dos nuevos en un mismo commit, → `VersionConflict("…run abierto…")` sin aplicar nada (ni runs ni eventos); cerrar el origen y abrir el destino commitea en cualquier orden de `save_run`; otras sesiones y los runs `task` no se limitan. Adaptador sin base (`tests/m04/test_postgres_uow_one_open_run.py`): el `UPDATE` que cierra va antes del `INSERT` abierto, el orden de guardado se conserva dentro de cada grupo y solo el `UniqueViolation` de `runs_one_open_per_session` da el mensaje propio. Postgres (`tests/integration/test_m4_postgres.py`, variante `postgres` del contrato): **sin verificar** (sin docker) | — |
| T-M4-23 | Telemetría del turno (§3.9; `tests/m04/test_telemetry_port.py` con `RecordingTelemetry` y `FailingTelemetry` de `testing/fakes/telemetry.py`, y `tests/composition/test_turn_telemetry.py` sobre OTel). Por defecto, no-op. `handle_turn` y `start_run` abren un contexto con los ids de correlación; `record` recibe los eventos que se pasaron a `EventChain.append` (mismos ids y orden; sin `seq` ni hashes), también los que vuelca M3; sin contexto en duplicados, `409`, `410` ni `start_run` repetido; el abandono cierra dentro del span con `agentcore.problem_code`; una excepción cierra el contexto y se propaga; la transferencia abre el turno del destino con su `run_id` y el mismo `turn_id`, con el link del span de la transferencia, y la resolución informa su desenlace (`transferred` | `rejected`, T-TR-16); mismos eventos con telemetría o sin ella; una telemetría que falla (al abrir, en `record`, al cerrar, en `transfer`) no rompe el turno ni oculta su error, y deja un aviso solo con el tipo. Sobre OTel: un `invoke_agent` por turno con atributos de la lista cerrada; por HTTP (`TestClient`, endpoints síncronos en el threadpool) el `invoke_agent` es hijo de `agentcore.api.request` y `trace_id` y `first_turn.trace_id` son su trace id; sin OTel, el respaldo del middleware; un `410` tras el motor lleva el mismo `trace_id` que su request; un reintento deduplicado devuelve el `trace_id` original | — |

## 8. Evaluación

Contención, tasa de resolución segura (junto con la unidad 6), tasa de `abandoned`, aclaraciones por run, tasa de modo degradado, tasa de ofertas de intención pendiente aceptadas.

Tiempos, todos desde el log de auditoría (no dependen del muestreo de trazas):

- **Turno:** latencia p50/p95 de `turn_completed.duration_ms` y por etapa (`stages`), para ubicar el cuello de botella.
- **Conversación:** duración total (`ts` de `run_started` → `run_closed`), tiempo activo del motor (Σ `duration_ms`), tiempo de espera del usuario (duración total − tiempo activo), turnos por run.
- **Esperas:** tiempo entre un turno que deja `awaiting` en `step_up` o `confirm` y el turno siguiente; tasa de abandono por tipo de espera.

## 9. Puntos de iteración

- Buzón de ráfagas (producción): reemplaza la respuesta `409` sin cambiar el pipeline.
- Pila de flows y `subflow` (producción): cambia 3.3; el resto del pipeline no.
- La tabla de manejadores es un dato ordenado: agregar un comando = una fila.

## 10. Definición de terminado

- [x] Pipeline completo con dobles, T-M4-01…18 en verde.
- [x] Integración con Postgres: bloqueo optimista, `409` con dos conexiones y transacción única por turno (`tests/integration/test_m4_postgres.py`, más la suite de contrato de `UnitOfWork` sobre Postgres y T-M3-03/04 en `tests/integration/`).
- [x] Transferencia: validación y rama `rejected` (§3.8), T-M4-19 y T-M4-20 en verde.
- [x] Transferencia: run destino en el mismo turno, atómico e idempotente (§3.8 pasos 5 a 7, paso 12b), T-M4-21 en verde.
- [x] Un run abierto por sesión impuesto por la UoW (§3.8 punto 5), T-M4-22 en verde con el doble y la conexión simulada.
- [ ] Variante Postgres de T-M4-22: **sin verificar** (sin docker en la fase 7).
- [x] `sweep` invocable por un comando (`agentcore sweep --dsn … --registry …`) sobre Postgres real (`tests/integration/test_sweep_postgres.py`).
- [x] Telemetría del turno por el puerto `TurnTelemetry` (§3.9), sin importar OTel, T-M4-23 en verde.

## 11. Abiertos

- **Escrituras de M3 en el turno de una transferencia (ADR 0021; pendiente de la spec, no se decide aquí).** M3 hace sus dos commits en UoW propias (`commit_point`), fuera de la UoW del turno.
  - Comportamiento actual del destino: en el turno de la transferencia no puede ejecutar una escritura. Una escritura exige una propuesta de un `confirm` del mismo run, confirmada en un turno posterior; el destino a lo sumo propone (`test_the_target_can_only_propose_a_write_in_the_transfer_turn`: sin commits de M3, un solo `commit`).
  - El origen sí puede escribir antes de transferir en el mismo turno (`confirm` sí → `tool` → … → `transfer`). Esos commits de M3 guardan el run origen abierto antes del `commit` final. Una caída en ese `commit` deja el origen abierto con la acción ya aplicada y sin destino. El reintento genera otro `to_run_id`, así que una clave de idempotencia derivada del `run_id` (no es el caso de `action_id`) podría reejecutar.
  - Si en el futuro el destino pudiera escribir en ese turno, una caída en el `commit` final podría dejar abiertos el origen y el destino a la vez.
  - Falta decidir si una transferencia admite escrituras en el mismo turno.

Los dos abiertos originales quedaron resueltos el 2026-09-29 por el usuario (las confirmaciones C1–C15 del plan de implementación quedaron aprobadas; ver §13):

- **P1 (`deny` ante la oferta de una intención pendiente):** ver 3.3.
- **P2 (turno que encuentra el run vencido):** ver paso 4 de 3.1.

## 12. Fronteras (nota de la rev. 2)

- El contrato `turn` de `.importlinter` usa `allow_indirect_imports = True` (aprobado el 2026-09-29): M4 importa `agent_core.interpreter`, que internamente usa `flows` y `views`. Siguen prohibidos los imports **directos** de M4 a `flows`, `views`, `response`, `knowledge`, `api`, `adapters`, `cli`, `contracts` y `registry`. Todo lo que M4 necesita de M7 entra por `RuntimeFactory`/`TurnRuntime` (`agent_core/turn/ports.py`).

## 13. Decisiones de la rev. 2 (2026-09-29)

Aprobadas por el usuario: C1 (`RuntimeFactory`/`TurnRuntime` sobre M7), C2 (plantillas del motor con `registry.get`, sin variables; la versión sale de los pines de la release), C3 (`signal_policy` evalúa `{"message": {"text": <vista model>}}`), C5 (`start_run -> RunResult`), C6 (`TraceIds`), C7 (el flow interrumpido va a `pending_intents` y reinicia desde su entrada), C8 (`cancel` cierra solo el flow, sin mensaje), C10 (M4 no emite `response_emitted`; solo rellena `transcript_fp`), C11 (`closed_by="flow"` para `end(abstained|clarify_exhausted)`), C12 (`expiry_evaluated` en cada turno), C13 (`turn_count = 1` en `start_run`), C14 (solo el `unclear` de texto suma reparación), C15 (`lease_ttl` 60 s), puertos locales `UnderstandPort`, `TurnRecorderPort`, `EventChain`; `turn_started` reservado al frente del buffer.

Detalles de implementación que el spec no fijaba (revisar):

- `turn_count` se incrementa al empezar el turno (no al persistir): así `Slot.source_turn` y `degraded_turns` usan el número del turno en curso.
- `input` de un run task entra como slots `claimed`, salvo que el agente declare `input_schema`: entonces `start_run` lo valida contra el contrato (`packet_problem`; si falla, `422 invalid_request` y la clave de idempotencia se libera) y los slots entran `validated`.
- Tras `cancel`, si quedan intenciones pendientes se ofrece la primera.
- `clarify` agotado con `on_clarify_exhausted = "end"` cierra `clarify_exhausted` sin mensaje (no hay plantilla del motor para eso; la app usa `outcome`).
- P1: el contador de la oferta repetida vive en `node_attempts["offer:<flow>"]`; al descartar la última oferta se responde con la plantilla `clarify` sin contarla como aclaración.
- Un turno abandonado (paso 4) no guarda `TurnResult` por `client_turn_id`: el reintento recibe `410`.
- `unclear` de `confirm` por texto suma siempre a `repair_turns_used` (equivale a "creció `node_attempts`" salvo en el intento que agota `max_attempts`).
- Los `Slot` de Understand se guardan tal cual (vista `model`); un slot ya `validated` no se pisa.

**Token vencido en `confirm` (decidido el 2026-09-29):** si el paso 6 cancela una propuesta por token vencido y el run esperaba confirmación, el turno avanza con `resume = none` (aunque llegue un `yes` por botón o un `affirm`): el `confirm` propone de nuevo con token nuevo y el usuario debe confirmar otra vez. Un token vencido nunca confirma. No suma `repair_turns_used`.

### Observaciones de la revisión (2026-09-29; el spec no las fija, no se cambian)

- **Liberación del lease ante una excepción:** `_release_quietly` traga la falla de su propia UoW y no deja rastro en eventos ni estado. No es posible dejarlo visible dentro del diseño actual: el turno que falló hizo rollback, el catálogo de eventos de M0 no tiene un evento de fallo de turno y M4 no tiene puerto de logs. Consecuencia: si la liberación también falla, el lease vence solo por su TTL (`lease_ttl`, 60 s) y el reintento recibe `409` hasta entonces. Se añadiría un evento o un puerto de observabilidad solo con un cambio de M0/M11.
- **Orden `409`/`410` (decidido el 2026-09-29):** un run ya cerrado recibe `410` aunque haya un lease ajeno vigente (se mira `status` antes de tomar el lease; tras tomarlo se vuelve a mirar con el estado fresco). La deduplicación por `client_turn_id` sigue yendo primero.
- **`affirm`/`deny` con `awaiting = slot`:** sin `confirm` pendiente y con el run esperando un slot, `continue`, `affirm` y `deny` se toman como respuesta del slot (`slot_answer` con el texto crudo). El spec solo define `continue` para `collect` (§3.3).
- **`inactive_after`:** solo se fija en runs conversacionales (§3.6); el cierre `abandoned` del turno y del barrido comparte una sola función (`closed_state`).

## 14. Fase B: contraste con M5 y M11 reales (2026-09-29)

**M11 (verificado, `tests/m04/test_real_m11.py`):** `TurnRecorder` cumple `TurnRecorderPort` y `AuditLog` cumple `EventChain` (mypy). Dos discrepancias resueltas dentro de M4:

- **Orden de las referencias de `record_turn`:** M11 devuelve `[user, *rejected, final]` (m11, decisión 3); M4 tomaba `refs[1]`, que con borradores rechazados era la huella de un borrador. Ahora usa `refs[-1]` (`transcript_fp` = huella de la respuesta final). El doble `InMemoryTurnRecorder` sigue ese orden.
- **`EventChain.append`** devuelve `-> object` (M11 devuelve los eventos encadenados; M4 los ignora).
- Recordatorio de M11: el `recorder()` de `AuditLog` solo agrega los eventos de M3; M4 ya vuelca antes los pendientes del turno (paso 14).

**M5 (cableado con `DecisionUnderstand`, `agent_core/turn/adapters.py`; decidido el 2026-09-29):** `UnderstandPort.run(UnderstandRequest) -> UnderstandOutcome` no equivalía a `UnderstandService.run(text, UnderstandContext, locale)`. Se resolvió dentro de M4 y de la composición, sin tocar M0:

1. `token_vault`: el adaptador lo toma de `UnderstandRequest.step.vault` (el `StepContext` del `TurnRuntime`), así M4 no importa `agent_core.views`.
2. `recent_turns`: `DecisionUnderstand` recibe un `TranscriptStore` (puerto de M0) y `recent_turns: int` (sin valor por defecto; lo fija la composición) y usa `recent_turns(run_id, n)` en vista `model`, sin borradores rechazados (con borradores el resultado puede traer menos de `n`). **Política (2026-09-30, tema #12):** `n` fijo de 6 turnos por despliegue (`EngineConfig.recent_turns`), medido en turnos; se trunca sin resumir; cada entrada es el mensaje del cliente o la respuesta del agente en vista `model`; no hay arrastre entre runs.
3. `flows`, `interrupts` y `model_ref` salen de la release fijada (`release.entities[flow]`, `release.interrupts`, `pinned_ref(agent.understand)`); un agente sin `understand` da `DecisionConfigError`.
4. `slots_model_ref`: sale de la release. **Cambio 2026-09-30 (aprobado):** `Agent.slots_model: RefSpec | None` (M0 rev. 8, junto a `Agent.understand`), validado por G0 como referencia a un `decision_model` y fijado con `pinned_ref`; ya no es configuración del despliegue (`EngineConfig.slots_model` desapareció). Así el mismo `release` se comporta igual en cualquier despliegue y el replay lo reproduce.
5. `UnderstandRequest` gana `turn_id` y `step`; `UnderstandOutcome` gana `model_calls` y `tokens` (junto a `cost_usd`). `p_cal` no se propaga: `command_emitted` no lo lleva.

~~**Abierto (M2/M4):** llamadas de Understand frente a `max_model_calls_per_turn`~~ **Resuelto 2026-09-29 (opción 2, decidida por el usuario):** contador aparte. M4 suma `UnderstandOutcome.model_calls` a `budgets_used.turn_understand_calls` (campo nuevo de M0, rev. 7; `begin_turn` de M2 lo reinicia) y, si supera `TurnConfig.max_understand_calls_per_turn` (2 por defecto, ADR 0005), escala con `budget_exceeded` sin avanzar el flow. `max_model_calls_per_turn` sigue contando solo las llamadas de M2 (`decide` y `respond(generate)`): son dos límites, y el techo total de llamadas de un turno es su suma. El tope vive en `TurnConfig` y no en `Budgets` para no romper los agentes publicados. Pruebas: `tests/m04/test_understand_budget.py`.

## 15. Fase C: Postgres (2026-09-29)

- **Adaptador:** `agent_core/adapters/postgres_uow.py` (`PostgresStore`, `PostgresUoW`, `PostgresAuditSink`, `PostgresOutbox`, `PostgresCostCounters`) y `agent_core/adapters/sql/schema.sql` (esquema plano, idempotente; el log de auditoría sigue en `audit_events.sql`). Sin dependencias nuevas: `psycopg[binary]` ya lo trajo M11.
- **Transacción única:** la UoW acumula todo y lo aplica en `commit()` dentro de **una** transacción de Postgres. **Bloqueo optimista:** `UPDATE runs … WHERE state_version = <base>` (o `INSERT … ON CONFLICT DO NOTHING` para un run nuevo); 0 filas → `VersionConflict` y no se aplica nada. **Lease:** sentencia autocommit propia (`INSERT … ON CONFLICT DO UPDATE … WHERE mismo turno OR vencido`), visible de inmediato y no lo deshace un rollback; `release_turn` se aplica con el commit.
- **Contrato:** `tests/contracts/test_uow_contract.py` corre los mismos 31 checks contra el doble en memoria y contra Postgres (parámetro `postgres`, marcador `integration`). Para Postgres los eventos de prueba van encadenados (`prev_hash` del primer evento = `genesis_hash`) y `append_outside_turn` recibe un evento ya encadenado: `adapters` no puede importar `audit`, así que `PostgresAuditSink` no encadena y M9 debe usar `AuditLog.append_standalone`.
- **Marcador y CI:** las pruebas de Postgres llevan `@pytest.mark.integration` y se omiten sin base (`AGENTCORE_REQUIRE_POSTGRES=1` las exige; el CI ya levanta el servicio). Se mantuvo la convención de M11 en vez de `AGENT_CORE_INTEGRATION=1`.
- **`agentcore sweep`:** `agent_core/cli.py` (raíz de composición) compone el `Sweeper` con la DSN (`--dsn`, `AGENTCORE_REGISTRY_DSN` —la misma de `serve` y `migrate`— o el alias anterior `AGENTCORE_DATABASE_URL`; nunca se imprime) y el registry de Postgres; con `--registry <dir>` usa en su lugar un `RegistryPort` mínimo sobre el directorio de autoría (por versión exacta) (ADR 0023). Errores de Postgres o del registro salen con exit 1 y sin la DSN.
- **Ediciones de prueba aprobadas:** `tests/m03/harness.py` (`World(uow_factory=…, record=…)`) y `tests/contracts/test_uow_contract.py` (backend `postgres`, eventos encadenados).
- **Cadena de auditoría concurrente:** si dos UoW encadenan el mismo `seq` sin pasar por `save_run` (p. ej. `append_standalone` de M9 contra un turno), la segunda falla con `VersionConflict` (mapeo de `UniqueViolation`), no con un error crudo de psycopg.
- **Un run abierto por sesión (fase 7, sin verificar):** índice único parcial `runs_one_open_per_session`. `_apply` escribe primero los runs que no quedan abiertos; el `UniqueViolation` cuyo `diag.constraint_name` es el del índice se traduce a `VersionConflict("la sesión ya tiene un run abierto (runs_one_open_per_session)")` y los demás conservan el mensaje de la cadena de auditoría. Ver §3.8 punto 5.
- **Revisión (2026-09-29), no aplicado:** `Outbox.mark_delivered` usa `now()` de SQL (el puerto no recibe hora); `apply_schema` solo da GRANT sobre `audit_events`; el barrido aborta entero si el registro no tiene el agente de un run (`SchemaError`), y ese lease queda hasta su TTL.
- **Pendiente:** un turno que falla deja el lease hasta su TTL si la liberación también falla (ver §13); el barrido lo deja igual si el registro no tiene el agente (exit 1).

## 16. Composición del motor (2026-09-29)

- **Dónde vive:** `agent_core/composition/` (decisión del usuario, opción A). `build_turn_engine(EngineDeps)` arma `TurnEngine` con M2, M3, M5, M6, M7, M8, M10 y M11 reales; todo lo del mundo exterior (almacenamiento, LLM, proveedores, tools, autorización, reloj, IDs) entra por `EngineDeps`. `agent_core.turn` sigue sin importar `response`, `views`, `flows` ni `adapters`.
- **`EngineRuntimeFactory`** (`RuntimeFactory` real, C1): la release fijada sale de `EngineDeps.releases` (el `RegistryPort` no lee una release por id); el vault se abre desde `state.token_map`; `model_text` usa `ViewService.tokenize_text` (M7 rev. 3); `render` usa `ViewService.render` con el principal y propósito `respond`; `sealed_token_map` sella solo si el turno agregó tokens; `bound_params` salen de `AuthzPort.bind_params`.
- **`ResponderAdapter`** arma el `ResponderContext` por nodo: hechos en vista `model` con `Projector` (M2 rev. 3), cierre `find_clear_pii`, `lang_cfg` de la release y umbrales de `EngineConfig.lang_thresholds`. `number_format` es un dato de despliegue (`EngineConfig`, m08 §3.3).
- **`turn_id` de los eventos de M2:** `DecisionPort` y `ResponderPort` no reciben `turn_id`, así que `decision_made` y `response_emitted` de esos nodos llegaban con `turn_id = None`. `EventBuffer(turn_id)` lo completa al agregar (solo si viene `None`).
- **Los eventos de M2 no pasan por el sink** sino por `EventBuffer.add` (vía `Closer.apply_outcome`); el sink solo recibe los de M3.
- **No resuelto aquí:** ~~`respond(template_ref)` sin `response_emitted`~~ **Resuelto 2026-09-30 (D6):** lo emite M2 y M4 rellena `transcript_fp`; `Rendered.unknown_tokens` de `render` no se registra; ~~el cobro de `run_tokens`/`run_cost` de Understand~~ **Resuelto 2026-09-29:** `_charge_understand` suma `tokens` y `cost_usd` de Understand a `budgets_used.run_tokens`/`run_cost` (como `charge_model` en M2); `add_usage` toma solo el delta de `run_cost`, así el principal no paga dos veces.
- **Step-up completado (2026-09-30, ADR 0010):** al inicio de `_process`, `_refresh_auth` copia `auth` de la credencial presentada a `state.principal` si `Principal.key` coincide (M9 ya validó firma y `principal_mismatch`); nunca toma identidad, roles ni scopes. El nodo se reintenta solo porque el puntero sigue en él (sin `Resume("step_up_retry")`). Pruebas: `tests/m04/test_step_up_completion.py`.
- **`trace_id` del turno (2026-10-02, U3 y F8 del plan de observabilidad):** `RequestTraceIds` reemplaza a `DerivedTrace` como `TraceIds` por defecto. Devuelve, por capas: (1) el trace id OTel válido del contexto; (2) si no hay, el respaldo que el middleware de M9 sacó de su `IdSource` y publicó con `agent_telemetry.bind_trace_id`; (3) `trace-{turn_id}`, que es **solo el último respaldo**: sin provider de OTel y fuera de un request (replay, `record`, evaluación del registry, pruebas en proceso). Con un provider vivo fuera de un request (p. ej. una evaluación en un proceso con telemetría), el `TurnResult.trace_id` es el trace id del `invoke_agent` del turno (capa 1). Nunca pide un id al `IdSource`: correría la secuencia de ids y cambiaría los bytes de los fixtures. `EngineDeps.telemetry` (opcional) llega a `TurnEngine`; `agentcore serve` pasa siempre `OtelTurnTelemetry()` (sin exportador, los spans son no-op, pero `bind` sigue correlacionando los logs).
- **Replay y `record`:** el mismo motor compuesto, con puertos grabados, corre en `testing/replay` (herramienta de desarrollo, ver M11 decisiones 18–23); `agent_core.turn` no provee `build_engine_runner`.
- Pruebas: `tests/composition/` (`EngineWorld` en `testing/engine_world.py`, con puertos externos guionados y el registro demo `tests/fixtures/registry-demo`).
