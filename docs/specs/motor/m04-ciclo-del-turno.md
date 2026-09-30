# M4 — Ciclo del turno

- Estado: rev. 2 (2026-09-29) · Fase 2 · Fase A implementada con dobles · Fase B: grabador y cadena de M11 verificados; Understand pendiente de decisión · Fase C (Postgres) pendiente
- Paquete: `agent_core.turn`
- Origen: spec general §4.1 (estado, release, revocación, recuperación, abandono), §4.4 (uso del resultado de Understand), §4.5, §4.6, §4.8, §4.9, §4.10, invalidación de §4
- ADRs: 0004 (intenciones e interrupciones), 0007 (precedencia con `confirm` pendiente), 0013 (cierre por escalamiento)
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

Dependencias por constructor (solo por nombre): `uow_factory`, `registry`, `clock`, `ids`, `guards` (`GuardsPort`, M6), `understand` (`UnderstandPort`, M5), `actions` (`ActionManager`, M3), `handoff` (`HandoffService`, M10), `recorder` (`TurnRecorderPort`, M11), `chain` (`EventChain`, M11), `audit` (`AuditSink`), `runtimes` (`RuntimeFactory`, puente con M7), `trace` (`TraceIds`), `config` (`TurnConfig`) y, opcional, `authz` (solo `reportable_attrs()` para `run_started`). M2 son funciones (`advance`, `start_flow`, `begin_turn`), no una clase `Interpreter`.

## 3. Comportamiento

### 3.1 Pipeline de `handle_turn`

1. **Deduplicación:** si `client_turn_id` ya tiene resultado guardado, se devuelve ese `TurnResult` sin reprocesar.
2. **Cargar:** `uow.find_run_by_session(session_id)`; `uow.acquire_turn(run_id, turn_id, now, ttl)` toma el lease del turno **antes** de trabajar (M0 §2.9): si otro turno lo tiene → `TurnInProgress` → `409 turn_in_progress`. Run cerrado → `410 run_closed`. Cada commit usa `save_run(expected_version)` como defensa; el lease se libera en el commit final.
3. **Release:** si está `revoked` → `escalate(release_revoked)` sin ejecutar nodos.
4. **Abandono:** si `now − last_activity_at > inactivity_ttl` → M4 cierra el run con `abandoned` (ver 3.6), invalida las acciones y responde `410 run_closed` **sin procesar el mensaje**. La app abre un run nuevo (M9); el motor no reabre el run por su cuenta (decisión P2, 2026-09-29).
5. **Recuperación:** `actions.pending_recovery(state)` no vacío → posicionar el flow en el `verify` correspondiente y avanzar desde ahí antes de procesar el mensaje.
6. **Tokens vencidos:** `actions.expire_tokens(state, turn_id=…)` (devuelve el estado y los eventos `action_cancelled`). Si cancela la propuesta de un `confirm` pendiente, el turno avanza con `resume = none` y el `confirm` re-propone con token nuevo (§13).
7. **Guardas (M6):** idioma, tamaño, injection. `unsupported` → plantilla en `default_locale`, sin Understand ni flow. Actualiza `state.locale`. `injection_flagged` → `degraded = true` para este turno.
8. **Understand:**
   - Si el request trae `confirm: {token, answer}` → no se llama a Understand; `resume = confirm_answer(answer)`.
   - Si no, `understand.run(model_view_text, state, locale)` → `UnderstandResult`, y se emite `command_emitted`.
9. **Manejadores globales** (3.2) → pueden terminar el turno.
10. **Flow e intenciones** (3.3).
11. **Avanzar:** `interpreter.advance(state, ctx, resume)`.
12. **Cierre:** si `StepOutcome.escalation` → `handoff.escalate(...)`; si `end_outcome` → cerrar el run (3.6). Al terminar un flow con pendientes, ofrecer la primera (3.3).
13. **Responder y registrar:** `recorder.record_turn(...)` (transcript + `response_emitted`, M11).
13b. **Medir:** emitir `turn_completed` (3.7) como último evento del turno.
14. **Persistir:** una transacción con `state` (`state_version + 1`), eventos del turno (los que devuelve `execute_write` ya los persistió el `EventRecorder` dentro de los commits de M3 y no se vuelven a agregar; el recorder de M4 vuelca primero los eventos pendientes del turno), outbox y resultado por `client_turn_id`.

`start_run` hace: crear `RunState` (release fijada, principal sin secretos, `locale` inicial = `lang` del request si es soportado, si no `default_locale`), emitir `run_started` con `reportable_attrs`, arrancar `entry_flow` y, en modo task, avanzar hasta un terminal (M1 G0-16 garantiza que un flow task no tiene nodos que esperan; si aun así M2 devuelve una espera, es un bug y el run escala con `validation_failed`).

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

## 4. Invariantes

- Un turno produce **una** transacción de estado (más las dos por escritura de M3).
- `client_turn_id` repetido nunca reprocesa.
- Nunca hay más de un flow activo.
- Con un `confirm` pendiente, ningún comando cierra el run salvo interrupción, `cancel` y `handoff`.
- Todo instante sale del `Clock`.

## 5. Fallas

| Falla | Comportamiento |
|---|---|
| Turnos concurrentes | `409 turn_in_progress` (MVP; buzón en producción) |
| Turno en run cerrado o escalado | `410 run_closed` |
| Caída a mitad del turno | la transacción no se commitea; el reintento con el mismo `client_turn_id` rehace el turno |
| Release revocada | `escalate(release_revoked)` |
| Idioma no soportado | plantilla en `default_locale`; el flow no avanza |

## 6. Eventos que emite

`run_started`, `turn_started` (con la salida de M6 y el instante del `Clock`), `command_emitted`, `expiry_evaluated`, `turn_completed`, `run_closed`. `injection_flagged` lo construye M6 y M4 lo agrega.

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

- [x] Pipeline completo con dobles, T-M4-01…17 en verde.
- [ ] Integración con Postgres: bloqueo optimista, `409` y transacción única por turno (Fase C: pendiente; Docker no estaba disponible al intentarlo el 2026-09-29).
- [ ] `sweep` invocable por un comando (`agentcore sweep`) para la demo: el comando existe, pero sin un sweeper inyectado termina con exit 2 hasta que la Fase C lo cablee sobre Postgres.

## 11. Abiertos

Los dos abiertos originales quedaron resueltos el 2026-09-29 por el usuario (las confirmaciones C1–C15 del plan de implementación siguen pendientes y no se reflejan aquí):

- **P1 (`deny` ante la oferta de una intención pendiente):** ver 3.3.
- **P2 (turno que encuentra el run vencido):** ver paso 4 de 3.1.

## 12. Fronteras (nota de la rev. 2)

- El contrato `turn` de `.importlinter` usa `allow_indirect_imports = True` (aprobado el 2026-09-29): M4 importa `agent_core.interpreter`, que internamente usa `flows` y `views`. Siguen prohibidos los imports **directos** de M4 a `flows`, `views`, `response`, `knowledge`, `api`, `adapters`, `cli`, `contracts` y `registry`. Todo lo que M4 necesita de M7 entra por `RuntimeFactory`/`TurnRuntime` (`agent_core/turn/ports.py`).

## 13. Decisiones de la rev. 2 (2026-09-29)

Aprobadas por el usuario: C1 (`RuntimeFactory`/`TurnRuntime` sobre M7), C2 (plantillas del motor con `registry.get`, sin variables; la versión sale de los pines de la release), C3 (`signal_policy` evalúa `{"message": {"text": <vista model>}}`), C5 (`start_run -> RunResult`), C6 (`TraceIds`), C7 (el flow interrumpido va a `pending_intents` y reinicia desde su entrada), C8 (`cancel` cierra solo el flow, sin mensaje), C10 (M4 no emite `response_emitted`; solo rellena `transcript_fp`), C11 (`closed_by="flow"` para `end(abstained|clarify_exhausted)`), C12 (`expiry_evaluated` en cada turno), C13 (`turn_count = 1` en `start_run`), C14 (solo el `unclear` de texto suma reparación), C15 (`lease_ttl` 60 s), puertos locales `UnderstandPort`, `TurnRecorderPort`, `EventChain`; `turn_started` reservado al frente del buffer.

Detalles de implementación que el spec no fijaba (revisar):

- `turn_count` se incrementa al empezar el turno (no al persistir): así `Slot.source_turn` y `degraded_turns` usan el número del turno en curso.
- `input` de un run task entra como slots `claimed`.
- Tras `cancel`, si quedan intenciones pendientes se ofrece la primera.
- `clarify` agotado con `on_clarify_exhausted = "end"` cierra `clarify_exhausted` sin mensaje (no hay plantilla del motor para eso; la app usa `outcome`).
- P1: el contador de la oferta repetida vive en `node_attempts["offer:<flow>"]`; al descartar la última oferta se responde con la plantilla `clarify` sin contarla como aclaración.
- Un turno abandonado (paso 4) no guarda `TurnResult` por `client_turn_id`: el reintento recibe `410`.
- `unclear` de `confirm` por texto suma siempre a `repair_turns_used` (equivale a "creció `node_attempts`" salvo en el intento que agota `max_attempts`).
- Los `Slot` de Understand se guardan tal cual (vista `model`); un slot ya `validated` no se pisa.

**Token vencido en `confirm` (decidido el 2026-09-29):** si el paso 6 cancela una propuesta por token vencido y el run esperaba confirmación, el turno avanza con `resume = none` (aunque llegue un `yes` por botón o un `affirm`): el `confirm` propone de nuevo con token nuevo y el usuario debe confirmar otra vez. Un token vencido nunca confirma. No suma `repair_turns_used`.

### Observaciones de la revisión (2026-09-29; el spec no las fija, no se cambian)

- **Liberación del lease ante una excepción:** `_release_quietly` traga la falla de su propia UoW y no deja rastro en eventos ni estado. No es posible dejarlo visible dentro del diseño actual: el turno que falló hizo rollback, el catálogo de eventos de M0 no tiene un evento de fallo de turno y M4 no tiene puerto de logs. Consecuencia: si la liberación también falla, el lease vence solo por su TTL (`lease_ttl`, 60 s) y el reintento recibe `409` hasta entonces. Se añadiría un evento o un puerto de observabilidad solo con un cambio de M0/M11.
- **Orden `409`/`410`:** con un lease ajeno vigente sobre un run ya cerrado, el turno recibe `409` (el lease se toma antes de mirar `status`). El spec lista ambos errores sin precedencia.
- **`affirm`/`deny` con `awaiting = slot`:** sin `confirm` pendiente y con el run esperando un slot, `continue`, `affirm` y `deny` se toman como respuesta del slot (`slot_answer` con el texto crudo). El spec solo define `continue` para `collect` (§3.3).
- **`inactive_after`:** solo se fija en runs conversacionales (§3.6); el cierre `abandoned` del turno y del barrido comparte una sola función (`closed_state`).

## 14. Fase B: contraste con M5 y M11 reales (2026-09-29)

**M11 (verificado, `tests/m04/test_real_m11.py`):** `TurnRecorder` cumple `TurnRecorderPort` y `AuditLog` cumple `EventChain` (mypy). Dos discrepancias resueltas dentro de M4:

- **Orden de las referencias de `record_turn`:** M11 devuelve `[user, *rejected, final]` (m11, decisión 3); M4 tomaba `refs[1]`, que con borradores rechazados era la huella de un borrador. Ahora usa `refs[-1]` (`transcript_fp` = huella de la respuesta final). El doble `InMemoryTurnRecorder` sigue ese orden.
- **`EventChain.append`** devuelve `-> object` (M11 devuelve los eventos encadenados; M4 los ignora).
- Recordatorio de M11: el `recorder()` de `AuditLog` solo agrega los eventos de M3; M4 ya vuelca antes los pendientes del turno (paso 14).

**M5 (NO cableado; requiere decisión):** `UnderstandPort.run(UnderstandRequest) -> UnderstandOutcome` no equivale a `UnderstandService.run(text, UnderstandContext, locale) -> (UnderstandResult, list[DecisionMade])`. Lo que falta o difiere:

1. `UnderstandContext.token_vault` es un `TokenVault` de M7 y M4 no puede importar `agent_core.views`; `TurnRuntime` tampoco lo expone.
2. `recent_turns` (spec M5: "M4 arma n fijo") no tiene fuente en M4: `TranscriptStore.recent_turns` existe en M0 pero M4 no recibe ese puerto.
3. `flows` e `interrupts` se pueden derivar de `release.entities[flow]` y `release.interrupts`, y `model_ref` de `pinned_ref(release, decision_model, agent.understand)`.
4. `slots_model_ref` (2.ª llamada de slots) no tiene fuente: `Agent` no tiene campo y `Release.entities` no distingue el rol del modelo.
5. `UnderstandOutcome` no lleva `p_cal`, `model_calls` ni `tokens` (M5 sí los da) y solo `cost_usd`; `max_model_calls_per_turn` no puede contar la 2.ª llamada.
