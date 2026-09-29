# M4 — Ciclo del turno: plan de implementación

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. (La skill `superpowers:writing-plans` no existe en este entorno: el plan sigue el formato de `2026-09-29-m7-vistas-y-tokenizacion.md` y `2026-09-29-m10-escalamiento-y-handoff.md`.)

**Goal:** Construir `agent_core.turn`: `TurnEngine.start_run`, `handle_turn` (los 14 pasos de m04 §3.1) y `sweep`, con lease de turno, idempotencia por `client_turn_id`, manejadores globales como tabla de datos, intenciones pendientes, cierre y escalamiento, `turn_completed`, T-M4-01…17 en verde, la integración con Postgres (bloqueo optimista, `409`, una transacción por turno) y el comando `agentcore sweep`.

**Architecture:** M4 solo importa `agent_core.domain`, `agent_core.ports` y la interfaz pública de `interpreter`, `actions`, `decision`, `guards`, `handoff`, `audit` (contrato `turn` de `.importlinter`; **no** puede importar `views`, `flows`, `response`, `knowledge`). Todo lo que necesita de módulos aún inexistentes o prohibidos entra por **puertos locales** en `agent_core/turn/ports.py` (mismo patrón que D1 de M2): `UnderstandPort` (M5), `TurnRecorderPort` y `EventChain` (M11), `RuntimeFactory`/`TurnRuntime` (arma el `StepContext` de M2 y hace de puente con M7: tokenizar el texto del usuario, renderizar mensajes, sellar `token_map`). El motor es una secuencia de pasos pequeños sobre un `TurnFrame`; los manejadores globales son una tabla ordenada de datos (m04 §9). Una sola UoW por turno (`acquire_turn` → … → `save_run` + eventos + outbox + `put_turn_result` + `release_turn` → `commit`); los dos commits por escritura los hace M3 con `uow_factory`.

**Tech Stack:** Python 3.12, Pydantic v2 (tipos de M0), pytest, ruff, mypy strict, import-linter; Fase C añade `psycopg` 3 (requiere aprobación, ver Confirmación C4) y Postgres 16 (`docker compose up -d postgres`).

**Spec:** `docs/specs/motor/m04-ciclo-del-turno.md` (el spec manda sobre este plan). Contexto que hay que haber leído: `docs/specs/motor/00-indice.md` (§3, §4, §5 dueños del estado, §6 eventos), ADR 0004 (intenciones e interrupciones), ADR 0007 (precedencia con `confirm` pendiente, recuperación), ADR 0013 (cierre por escalamiento), `m00` §2.6–§2.11, `m02` §2, `m03` §3.6 y Abiertos, `m05` §2, `m06` §2, `m10` §11, `m11` §2.

## Estado de dependencias (verificado contra el código el 2026-09-29)

| Módulo | Estado | Cómo lo usa M4 | Verificación |
|---|---|---|---|
| M0 `domain`/`ports` | real | tipos, `UnitOfWork`, `EngineError`, eventos | leído; firmas abajo |
| M2 `interpreter` | real | `advance`, `start_flow`, `begin_turn`, `Resume`, `NO_RESUME`, `StepContext`, `StepOutcome`, `Stop`, `evaluate`, `truthy`, `CircuitBreaker` | `agent_core/interpreter/__init__.py` |
| M3 `actions` | real | `ActionManager.invalidate/expire_tokens/pending_recovery`, `EventRecorder` | `agent_core/actions/manager.py` |
| M6 `guards` | real | `GuardService.run(text_model_view, state, agent, release, request_lang, first_turn, *, turn_id) -> (GuardResult, list[EngineEvent])`, `GuardResult.to_output()` | `agent_core/guards/service.py`, `models.py` |
| M10 `handoff` | real | `HandoffService.escalate(state, request, events_so_far, *, uow, turn_id) -> (RunState, [Escalated], OutboxMessage, Message)` | `agent_core/handoff/service.py` |
| M7 `views` | real, **prohibido para M4** | solo vía `TurnRuntime` (lo cablea quien compone) | `.importlinter` contrato `turn` |
| M5 `decision` | **solo spec** (`m05`; otro agente lo construye) | `UnderstandPort` local + `understand_adapter.py` en Fase B | **[POR VERIFICAR contra el código cuando exista]** |
| M11 `audit` | **solo spec** (`m11`; se asume listo al ejecutar Fase B) | `TurnRecorderPort` + `EventChain` locales + `recorder_adapter.py` en Fase B | **[POR VERIFICAR contra el código cuando exista]** |
| M8 `response` | no existe | M2 ya lo aísla en `ResponderPort`; M4 no lo toca | — |

### Interfaces reales que M4 consume (copiadas del código)

```python
# interpreter
advance(state: RunState, ctx: StepContext, resume: Resume) -> StepOutcome
start_flow(state: RunState, flow: Flow) -> RunState                     # fija active_flow en flow.nodes[0]; reinicia node_attempts
begin_turn(state: RunState, clock: Clock) -> RunState                   # reinicia contadores por turno
Resume(kind: "slot_answer"|"confirm_answer"|"step_up_retry"|"none" = "none", value: JsonValue = None, token: str | None = None)
StepOutcome(state, stop: Stop, messages, events, end_outcome: Outcome|None, escalation: EscalationRequest|None,
            confirmation, step_up, output, rejected_drafts)             # los eventos de execute_write NO vuelven aquí
Stop: awaiting_slot | awaiting_confirmation | awaiting_step_up | awaiting_user | terminal
StepContext(release, agent, locale, clock, degraded, registry, tools, decisions, actions, responder, views, vault,
            ids, uow_factory, bound_params={}, record=append_events, turn_id=None, breaker=CircuitBreaker())  # dataclass congelada
# actions (M3)
ActionManager.invalidate(state, reason: InvalidationReason, *, turn_id) -> (RunState, list[EngineEvent])
ActionManager.expire_tokens(state, *, turn_id) -> (RunState, list[EngineEvent])
ActionManager.pending_recovery(state) -> list[str]                      # action_id de las acciones en `executing`
EventRecorder = (uow, state, events) -> None
# guards / handoff: ver tabla
# ports M0
UnitOfWork: acquire_turn(run_id, turn_id, now, ttl) | release_turn | load_run | find_run_by_session | save_run(state, expected_version)
  | get_turn_result | put_turn_result | append_events(run_id, events) | last_event | enqueue_outbox | add_usage(principal, cost, now)
  | list_inactive(now, limit) | commit      # NOTA: `limit` no está en m04 §3.6
AuditSink.read(run_id) -> list[EngineEvent]     # M4 lo necesita para `events_so_far` (m10 §11.2)
Clock.now() / Clock.monotonic_ns();  IdSource.new_id(IdKind.turn|event|...)
EngineError(ProblemCode.turn_in_progress|run_closed|not_found, detail)  # 409 / 410 / 404; TurnInProgress y VersionConflict son DomainError
```

## Puntos de PARADA (bloquean tareas; NO se resuelven por cuenta propia)

| # | Abierto del spec (m04 §11) | Qué se pregunta al usuario | Tareas bloqueadas |
|---|---|---|---|
| **P1** | `deny` ante la oferta de una intención pendiente | El spec propone "descartarla y ofrecer la siguiente". Preguntar además: (a) si el `deny` descarta la **última** pendiente, ¿el run se cierra (con qué `outcome`; el `end` del flow anterior ya pasó) o queda abierto sin flow?; (b) si el usuario responde otra cosa que `affirm`/`deny` a la oferta, ¿se conserva la oferta? | Task 14 (solo la rama `deny`); T-M4-14 se implementa **solo con `affirm`** hasta que responda |
| **P2** | Qué recibe el turno que encuentra el run vencido (paso 4) | El spec propone `410 run_closed` y que la app abra un run nuevo sobre el mismo subject. Confirmar (o cambiar) qué se devuelve y si el mensaje del turno se descarta | Task 9 (abandono dentro de `handle_turn`); el `sweep` (Task 19) **no** depende de P2 |

**Regla:** al llegar a una tarea bloqueada, detente, formula la pregunta (con las opciones y la propuesta del spec) y espera. No dejes `xfail` ni comportamiento provisional que parezca definitivo.

## Confirmaciones (spec ambiguo o con hueco: CLAUDE.md pide preguntar)

Hacer **una sola ronda de preguntas** al inicio (Task 1, Step 4) con estos defaults propuestos; cada respuesta se anota en el spec (rev. 2, Task 1) antes de tocar la tarea afectada.

| # | Ambigüedad | Default propuesto | Tarea |
|---|---|---|---|
| C1 | M4 no puede importar `views`, pero necesita: texto del usuario en vista `model` (m04 §3.1 paso 8 usa `model_view_text` y nadie lo produce), renderizar tokens en los mensajes de salida, sellar `RunState.token_map` (nadie lo hace: M2/M3/M10 no lo tocan) y armar el `StepContext` (lleva `ViewService` y `TokenVault`) | Puerto `RuntimeFactory.open(state, principal, on_behalf_of) -> TurnRuntime` con `step`, `model_text`, `render`, `sealed_token_map`; lo implementa quien compone (M9/demo) sobre M7 | 2, 8 |
| C2 | Plantillas del motor (`clarify`, `abstain`, `pending_ack`, `pending_offer`, `unsupported_language`, `input_too_large`): `render_message` de M2 es interno y `flows` está prohibido | M4 lee `Template.locales[locale]` con `registry.get`; si la plantilla declara `reads` (variables) → error de configuración (las del demo no tienen variables) | 5 |
| C3 | `Interrupt.signal_policy`: qué datos evalúa la `Policy` (m04 §3.2 solo dice "su `signal_policy` da `true`") | `data = {"message": {"text": <texto en vista model>}}` con `interpreter.evaluate` | 12 |
| C4 | Adaptador Postgres: no hay driver, ni esquema, ni `tests/integration/`, ni marcador `integration` | `psycopg[binary]>=3.2` en `pyproject.toml`/`uv.lock`, esquema SQL plano (`schema.sql`, sin Alembic), marcador `integration` que solo corre con `AGENT_CORE_INTEGRATION=1`, servicio `postgres` en `ci.yml`; tablas de eventos coordinadas con M11 | 24–31 |
| C5 | `start_run` devuelve `TurnResult` (m04 §2) pero M9 `create_run` necesita un `RunResult` (`output` en modo task, `first_turn`) | `start_run -> RunResult` con `first_turn: TurnResult | None` (M0 §2.8 ya lo modela); se corrige m04 §2 | 7 |
| C6 | `TurnResult.trace_id` (obligatorio): nadie define de dónde sale | `TraceIds.current(turn_id) -> str`, por defecto `turn_id`; M11 lo reemplaza con el `trace_id` OTel | 2 |
| C7 | Interrupción `start_flow` con flow activo: ¿qué pasa con el flow interrumpido? ("se antepone", T-M4-03) | Va a `pending_intents` (se ofrece después; reinicia desde su entrada) | 12 |
| C8 | `cancel`: ¿cierra el flow o el run? No hay plantilla de cancelación en `EngineTemplates`; `RunClosedPayload.closed_by` no tiene valor `cancel` | Cierra solo el flow (`active_flow=None`, `awaiting=none`), run abierto, sin mensaje | 12 |
| C9 | Tras la recuperación (paso 5), ¿el mensaje del turno se procesa? | Se prepende el resultado de la recuperación; si el flow queda esperando al usuario, el mensaje se procesa normalmente; si termina, el turno acaba ahí | 8 |
| C10 | `response_emitted` de las plantillas del motor: `EVENT_EMITTERS` solo permite M8, m04 §3.5 dice que M4 rellena `transcript_fp` | M4 **no** emite `response_emitted`; solo rellena `transcript_fp` en los que traiga el turno. Si el usuario quiere que M4 los emita, es cambio de M0 (regenerar `contracts/`) | 15 |
| C11 | `run_closed.closed_by` para `end(abstained)` / `end(clarify_exhausted)` que cierra M4 | `"flow"` | 13 |
| C12 | `expiry_evaluated` en el paso 4 de cada turno (m04 §3.6 solo lo pide en el barrido) | Se emite en cada evaluación (con `expired` true/false): el replay lee los instantes de ahí (m11 §3.4) | 9 |
| C13 | `turn_count` en `start_run` | `turn_count = 1` en `start_run`; `handle_turn` incrementa | 7 |
| C14 | Caveat M3 Abierto (b): `yes` por botón con token rotado/vencido devuelve `unclear` | M4 suma `repair_turns_used` solo si `node_attempts` del `confirm` creció (m03 §3.3); T-M4-05 se cumple porque el botón no pasa por Understand | 13 |
| C15 | TTL del lease de turno | `TurnConfig.lease_ttl = 60 s` (> `max_wall_ms_per_turn` = 8 s) | 2 |

## Restricciones globales

- Python `>=3.12,<3.13`. Sin colas, Redis ni vector DB (ADR 0001).
- `agent_core.turn` solo importa `agent_core.domain`, `agent_core.ports` y las interfaces públicas (`__init__.py`) de `interpreter`, `actions`, `decision`, `guards`, `handoff`, `audit`. Nunca `agent_core.<mod>.<archivo>`. Lo verifica `uv run lint-imports`.
- Nunca `datetime.now()`, `time.time()`, `uuid4()`, `random`, `secrets`, `time.monotonic*`: el instante sale de `Clock.now()`, las duraciones de `Clock.monotonic_ns()` y todo ID de `IdSource.new_id` (lo verifica `ruff`, TID251).
- Determinismo: mismos puertos y mismo `Clock` ⇒ mismos eventos. Los campos `MEASURED_FIELDS` (`turn_completed.duration_ms`/`stages`) no participan en ninguna decisión.
- Cifras en `Decimal`, nunca `float`. Todo JSON entra con `agent_core.domain.loads` y sale con `dumps`; canonización con `canonical_bytes`.
- **PII:** el texto crudo del usuario nunca llega a guardas, Understand, transcript, eventos, logs ni outbox: solo `runtime.model_text(text)`. Los eventos de M4 no llevan texto. Los mensajes de `TurnResult` salen de `runtime.render`.
- Fixtures y pruebas solo con datos sintéticos (`example.test`, `tx-demo-1`, documentos inventados). Nunca datos reales del dataset ni credenciales del diccionario de datos.
- M4 nunca crea ni commitea la UoW de otro módulo; `M3` abre las suyas con `uow_factory`. `run_closed` lo emite **solo** M4.
- No tocar otros módulos (ver "Trabajo en paralelo"). Si hace falta algo que no está en una interfaz pública: detente y pregunta.
- Comandos: `uv run pytest tests/m04`, `uv run pytest`, `uv run ruff check .`, `uv run mypy`, `uv run lint-imports`, `uv run agentcore contracts --check`.
- `ruff`: `line-length = 110`. `I001`/`RUF022` se corrigen con `uv run ruff check --fix`; el resto a mano.
- Commits: español, prefijo `feat(m4):` / `test(m4):` / `docs(m4):`, **`git add` con rutas explícitas (nunca `-A`)** porque otros agentes commitean en la misma rama, y cierre con:

```
Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_014UPwyj3E9DZXR4mMR19Cof
```

## Decisiones de este plan (van al spec en la Task 1)

1. **Constructor.** El spec lista `Interpreter`; M2 es un módulo de funciones. Constructor real (solo nombre): `uow_factory, registry, clock, ids, guards, understand: UnderstandPort, actions: ActionManager, handoff: HandoffService, recorder: TurnRecorderPort, chain: EventChain, audit: AuditSink, runtimes: RuntimeFactory, trace: TraceIds, config: TurnConfig`. `GuardService` se tipa con un `Protocol` estructural (`GuardsPort`).
2. **Puertos locales** en vez de importar tipos de M5/M11 que no existen: `UnderstandRequest/UnderstandOutcome`, `TurnRecorderPort.record_turn(run_id, turn_id, user_msg_model, final_model, rejected)` (M11 spec no trae `turn_id`; `TranscriptEntry` sí lo exige), `EventChain.append(uow, run_id, events)` (encadena **y** persiste vía `uow.append_events`). Los adaptadores a los tipos reales se escriben en Fase B.
3. **`above_threshold`:** la marca por campo viene de M5 como `above_threshold` (m05), no `below_threshold`/`p_cal` (m04 §3.2). Un campo bajo umbral es `not above_threshold.get(campo, False)`. `command` siempre se evalúa; `flow` solo con `start_flow`; `interrupt` solo con `interrupt`.
4. **`turn_started` abre el turno.** Se crea con la salida de M6 (o `guards=None` en salidas tempranas: release revocada, abandono) y se coloca **primero** en el buffer de eventos del turno; cualquier vuelco previo (M3) lo materializa con `guards=None`.
5. **Orden de eventos de un turno:** `turn_started`, `expiry_evaluated`, [recuperación], `action_cancelled` (tokens vencidos), `injection_flagged`, `command_emitted` (+ `decision_made` de M5), [manejadores/M2/M3], `escalated`/`run_closed`, `turn_completed` (último, m04 §3.7).
6. **Un solo `EventRecorder` para M2/M3:** `TurnEventSink.record(uow, state, events)` vuelca primero el buffer del turno y luego los eventos que llegan, todo por `EventChain.append`. M2 ya vuelca los suyos pendientes antes (`flushing_record`).
7. **`events_so_far` de M10** = `audit.read(run_id)` + buffer pendiente + eventos de invalidación (m10 §11.2).
8. **Idempotencia en dos tiempos:** `get_turn_result` antes del lease y otra vez después (cierra la carrera "el otro turno commiteó entre mi lectura y mi `acquire_turn`"). Un duplicado devuelve el resultado guardado aunque el run ya esté cerrado.
9. **Fallas:** una excepción (no una caída) dentro del turno libera el lease con una UoW propia (`release_turn` + `commit`) para que el reintento no espere al TTL. Una caída real no libera nada: el lease vence solo.
10. **Costo:** M4 llama `uow.add_usage(principal, Δcosto, now)` en la transacción del turno (M0 rev. 5; el spec de M4 lo omite). Δcosto = `budgets_used.run_cost` final − inicial + costo de Understand.
11. **`start_run`:** sin lease (el `run_id` es nuevo); `save_run(state, 0)`; la idempotencia por `Idempotency-Key` es de M9 (`get/put_run_idempotency`), no de M4.
12. **`sweep`** vive en `turn/sweep.py` (`Sweeper`) y solo necesita `uow_factory, registry, clock, ids, actions, chain`; así `agentcore sweep` no arrastra M5/M6/M10.
13. **Datos sintéticos de prueba:** un registro propio en `tests/m04/harness.py` (el registro `demo` de M1 tiene una interrupción vacía y un solo flow): agente `atencion` (plantillas del motor sin variables), flows `disputa` (prioridad 50) y `bloquear-tarjeta` (80), interrupción `fraude` (`escalate`, prioridad `critical`) y una interrupción `start_flow`.

## Trabajo en paralelo (M5/M8 en otros agentes, M11 asumido)

- **Rama:** la actual (`claude/m5-m8-m4-parallel-8z4wxx`). Si el usuario prefiere worktree, `git worktree add ../agent-core-m4 -b feat/m4-ciclo-del-turno`.
- **Archivos que M4 puede tocar:** `agent_core/turn/**`, `tests/m04/**`, `tests/integration/**` y `agent_core/adapters/postgres/**` (Fase C, tras C4), `docs/specs/motor/m04-ciclo-del-turno.md`, este plan, una línea en `docs/specs/motor/00-indice.md` (§10), `agent_core/cli.py` (solo el subcomando `sweep`).
- **Ediciones mínimas fuera del paquete, solo con aprobación (C4):** `pyproject.toml`, `uv.lock`, `.github/workflows/ci.yml`, `tests/contracts/test_uow_contract.py` (parámetro `backend` prometido en su docstring) y `tests/m03/harness.py` (`World` con `uow_factory` opcional, para `crash_then_recover` sobre Postgres; decisión de M3 del 2026-09-29).
- **No tocar:** `agent_core/decision/**` (M5), `agent_core/response/**` (M8), `agent_core/audit/**` (M11), `agent_core/domain/**`, `agent_core/ports/**`, `agent_core/views/**`, `.importlinter`, `contracts/**`, `testing/**`. Los dobles de M4 (`ScriptedUnderstand`, `InMemoryTurnRecorder`, `PlainChain`, `ScriptedGuards`, `FakeRuntime`) viven en `tests/m04/helpers.py` para no chocar con `testing/fakes/` (otro agente puede agregar dobles de M5/M11 ahí).
- Cuando exista `agent_core.decision`/`agent_core.audit`, sincroniza la rama y ejecuta la **Fase B** completa antes de cerrar.

## Mapa de archivos

| Archivo | Responsabilidad |
|---|---|
| `agent_core/turn/ports.py` | puertos locales: `UnderstandPort`, `TurnRecorderPort`, `EventChain`, `GuardsPort`, `RuntimeFactory`, `TurnRuntime`, `TraceIds` |
| `agent_core/turn/config.py` | `TurnConfig` (lease, lote de barrido) |
| `agent_core/turn/events.py` | `TurnEvents`: `run_started`, `turn_started`, `command_emitted`, `expiry_evaluated`, `turn_completed`, `run_closed` |
| `agent_core/turn/metering.py` | `StageMeter` (`Clock.monotonic_ns`) |
| `agent_core/turn/buffer.py` | `EventBuffer`, `TurnEventSink` |
| `agent_core/turn/frame.py` | `TurnFrame`, `Signals` |
| `agent_core/turn/templates.py` | plantillas del motor (C2) |
| `agent_core/turn/recovery.py` | paso 5 (`executing` → `verify`) |
| `agent_core/turn/handlers.py` | tabla de manejadores globales y resolución con `confirm` pendiente |
| `agent_core/turn/intents.py` | selección de flow, `pending_intents`, ofertas |
| `agent_core/turn/closing.py` | cerrar run, abandonar, escalar |
| `agent_core/turn/results.py` | `Stop → Awaiting`, `TurnResult`, contadores de reparación |
| `agent_core/turn/engine.py` | `TurnEngine` (orquestación de los 14 pasos) |
| `agent_core/turn/sweep.py` | `Sweeper`, `SweepReport` |
| `agent_core/turn/understand_adapter.py`, `recorder_adapter.py` | Fase B: adaptadores a M5/M11 reales |
| `agent_core/turn/__init__.py` | interfaz pública |
| `agent_core/adapters/postgres/{schema.sql,uow.py,counters.py}` | Fase C |
| `tests/m04/helpers.py`, `harness.py`, `test_*.py` | dobles, mundo y pruebas T-M4-01…17 + trazabilidad |
| `tests/integration/test_turn_postgres.py`, `conftest.py` | Fase C |

## Trazabilidad T-M4 → tarea

| Prueba | Tarea | Fase |
|---|---|---|
| T-M4-01 | 14 | A |
| T-M4-02, 04, 05 | 13 | A |
| T-M4-03 | 12 | A |
| T-M4-06 | 16 | A |
| T-M4-07 | 9 (bloqueada P2) y 19 (sweep) | A |
| T-M4-08 | 12 y 16 | A |
| T-M4-09 | 8 | A |
| T-M4-10, 11 | 8 | A |
| T-M4-12 | 10 | A |
| T-M4-13 | 11 | A |
| T-M4-14 | 14 (solo `affirm`; `deny` bloqueada P1) | A |
| T-M4-15 | 13 | A |
| T-M4-16, 17 | 18 | A |

## Fases

- **Fase A (avanza ya, con dobles):** Tasks 1–20 (más 14b cuando P1 esté resuelta). Solo M5 (Understand) y M11 (recorder/cadena) son dobles locales; M2, M3, M6, M10 son los reales.
- **Fase B (depende de M5 y M11 reales):** Tasks 21–23. No cambian el pipeline: reemplazan dobles por adaptadores y repiten la suite.
- **Fase C (depende de C4 y de Docker):** Tasks 24–31.

---

## Fase A

### Task 1: Base, spec rev. 2 y ronda de preguntas

**Files:**
- Modify: `docs/specs/motor/m04-ciclo-del-turno.md`, `docs/specs/motor/00-indice.md` (§10)
- Create: `tests/m04/__init__.py`

**Interfaces:** Consumes: nada. Produces: el spec rev. 2 con la lista de decisiones y las respuestas a C1–C15; la suite base en verde.

- [ ] **Step 1: Verificar la base**

Run: `uv run pytest -q && uv run lint-imports && uv run mypy && uv run ruff check . && uv run agentcore contracts --check`
Expected: todo pasa. Si falla en la rama, detente y repórtalo (no es parte de este plan).

- [ ] **Step 2: Confirmar el estado de M5 y M11**

Run: `git log --oneline -5 && ls agent_core/decision agent_core/audit && grep -n "class UnderstandService\|class TurnRecorder\|class AuditLog" -r agent_core/decision agent_core/audit`
Expected: si `grep` no encuentra nada, M5/M11 no existen aún: Fase A con dobles; Fase B queda pendiente. Si existen, lee sus interfaces reales y anota las diferencias con "Interfaces esperadas" (Task 21/22) **antes** de escribir `ports.py`.

- [ ] **Step 3: Crear el paquete de pruebas**

```bash
mkdir -p tests/m04 && : > tests/m04/__init__.py
```

- [ ] **Step 4: Ronda de preguntas al usuario (P1, P2 y C1–C15)**

Presenta P1 y P2 con sus preguntas literales y la tabla C1–C15 con los defaults. Espera respuesta. **No avances a la Task 2 sin C1, C2, C6 y C15**; el resto se pide cuando llegues a la tarea afectada si el usuario prefiere responder por partes.

- [ ] **Step 5: Spec rev. 2**

En `m04-ciclo-del-turno.md`: cambia `- Estado: borrador · Fase 2` por `- Estado: rev. 2 (2026-09-29) · Fase 2`; en §2 reemplaza "Dependencias por constructor" por la lista de la Decisión 1; en §3.6 cambia `uow.list_inactive(now)` por `uow.list_inactive(now, limit)` (lote); agrega §12 "Decisiones de la rev. 2" con las decisiones 1–13 de este plan y las respuestas a C1–C15. **No** edites §11 (Abiertos) salvo que el usuario responda P1/P2.

- [ ] **Step 6: Commit**

```bash
git add docs/specs/motor/m04-ciclo-del-turno.md docs/specs/motor/00-indice.md tests/m04/__init__.py docs/superpowers/plans/2026-09-29-m4-ciclo-del-turno.md
git commit -m "docs(m4): spec rev. 2 (puertos locales, decisiones) y plan de implementación

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_014UPwyj3E9DZXR4mMR19Cof"
```

---

### Task 2: Puertos locales, configuración e interfaz pública

**Files:**
- Create: `agent_core/turn/ports.py`, `agent_core/turn/config.py`
- Modify: `agent_core/turn/__init__.py`
- Test: `tests/m04/test_public_api.py`

**Interfaces:** Consumes: `interpreter.StepContext`, `guards.GuardResult`, `domain` (`EncryptedBlob`, `TranscriptRef`, `RejectedDraft`, `Command`). Produces: los puertos que usan todas las tareas siguientes.

- [ ] **Step 1: Escribir la prueba (falla)**

```python
# tests/m04/test_public_api.py
def test_interfaz_publica_de_m4() -> None:
    import agent_core.turn as turn

    for name in ("TurnEngine", "TurnConfig", "SweepReport", "Sweeper", "UnderstandPort", "UnderstandRequest",
                 "UnderstandOutcome", "TurnRecorderPort", "EventChain", "GuardsPort", "RuntimeFactory",
                 "TurnRuntime", "TraceIds"):
        assert hasattr(turn, name), name
    assert set(turn.__all__) >= {"TurnEngine", "Sweeper"}


def test_turn_config_por_defecto() -> None:
    from datetime import timedelta

    from agent_core.turn import TurnConfig

    cfg = TurnConfig()
    assert cfg.lease_ttl == timedelta(seconds=60)
    assert cfg.sweep_batch == 100
```

- [ ] **Step 2: Ver que falla**

Run: `uv run pytest tests/m04/test_public_api.py -v`
Expected: FAIL (`AttributeError: module 'agent_core.turn' has no attribute 'TurnEngine'`).

- [ ] **Step 3: `config.py`**

```python
"""Configuración de M4 (decisión C15)."""
from dataclasses import dataclass
from datetime import timedelta


@dataclass(frozen=True)
class TurnConfig:
    lease_ttl: timedelta = timedelta(seconds=60)  # > max_wall_ms_per_turn
    sweep_batch: int = 100
```

- [ ] **Step 4: `ports.py`** (interfaces esperadas de M5/M11 marcadas)

```python
"""Puertos locales de M4 (patrón D1 de M2). Los adaptadores a M5/M11 reales van en Fase B.

[POR VERIFICAR] `UnderstandPort` reproduce m05 §2 (`UnderstandService.run`) y `TurnRecorderPort`/`EventChain`
reproducen m11 §2 (`TurnRecorder.record_turn`, `AuditLog.append`); se contrastan con el código cuando exista."""
from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Protocol

from agent_core.domain import (
    Agent, Command, EncryptedBlob, EngineEvent, JsonValue, Locale, Message, NodeId, OnBehalfOf, Principal,
    RejectedDraft, Release, RunState, TranscriptRef,
)
from agent_core.guards import GuardResult
from agent_core.interpreter import StepContext
from agent_core.ports import UnitOfWork


@dataclass(frozen=True)
class UnderstandRequest:
    text_model: str          # vista `model` (C1)
    state: RunState
    release: Release
    agent: Agent
    locale: Locale
    awaiting_confirmation: bool   # hay un confirm pendiente (m05 §3.2)
    current_node: NodeId | None


@dataclass(frozen=True)
class UnderstandOutcome:
    command: Command
    flow: str | None = None
    interrupt: str | None = None
    additional_flows: list[str] = field(default_factory=list)
    slots: dict[str, JsonValue] = field(default_factory=dict)
    above_threshold: dict[str, bool] = field(default_factory=dict)   # solo campos calibrados
    decision_id: str | None = None
    events: list[EngineEvent] = field(default_factory=list)          # `decision_made`, lo emite M5
    cost_usd: Decimal = Decimal("0")


class UnderstandPort(Protocol):
    def run(self, request: UnderstandRequest) -> UnderstandOutcome: ...


class TurnRecorderPort(Protocol):
    def record_turn(self, run_id: str, turn_id: str, user_msg_model: str, final_model: str,
                    rejected: list[RejectedDraft]) -> list[TranscriptRef]:
        """Orden: entrada del usuario, respuesta final, borradores rechazados."""
        ...


class EventChain(Protocol):
    def append(self, uow: UnitOfWork, run_id: str, events: list[EngineEvent]) -> None:
        """Encadena (seq, prev_hash, hash) y persiste con `uow.append_events`."""
        ...


class GuardsPort(Protocol):
    """Forma de `guards.GuardService.run` (verificada)."""
    def run(self, text_model_view: str, state: RunState, agent: Agent, release: Release,
            request_lang: str | None, first_turn: bool, *,
            turn_id: str | None = None) -> tuple[GuardResult, list[EngineEvent]]: ...


class TurnRuntime(Protocol):
    step: StepContext                     # base: M4 la ajusta por turno con `dataclasses.replace`

    def model_text(self, text: str) -> str: ...               # M7: PII del usuario → tokens del run
    def render(self, message: Message) -> Message: ...        # M7: tokens → valores para el principal
    def sealed_token_map(self) -> EncryptedBlob | None: ...   # M7: vault.seal() si cambió


class RuntimeFactory(Protocol):
    def open(self, state: RunState, principal: Principal, on_behalf_of: OnBehalfOf | None) -> TurnRuntime: ...


class TraceIds(Protocol):
    def current(self, turn_id: str) -> str: ...


TraceFn = Callable[[str], str]
```

- [ ] **Step 5: `__init__.py`** con `__all__` (ordenado por `ruff --fix`); exporta también `TurnEngine`, `Sweeper`, `SweepReport` (por ahora stubs mínimos `class TurnEngine: ...` en `engine.py` y `sweep.py` con `# reemplazado en Task 8/19`; la prueba solo exige que existan).

- [ ] **Step 6: Verde y calidad**

Run: `uv run pytest tests/m04/test_public_api.py -v && uv run ruff check --fix . && uv run mypy && uv run lint-imports`
Expected: 2 passed; ruff/mypy limpios; `lint-imports` KEPT (incluye `M4 (turn) solo usa domain, ports y interpreter, actions, decision, guards, handoff, audit`).

- [ ] **Step 7: Commit** (`feat(m4): puertos locales y configuración`; rutas explícitas).

---

### Task 3: Dobles y mundo de pruebas

**Files:** Create `tests/m04/helpers.py`, `tests/m04/harness.py`, `tests/m04/test_harness.py`.

**Interfaces:** Produces: `ScriptedUnderstand` (`push(*outcomes)`, `calls`), `cmd(...)`, `ScriptedGuards` (`decision`, `advance`, `calls`), `InMemoryTurnRecorder` (`calls`, `advance`), `PlainChain`, `FakeRuntime` (`model_text = "[model]" + t`, `render`, `sealed_token_map`), `CommitCounter` (envuelve `uow_factory`), y `World` con `engine`, `turn(text)`, `turn_confirm(token, answer)`, `open_run(**over)`, `saved()`, `events()`, `outbox()`.

- [ ] **Step 1: Prueba del propio arnés (falla)**

```python
# tests/m04/test_harness.py
from tests.m04.harness import World


def test_el_mundo_construye_el_motor_y_un_run_abierto() -> None:
    w = World()
    state = w.open_run()
    assert state.status == "open" and state.active_flow is None
    assert w.engine is not None
```

- [ ] **Step 2: `helpers.py`**

```python
"""Dobles de M4 (solo datos sintéticos). `FakeRuntime.model_text` marca el texto para detectar fugas de PII."""
from collections import deque
from dataclasses import dataclass, field, replace
from datetime import timedelta
from decimal import Decimal

from agent_core.domain import Command, EncryptedBlob, EngineEvent, Message, RejectedDraft, TranscriptRef, Fingerprint
from agent_core.guards import GuardResult, InjectionResult, LangDecision
from agent_core.interpreter import StepContext
from agent_core.turn import UnderstandOutcome, UnderstandRequest
from testing.fakes.clock import FakeClock


def cmd(command: str, *, flow: str | None = None, interrupt: str | None = None, additional: tuple[str, ...] = (),
        slots: dict | None = None, above: dict[str, bool] | None = None, cost: str = "0") -> UnderstandOutcome:
    """Comando sintético; por defecto todos los campos calibrados presentes superan el umbral."""
    fields = {"command": True, **({"flow": True} if flow else {}), **({"interrupt": True} if interrupt else {})}
    return UnderstandOutcome(command=Command(command), flow=flow, interrupt=interrupt,
                             additional_flows=list(additional), slots=slots or {},
                             above_threshold={**fields, **(above or {})}, decision_id="decision-x",
                             cost_usd=Decimal(cost))


class ScriptedUnderstand:
    def __init__(self, clock: FakeClock, advance: timedelta = timedelta(0)) -> None:
        self._script: deque[UnderstandOutcome] = deque()
        self.calls: list[UnderstandRequest] = []
        self.clock, self.advance = clock, advance

    def push(self, *outcomes: UnderstandOutcome) -> None:
        self._script.extend(outcomes)

    def run(self, request: UnderstandRequest) -> UnderstandOutcome:
        self.calls.append(request)
        self.clock.advance(self.advance)
        if not self._script:
            raise AssertionError("ScriptedUnderstand sin resultado guionado")
        return self._script.popleft()


def kept(locale: str = "es") -> GuardResult:
    return GuardResult(lang=LangDecision(decision="kept", locale=locale, locale_prior=locale, letters=20,
                                         top2=[(locale, 0.99)], detector="lingua@2.1.1"),
                       size_ok=True, injection=InjectionResult(flagged=False, ruleset="none"))


class ScriptedGuards:
    """Devuelve `result` (mutable por el test) y registra el texto recibido; `advance` mueve el reloj."""
    def __init__(self, clock: FakeClock, advance: timedelta = timedelta(0)) -> None:
        self.result, self.events = kept(), []
        self.calls: list[str] = []
        self.clock, self.advance = clock, advance

    def run(self, text_model_view, state, agent, release, request_lang, first_turn, *, turn_id=None):
        self.calls.append(text_model_view)
        self.clock.advance(self.advance)
        return self.result, list(self.events)


class InMemoryTurnRecorder:
    def __init__(self, clock: FakeClock, advance: timedelta = timedelta(0)) -> None:
        self.calls: list[tuple[str, str, str, str, list[RejectedDraft]]] = []
        self.clock, self.advance = clock, advance

    def record_turn(self, run_id, turn_id, user_msg_model, final_model, rejected):
        self.clock.advance(self.advance)
        self.calls.append((run_id, turn_id, user_msg_model, final_model, rejected))
        fp = Fingerprint(alg="HMAC-SHA256", kid="fp-1", value=f"fp{len(self.calls)}")
        return [TranscriptRef(entry_id=f"entry-{len(self.calls)}-{i}", fingerprint=fp) for i in range(2 + len(rejected))]


class PlainChain:
    """Sin hash: solo persiste por la UoW (fase A, sin M11)."""
    def append(self, uow, run_id, events) -> None:
        uow.append_events(run_id, events)


class FakeRuntime:
    """C1: `step` lo arma el `World` con M2/M3 reales; `model_text`/`render` son marcas visibles."""
    def __init__(self, step: StepContext) -> None:
        self.step = step
    def model_text(self, text: str) -> str:
        return f"[model]{text}"
    def render(self, message: Message) -> Message:
        return message.model_copy(update={"text": message.text.replace("[model]", "")})
    def sealed_token_map(self) -> EncryptedBlob | None:
        return None


class CommitCounter:
    """Envuelve una `UnitOfWorkFactory`: cuenta commits por UoW (invariante "una transacción por turno")."""
    def __init__(self, base) -> None:
        self.base, self.commits = base, 0
    def __call__(self):
        inner = self.base()
        outer = self
        class _U:
            def __enter__(s): inner.__enter__(); return s
            def __exit__(s, *a): return inner.__exit__(*a)
            def __getattr__(s, n): return getattr(inner, n)
            def commit(s): outer.commits += 1; inner.commit()
        return _U()
```

- [ ] **Step 3: `harness.py`** — `World` (dataclass) que en `__post_init__`:
  1. crea `FakeClock`, `FakeIds`, `InMemoryStore`, `InMemoryRegistry`, `FakeToolExecutor`, `ScriptedDecision`, `ScriptedResponder`, `ActionManager(ids, clock)`, `InMemoryAuditSink(store)`, `InMemoryOutbox(store)`;
  2. registra entidades: agente `atencion` (copiar el dict de `tests/m02/harness.py::AGENT` con `entry_flow: "disputa@1"` y plantillas `t/aclarar`, `t/abstencion`, `t/traspaso`, `t/acuse`, `t/oferta`, `t/idioma_no_soportado`, `t/mensaje_largo` **sin variables**), flows `disputa` (prioridad 50: `pedir` collect del slot `descripcion` → `confirmar` (`max_attempts: 5`) → `radicar` (write) → `verificar` → `fin` end `resolved`; cuerpos de nodos copiados de `tests/m03/harness.py::_CONFIRM/WRITE_NODE/VERIFY_NODE` y de `tests/m02/test_collect.py`) y `bloquear-tarjeta` (prioridad 80: `pedir_tarjeta` collect → `fin` end), tools (`radicar_pqr`, `obtener_pqr` como en `tests/m03/harness.py`), release con `interrupts=[fraude(escalate, queue "fraude", priority "critical", prioridad 100), reemplazo(start_flow bloquear-tarjeta, prioridad 90)]` y `language_detection` como en `tests/m02/harness.py::World.release`;
  3. `FakeRuntime` sobre un `StepContext` real (`views=ViewService(...)`, `vault=TokenVault(...)` como `tests/m02/harness.py::World.ctx`), y un `HandoffService` **real** con `HandoffAuthz` copiado de `tests/m10/helpers.py`;
  4. construye `TurnEngine(...)` con `chain=PlainChain()`, `guards=ScriptedGuards`, `understand=ScriptedUnderstand`, `recorder=InMemoryTurnRecorder`.
  Métodos: `open_run(**over) -> RunState` (guarda un `run_state(release=RELEASE_ID, agent="atencion@1.0.0", session_id="session-0001", ...)` sin flow activo), `turn(text, *, client_turn_id=None) -> TurnResult`, `turn_confirm(token, answer)`, `saved()`, `events()` (vía `InMemoryAuditSink.read`), `outbox()`, `at_confirm()` (lleva el run hasta `confirmar` con dos turnos: `start_flow disputa` y `continue` con slot) y `clock`/`ids`/`store` públicos.

- [ ] **Step 4: Verde** — el `World` no puede construirse aún (falta `TurnEngine`): deja `test_harness.py` con `pytest.importorskip` **no**; en su lugar corre después de la Task 8 y aquí solo verifica `uv run mypy tests/m04/helpers.py`.
Run: `uv run mypy && uv run ruff check tests/m04`
Expected: limpio.

- [ ] **Step 5: Commit** (`test(m4): dobles y mundo de pruebas`).

---

### Task 4: Fábrica de eventos de M4

**Files:** Create `agent_core/turn/events.py`; Test `tests/m04/test_events.py`.

**Interfaces:** Consumes `IdSource`, `Clock`, payloads de M0 (`RunStartedPayload`, `TurnStartedPayload`, `CommandEmittedPayload`, `ExpiryEvaluatedPayload`, `TurnCompletedPayload`, `RunClosedPayload`, `TurnStages`). Produces `TurnEvents`.

- [ ] **Step 1: Pruebas (fallan)**

```python
# tests/m04/test_events.py
from datetime import timedelta

from agent_core.domain import Awaiting, EVENT_EMITTERS, Outcome, TurnStages
from agent_core.turn.events import TurnEvents
from testing.builders import run_state
from testing.fakes.clock import FakeClock
from testing.fakes.ids import FakeIds


def make() -> tuple[TurnEvents, FakeClock]:
    clock = FakeClock()
    return TurnEvents(FakeIds(), clock), clock


def test_todos_los_eventos_de_m4_estan_permitidos_a_m4() -> None:
    assert {"run_started", "turn_started", "command_emitted", "expiry_evaluated", "turn_completed",
            "run_closed"} <= {t for t, who in EVENT_EMITTERS.items() if "M4" in who}


def test_turn_started_usa_el_instante_del_clock_y_sin_guardas() -> None:
    ev, clock = make()
    event = ev.turn_started(run_state(), "turn-1", "client-1", guards=None)
    assert event.ts == clock.now() and event.payload.guards is None and event.turn_id == "turn-1"


def test_turn_completed_lleva_stages_y_awaiting() -> None:
    ev, _ = make()
    event = ev.turn_completed(run_state(), "turn-1", "turn", "client-1", duration_ms=87,
                              stages=TurnStages(guards_ms=2, understand_ms=30, flow_ms=50, response_ms=5),
                              degraded=False, awaiting=Awaiting.confirmation)
    assert event.payload.duration_ms == 87 and event.payload.stages.understand_ms == 30


def test_expiry_evaluated_registra_instante_y_ttl() -> None:
    ev, clock = make()
    state = run_state()
    event = ev.expiry_evaluated(state, "turn-1", clock.now(), timedelta(minutes=30), expired=False)
    assert event.payload.ttl == timedelta(minutes=30) and event.payload.expired is False


def test_run_closed_por_escalamiento() -> None:
    ev, _ = make()
    event = ev.run_closed(run_state(), "turn-1", Outcome.escalated, "escalation")
    assert event.payload.closed_by == "escalation"
```

- [ ] **Step 2:** `uv run pytest tests/m04/test_events.py -v` → FAIL (`ModuleNotFoundError: agent_core.turn.events`).
- [ ] **Step 3: Implementar `TurnEvents`**: un método por evento; base común `_base(state, turn_id)` = `dict(event_id=ids.new_id(IdKind.event), run_id=state.run_id, turn_id=turn_id, session_id=state.session_id, release=state.release, ts=clock.now())`. `run_started(state, agent, reportable_attrs, principal_type, subject_kind)` sin texto ni secretos. `command_emitted(state, turn_id, outcome, source)` copia `command, flow, interrupt, additional_flows, above_threshold, decision_id` de `UnderstandOutcome`; `source="button"` construye el evento con `command=affirm|deny`, `above_threshold={}`, `decision_id=None` (el botón no pasa por Understand).
- [ ] **Step 4:** `uv run pytest tests/m04/test_events.py -v` → 5 passed; `uv run mypy && uv run ruff check .`.
- [ ] **Step 5: Commit** (`feat(m4): fábrica de eventos del turno`).

---

### Task 5: Medición por etapas, buffer de eventos y plantillas del motor

**Files:** Create `agent_core/turn/metering.py`, `buffer.py`, `templates.py`; Tests `tests/m04/test_metering.py`, `test_buffer.py`, `test_templates.py`.

**Interfaces:** `StageMeter(clock)`: `stage(name)` context manager acumula ns por etapa (`guards|understand|flow|response`), `duration_ms()`, `stages() -> TurnStages` (`None` si la etapa nunca corrió; ms enteros por división `// 1_000_000`; negativo → 0). `EventBuffer`: `add(*events)`, `reserve_turn_started()`/`fill(event)`, `peek()`, `drain()`. `TurnEventSink(buffer, chain, ensure_turn_started)`: implementa `EventRecorder` (decisión 6). `templates.render_engine(registry, ref, locale, agent) -> Message` (C2).

- [ ] **Step 1: Pruebas (fallan)**

```python
# tests/m04/test_metering.py
from datetime import timedelta

from agent_core.turn.metering import StageMeter
from testing.fakes.clock import FakeClock


def test_las_etapas_acumulan_y_las_no_corridas_son_none() -> None:
    clock = FakeClock()
    meter = StageMeter(clock)
    with meter.stage("guards"):
        clock.advance(timedelta(milliseconds=2))
    with meter.stage("flow"):
        clock.advance(timedelta(milliseconds=10))
    with meter.stage("flow"):
        clock.advance(timedelta(milliseconds=5))
    stages = meter.stages()
    assert (stages.guards_ms, stages.flow_ms) == (2, 15)
    assert stages.understand_ms is None and stages.response_ms is None


def test_duration_desde_la_recepcion() -> None:
    clock = FakeClock()
    meter = StageMeter(clock)
    clock.advance(timedelta(milliseconds=87))
    assert meter.duration_ms() == 87
```

```python
# tests/m04/test_buffer.py
def test_turn_started_reservado_va_primero_aunque_se_cree_despues() -> None:
    ...  # add(ev_a); reserve; add(ev_b); fill(ts) → peek() == [ts, ev_a, ev_b]
def test_vuelco_previo_materializa_turn_started_sin_guardas() -> None:
    ...  # TurnEventSink.record con la reserva vacía llama a ensure_turn_started() una vez y luego chain.append
def test_drain_vacia_y_no_repite_eventos() -> None: ...
```
```python
# tests/m04/test_templates.py
def test_plantilla_del_motor_en_el_locale_y_sin_variables() -> None: ...   # render_engine → Message(kind="template")
def test_plantilla_con_reads_es_error_de_configuracion() -> None: ...       # SchemaError
def test_locale_ausente_es_error_de_configuracion() -> None: ...
```
(Escribe los cuerpos completos siguiendo `tests/m02/test_resolve.py` para construir `Template` y `InMemoryRegistry`.)

- [ ] **Step 2:** correr → FAIL (módulos inexistentes).
- [ ] **Step 3: Implementar.** `StageMeter` usa **solo** `clock.monotonic_ns()` (regla dura 2). `render_engine` hace `registry.get(ref.require_exact(), Template)`; `template.reads` no vacío → `SchemaError("plantilla del motor con variables")`.
- [ ] **Step 4:** `uv run pytest tests/m04/test_metering.py tests/m04/test_buffer.py tests/m04/test_templates.py -v` → todo en verde; `uv run mypy && uv run ruff check . && uv run lint-imports`.
- [ ] **Step 5: Commit** (`feat(m4): medición por etapas, buffer de eventos y plantillas del motor`).

---

### Task 6: `TurnFrame`, resultados y contadores

**Files:** Create `agent_core/turn/frame.py`, `results.py`; Test `tests/m04/test_results.py`.

**Interfaces:** `TurnFrame` (dataclass mutable: `uow, state, turn_id, entry, client_turn_id, agent, release, runtime, meter, buffer, events (TurnEvents), messages, rejected, degraded, resume, confirmation, step_up, stop, text_model, cost_usd, closed`); `results.awaiting_for(stop, state) -> (Awaiting, NodeId | None)` (m02 §2: `awaiting_slot→slot`, `awaiting_confirmation→confirmation`, `awaiting_step_up→step_up`, `awaiting_user→input`, `terminal→none`; oferta pendiente → `input` con `pending_offer` y sin `awaiting_node_id`), `results.build_turn_result(frame, state, trace_id) -> TurnResult` (mensajes ya pasados por `runtime.render`), `results.repair_delta(...)`.

- [ ] **Step 1: Pruebas (fallan):**
```python
def test_stop_se_traduce_a_awaiting() -> None:
    from agent_core.domain import Awaiting
    from agent_core.interpreter import Stop
    from agent_core.turn.results import awaiting_for
    from testing.builders import run_state
    assert awaiting_for(Stop.awaiting_confirmation, run_state())[0] is Awaiting.confirmation
    assert awaiting_for(Stop.awaiting_user, run_state())[0] is Awaiting.input
    assert awaiting_for(Stop.terminal, run_state())[0] is Awaiting.none

def test_el_resultado_usa_render_y_no_expone_tokens() -> None: ...   # FakeRuntime.render quita "[model]"
def test_run_escalado_expone_handoff_ref_y_outcome() -> None: ...
```
- [ ] **Step 2–4:** correr (FAIL), implementar, correr (PASS), `mypy`/`ruff`/`lint-imports`.
- [ ] **Step 5: Commit** (`feat(m4): frame del turno y construcción del resultado`).

---

### Task 7: `start_run` (conversacional y task) — pasos "crear" y "avanzar"

**Files:** Create/Modify `agent_core/turn/engine.py` (nace aquí `TurnEngine` con `start_run`), `closing.py` (`close_run`); Test `tests/m04/test_start_run.py`.

**Bloqueada por C5 (tipo de retorno) y C13 (`turn_count`).**

**Interfaces:** `start_run(principal, on_behalf_of, run_input) -> RunResult` (según C5; con el default rechazado el spec queda en `TurnResult`).

- [ ] **Step 1: Pruebas (fallan)**

```python
# tests/m04/test_start_run.py
from agent_core.domain import AgentSelector, Outcome, RunInput
from tests.m04.harness import World


def run_input(**over) -> RunInput:
    return RunInput.model_validate({"agent": AgentSelector(id="atencion", alias="prod"),
                                    "idempotency_key": "key-1", **over})


def test_crea_el_run_con_release_fijada_y_locale_inicial() -> None:
    w = World()
    result = w.engine.start_run(w.principal, None, run_input(lang="pt"))
    state = w.store.runs[result.run_id]
    assert state.release == w.release_id and state.locale == "pt" and state.mode == "conversational"
    assert state.active_flow is not None and state.principal.model_dump().get("credential") is None


def test_lang_no_soportado_cae_al_default_locale() -> None: ...       # lang="fr" → "es"
def test_emite_run_started_con_reportable_attrs_y_turn_completed_start_run() -> None: ...
def test_solo_los_reportable_attrs_del_authz_viajan_en_run_started() -> None: ...
def test_task_avanza_hasta_terminal_y_devuelve_output() -> None: ...       # flow task del arnés
def test_task_que_espera_es_un_bug_y_escala_validation_failed() -> None: ...
def test_start_run_es_una_sola_transaccion_de_estado() -> None: ...        # CommitCounter == 1 (sin escrituras)
```
(Cuerpos: el flow task de prueba tiene `tool(read)` → `end completed` con `output_map`; el "bug" usa un flow task con `respond(await)` que se inyecta saltándose G0-16.)

- [ ] **Step 2:** `uv run pytest tests/m04/test_start_run.py -v` → FAIL.
- [ ] **Step 3: Implementar.** Orden: `release = registry.resolve_release(selector, principal)`; `agent = registry.get(release agent ref, Agent)`; `locale = lang if lang in agent.supported_locales else agent.default_locale`; construir `RunState` (dueños según índice §5: `run_id = ids.new_id(IdKind.run)`, `session_id` solo en conversacional, `created_at = last_activity_at = clock.now()`, `inactive_after = now + agent.inactivity_ttl` en conversacional, `turn_count=1`); `state = begin_turn(state, clock)`; `runtime = runtimes.open(...)`; `state = start_flow(state, registry.get(entry_flow exacto, Flow))`; `outcome = advance(state, ctx, NO_RESUME)`; en task: `stop != terminal` → `EscalationRequest("validation_failed", agent.default_target_queue, "normal")` por el camino de cierre de la Task 16 (si aún no existe, la prueba de "bug" se activa en esa tarea; anótalo con `pytest.mark.skip(reason="Task 16")` **solo hasta** entonces y quítalo en la Task 16).
- [ ] **Step 4:** correr las pruebas de la tarea (las de escalamiento quedan para la Task 16) → verde el resto; `mypy`/`ruff`/`lint-imports`.
- [ ] **Step 5: Commit** (`feat(m4): start_run`).

---

### Task 8: `handle_turn` — pasos 1–3 (dedupe, carga, lease, `410`, release revocada)

**Files:** Modify `agent_core/turn/engine.py`; Test `tests/m04/test_lifecycle.py`, `tests/m04/test_harness.py`.

**Interfaces:** `handle_turn(principal, on_behalf_of, turn) -> TurnResult` con: `find_run_by_session`, dedupe (antes y después del lease, decisión 8), `acquire_turn(run_id, turn_id, now, config.lease_ttl)` (`TurnInProgress` → `EngineError(turn_in_progress)`), `status != "open"` → `EngineError(run_closed)`, release revocada (`registry.release_status(state.release) == "revoked"`) → escalamiento `release_revoked` (Task 16 lo cierra; aquí se prueba el efecto observable), liberación del lease ante excepción (decisión 9), `runtimes.open` y `runtime.model_text(turn.text)` (C1).

- [ ] **Step 1: Pruebas (fallan)** — T-M4-09, T-M4-10, T-M4-11 y las de robustez:

```python
# tests/m04/test_lifecycle.py
import pytest
from datetime import timedelta

from agent_core.domain import EngineError, ProblemCode, TurnInput
from tests.m04.harness import World


def test_t_m4_10_turnos_concurrentes_dan_409() -> None:
    w = World(); w.open_run()
    with w.store.uow() as other:
        other.acquire_turn("run-0001", "turn-otro", w.clock.now(), timedelta(seconds=60))
    with pytest.raises(EngineError) as exc:
        w.turn("hola")
    assert exc.value.code is ProblemCode.turn_in_progress and exc.value.status == 409
    assert w.understand.calls == [] and w.guards.calls == []


def test_el_lease_vencido_no_bloquea() -> None: ...            # advance(61 s) → el turno corre


def test_t_m4_11_client_turn_id_repetido_no_reprocesa() -> None:
    w = World(); w.open_run()
    w.understand.push(cmd("out_of_scope"))
    first = w.turn("hola", client_turn_id="c-1")
    calls, events = len(w.understand.calls), len(w.events())
    again = w.turn("hola", client_turn_id="c-1")
    assert again == first and len(w.understand.calls) == calls and len(w.events()) == events


def test_duplicado_de_un_turno_que_cerro_el_run_devuelve_el_resultado_y_no_410() -> None: ...
def test_dedupe_tras_el_lease_cierra_la_carrera() -> None: ...   # resultado guardado justo antes de acquire_turn
def test_run_cerrado_da_410_y_libera_el_lease() -> None: ...
def test_t_m4_09_release_revocada_escala_sin_ejecutar_nodos() -> None:
    w = World(); w.open_run(active=True); w.registry.revoke(w.release_id)
    result = w.turn("hola")
    assert result.status == "escalated" and w.understand.calls == [] and w.guards.calls == []
    assert not [e for e in w.events() if e.type in ("node_entered", "command_emitted")]
    assert [e.payload.reason_code for e in w.events() if e.type == "escalated"] == ["release_revoked"]
def test_una_excepcion_libera_el_lease_para_el_reintento() -> None: ...   # RuntimeError en understand → lease libre
def test_una_caida_simulada_no_libera_el_lease() -> None: ...            # SimulatedCrash → sigue tomado hasta el TTL
def test_texto_crudo_nunca_llega_a_guardas_ni_a_understand() -> None: ...  # guards.calls[0] == "[model]hola"
```
(La prueba de release revocada se marca `skip("Task 16")` hasta que exista el cierre; las demás deben pasar aquí.)

- [ ] **Step 2:** FAIL. **Step 3:** implementar el esqueleto de `handle_turn`:

```python
def handle_turn(self, principal, on_behalf_of, turn):
    turn_id = self._ids.new_id(IdKind.turn)
    lease: str | None = None
    try:
        with self._uow_factory() as uow:
            found = uow.find_run_by_session(turn.session_id)
            if found is None:
                raise EngineError(ProblemCode.not_found, "sesión")
            if (cached := uow.get_turn_result(found.run_id, turn.client_turn_id)) is not None:
                return cached                                             # paso 1
            try:
                uow.acquire_turn(found.run_id, turn_id, self._clock.now(), self._config.lease_ttl)
            except TurnInProgress as exc:
                raise EngineError(ProblemCode.turn_in_progress, "turno en curso") from exc
            lease = found.run_id
            if (cached := uow.get_turn_result(found.run_id, turn.client_turn_id)) is not None:
                uow.release_turn(found.run_id, turn_id); uow.commit(); return cached
            state = uow.load_run(found.run_id) or found                  # estado fresco tras el lease
            if state.status != "open":
                raise EngineError(ProblemCode.run_closed, "run cerrado")
            result = self._process(uow, state, principal, on_behalf_of, turn, turn_id)   # pasos 3–14
            uow.commit()
            return result
    except Exception:
        if lease is not None:
            self._release_quietly(lease, turn_id)   # UoW propia: release_turn + commit; traga SimulatedCrash/errores
        raise
```
Nota: `SimulatedCrash` es `Exception` en el doble; `_release_quietly` **no** debe ejecutarse en la prueba "caída simulada" (el arnés la simula con `ProcessDied(BaseException)`). Si `_process` lanza `VersionConflict`, se propaga (M9 lo traduce a 500/reintento).
- [ ] **Step 4:** `uv run pytest tests/m04/test_lifecycle.py tests/m04/test_harness.py -v` → verde (salvo la marcada); `mypy`/`ruff`/`lint-imports`.
- [ ] **Step 5: Commit** (`feat(m4): handle_turn — deduplicación, lease de turno y run cerrado`).

---

### Task 9: Paso 4 — abandono al cargar un run vencido  ⛔ P2

**Files:** Modify `agent_core/turn/engine.py`, `closing.py`; Test `tests/m04/test_abandonment.py`.

**BLOQUEADA por P2** (qué recibe el turno). **Detente y pregunta antes de escribir código.** Lo que sí está acordado en el spec y se prueba/implementa una vez resuelta P2:

- Evaluación: `now − last_activity_at > agent.inactivity_ttl` (estricto). Emite `expiry_evaluated{now, last_activity_at, ttl, expired}` en **cada** turno (C12).
- Vencido: `actions.invalidate(state, InvalidationReason.abandoned, turn_id=…)`, `status="closed"`, `outcome=Outcome.abandoned`, `closed_at=now`, `inactive_after=None`, `awaiting=none`, evento `run_closed(closed_by="abandonment")`, todo en la misma transacción, con `turn_completed` (m04 §3.7).
- Respuesta de ese turno: **la decide P2** (propuesta del spec: `410 run_closed`; nota: si el error se lanza tras `save_run`, el commit debe ocurrir antes de lanzar, o el cierre se pierde).

- [ ] **Step 1 (tras P2):** Pruebas: (a) `expiry_evaluated` con `expired=False` en un turno normal; (b) run vencido → estado `closed/abandoned`, acciones `cancelled` con `cancel_reason=abandoned`, eventos `expiry_evaluated(expired=True)`, `action_cancelled`, `run_closed`, `turn_completed`; (c) el resultado según P2; (d) el instante usado es el del `Clock` (con `FakeClock`, límite exacto: `ttl` exacto **no** vence).
- [ ] **Step 2–5:** FAIL → implementar → PASS → `mypy`/`ruff`/`lint-imports` → commit `feat(m4): abandono al cargar un run vencido`.

---

### Task 10: Pasos 5–6 — recuperación y tokens vencidos (T-M4-12)

**Files:** Create `agent_core/turn/recovery.py`; Modify `engine.py`; Test `tests/m04/test_recovery.py`.

**Interfaces:** `recover(frame) -> None`: si `actions.pending_recovery(state)` no está vacío, localiza el `WriteToolNode` del flow activo cuyo `config.action_from == action.confirm_node_id` (`registry.get(active_flow.flow, Flow)` + `node_kind`), posiciona `active_flow.node_id = write_node.next["uncertain"]` y llama `advance(state, ctx, NO_RESUME)` **antes** de procesar el mensaje (C9). `expire_tokens(state, turn_id=…)` va justo después; sus eventos van al buffer.

- [ ] **Step 1: Pruebas (fallan)**

```python
# tests/m04/test_recovery.py
def test_t_m4_12_accion_executing_va_a_verify_antes_del_mensaje() -> None:
    w = World()
    w.open_run_crashed_in_write()      # acción en `executing`, flow en `radicar`, escritura ya aplicada en FakeTools
    writes_before = len(w.write_calls())
    w.understand.push(cmd("continue"))
    result = w.turn("¿ya quedó?")
    assert len(w.write_calls()) == writes_before                       # nunca se re-ejecuta (ADR 0007 §4)
    types = [e.type for e in w.events()]
    assert types.index("action_verified") < len(types)                 # readback antes que el mensaje
    assert w.saved().actions[0].state.value == "verified"

def test_recuperacion_que_termina_el_flow_acaba_el_turno() -> None: ...       # C9
def test_token_vencido_cancela_la_propuesta_y_emite_action_cancelled() -> None: ...
def test_sin_acciones_pendientes_no_hay_recuperacion() -> None: ...
```
`World.open_run_crashed_in_write()` reutiliza `tests/m03/scenarios.py::crash_then_recover` como referencia para dejar el run persistido tras un `ProcessDied` en `after_call` (mismo patrón, con el `World` de M4).
- [ ] **Step 2–5:** FAIL → implementar → PASS → calidad → commit (`feat(m4): recuperación de acciones y tokens vencidos`). Marca `state_version`: tras la recuperación (M3 no commitea `verify`) el estado avanza solo en el commit final.

---

### Task 11: Paso 7 — guardas (T-M4-13)

**Files:** Modify `engine.py`; Test `tests/m04/test_guards_step.py`, `tests/m04/test_real_guards.py`.

**Comportamiento:** `guards.run(runtime.model_text(text), state, agent, release, turn.lang, first_turn=False, turn_id=…)` dentro de `meter.stage("guards")`; eventos (`injection_flagged`) al buffer; `turn_started` con `result.to_output()`; `unsupported` → mensaje `unsupported_language` **en `agent.default_locale`**, sin Understand ni flow, `locale` sin cambio; `size_ok=False` → `input_too_large` (m06 §3.4) igual; `injection.flagged` → `frame.degraded = True` y `degraded_turns += [turn_count]`; `state.locale = result.lang.locale` cuando no es `unsupported`.

- [ ] **Step 1: Pruebas (fallan)**

```python
def test_t_m4_13_idioma_no_soportado_responde_plantilla_y_no_avanza() -> None:
    w = World(); w.open_run(active=True)
    before = w.saved().active_flow
    w.guards.result = unsupported("fr")            # helper: LangDecision(decision="unsupported", locale="es"...)
    result = w.turn("bonjour")
    assert result.messages[0].text == "Solo atiendo en español y portugués."   # texto de t/idioma_no_soportado (es)
    assert w.understand.calls == [] and w.saved().active_flow == before and result.locale == "es"
    assert [e.type for e in w.events()].count("turn_completed") == 1

def test_tamano_excedido_responde_input_too_large_sin_procesar() -> None: ...
def test_injection_marca_modo_degradado_y_lo_pasa_al_interprete() -> None: ...   # ctx.degraded True; degraded_turns
def test_injection_flagged_lo_construye_m6_y_lo_agrega_m4() -> None: ...
def test_locale_del_run_sigue_la_decision_de_m6() -> None: ...                    # switched → state.locale
def test_turn_started_lleva_la_salida_de_m6_y_el_client_turn_id() -> None: ...
```
`tests/m04/test_real_guards.py`: un caso con el `GuardService` **real** sobre el registro `demo` de M1 (`registry_from_directory(FIXTURE, "demo")` como `tests/m02/test_disputa_cargo.py`): texto español válido → `kept`; comprueba que `GuardService` satisface `GuardsPort` (`_conforms` con `TYPE_CHECKING`).
- [ ] **Step 2–5:** FAIL → implementar → PASS → calidad → commit (`feat(m4): guardas de entrada (M6) en el turno`).

---

### Task 12: Pasos 8–9 — Understand y manejadores globales sin `confirm` pendiente (T-M4-03, T-M4-08 parte)

**Files:** Create `agent_core/turn/handlers.py`, `closing.py` (`escalate`); Modify `engine.py`; Tests `tests/m04/test_understand_step.py`, `tests/m04/test_global_handlers.py`.

**Bloqueada por C3 (datos de `signal_policy`), C7 (flow interrumpido) y C8 (`cancel`).**

**Diseño (tabla de datos, m04 §9):**

```python
# handlers.py
@dataclass(frozen=True)
class GlobalHandler:
    name: str
    applies: Callable[[Signals], bool]
    run: Callable[[TurnFrame, Signals], HandlerResult]

GLOBAL_HANDLERS: tuple[GlobalHandler, ...] = (
    GlobalHandler("interrupt", _interrupt_applies, _run_interrupt),   # 1
    GlobalHandler("cancel", lambda s: s.is_(Command.cancel), _run_cancel),                       # 2
    GlobalHandler("handoff", lambda s: s.is_(Command.handoff), _run_handoff),                    # 3
    GlobalHandler("out_of_scope", lambda s: s.is_(Command.out_of_scope), _run_abstain),          # 4
    GlobalHandler("clarify", _clarify_applies, _run_clarify),                                    # 5
)
```
`Signals`: `outcome` (Understand), `hit: Interrupt | None` (interrupciones de `release.interrupts` por `priority` desc: dispara si `command == interrupt` con `above_threshold["command"]` **y** `above_threshold["interrupt"]` e `interrupt == id`, **o** `signal_policy` da `truthy(evaluate(policy.expr, data))` con `data` de C3), `below(field)`.
Acciones: `escalate` → `invalidate(interrupt)` + `escalate_run(frame, EscalationRequest(reason_code=f"interrupt:{id}", target_queue, priority))`; `start_flow` → `invalidate(interrupt)`, el flow activo (si lo hay) pasa a `pending_intents` (C7) y `start_flow(state, nuevo)`.

- [ ] **Step 1: Pruebas (fallan)**

```python
def test_t_m4_03_interrupcion_de_fraude_invalida_acciones_y_se_antepone() -> None:
    w = World(); w.at_confirm()                                    # acción `proposed`
    w.understand.push(cmd("interrupt", interrupt="fraude"))
    result = w.turn("me robaron la tarjeta")
    assert result.status == "escalated" and w.saved().actions[0].state.value == "cancelled"
    assert w.saved().actions[0].cancel_reason.value == "interrupt"  # invalidación antes de escalar
    assert [e.payload.reason_code for e in w.events() if e.type == "escalated"] == ["interrupt:fraude"]

def test_interrupcion_start_flow_reemplaza_el_flow_activo() -> None: ...           # C7
def test_interrupcion_por_signal_policy_sin_comando() -> None: ...                 # C3
def test_la_de_mayor_prioridad_gana() -> None: ...
def test_cancel_cierra_el_flow_e_invalida_acciones() -> None: ...                  # C8
def test_handoff_escala_customer_request_y_emite_handoff_created() -> None: ...    # T-M4-08 (1ª mitad)
def test_out_of_scope_responde_abstencion_y_cierra_abstained() -> None: ...
def test_command_emitted_se_registra_una_vez_por_turno_con_understand() -> None: ...
def test_understand_recibe_solo_el_texto_en_vista_model() -> None: ...             # "[model]…"
```
- [ ] **Step 2–5:** FAIL → implementar `escalate_run` (ver Task 16 para el cierre común; aquí solo la llamada) → PASS → calidad → commit (`feat(m4): Understand y manejadores globales`).

---

### Task 13: Manejadores con `confirm` pendiente, botón y clarify (T-M4-02, 04, 05, 15)

**Files:** Modify `handlers.py`, `engine.py`; Tests `tests/m04/test_confirm_pending.py`, `tests/m04/test_clarify.py`.

**Resolución (tabla, m04 §3.2 "Con `confirm` pendiente")** como datos: `PENDING_CONFIRM_KEEPS = {"interrupt", "cancel", "handoff"}`; el resto mapea a `Resume(kind="confirm_answer", value=…)`:

| Entrada | Resultado |
|---|---|
| botón `yes`/`no` (paso 8: sin Understand; `command_emitted(source="button")`) | `Resume("confirm_answer", answer, token=turn.confirm.token)`; nunca `unclear` |
| `affirm`/`deny` con `command` sobre umbral | `yes`/`no` |
| interrupción, `cancel`, `handoff` | igual que sin `confirm` |
| `start_flow` o `additional_flows` | a `pending_intents` con acuse + `unclear` |
| otro comando, `out_of_scope`, `clarify`, bajo umbral | `unclear` |

Contadores (C14): `repair_turns_used += 1` por `clarify` (con `clarifications_used += 1`) y por `unclear` **de texto** solo si `node_attempts[confirm]` creció; el botón no suma. Tope de `max_clarifications`: al superarlo, `end(clarify_exhausted)` (`closed_by="flow"`, C11) o `escalate(low_confidence)` según `agent.on_clarify_exhausted`.

- [ ] **Step 1: Pruebas (fallan)**

```python
def test_t_m4_02_intencion_nueva_en_confirm_es_pendiente_mas_unclear() -> None:
    w = World(); w.at_confirm()
    w.understand.push(cmd("start_flow", flow="bloquear-tarjeta"))
    result = w.turn("también bloquea mi tarjeta")
    assert [p.flow for p in w.saved().pending_intents] == ["bloquear-tarjeta"]
    assert w.saved().actions[0].state.value == "proposed"           # sigue proposed
    assert any(m.text == w.text("acuse") for m in result.messages)

@pytest.mark.parametrize("command, effect", [("cancel", "cancelled"), ("handoff", "escalated"), ("interrupt", "escalated")])
def test_t_m4_04_cancel_handoff_e_interrupcion_si_aplican_con_confirm(command, effect) -> None: ...

def test_t_m4_04_out_of_scope_da_unclear_y_no_cierra() -> None: ...       # run open, proposed, repair +1

def test_t_m4_05_boton_no_pasa_por_understand_y_no_da_unclear() -> None:
    w = World(); prompt = w.at_confirm().confirmation
    result = w.turn_confirm(prompt.token, "yes")
    assert w.understand.calls == [] and w.saved().repair_turns_used == 0
    assert [e.payload.source for e in w.events() if e.type == "command_emitted"] == ["button"]

def test_boton_no_cancela_la_accion() -> None: ...
def test_affirm_sobre_umbral_confirma_y_bajo_umbral_da_unclear() -> None: ...
def test_boton_con_token_rotado_no_suma_reparacion() -> None: ...           # C14
def test_t_m4_15_clarify_agotado_end_clarify_exhausted() -> None: ...
def test_t_m4_15_clarify_agotado_escalate_low_confidence() -> None: ...     # agente con on_clarify_exhausted="escalate"
def test_command_o_flow_bajo_umbral_pide_aclaracion_y_suma_clarifications_used() -> None: ...
```
- [ ] **Step 2–5:** FAIL → implementar → PASS → calidad → commit (`feat(m4): precedencia con confirm pendiente, botón y aclaraciones`).

---

### Task 14: Paso 10 — flow activo, intenciones pendientes y ofertas (T-M4-01, T-M4-14 parcial)  ⛔ P1 (solo `deny`)

**Files:** Create `agent_core/turn/intents.py`; Modify `engine.py`; Tests `tests/m04/test_intents.py`.

**Comportamiento (m04 §3.3, ADR 0004):**
- Sin flow activo y `start_flow`: se juntan `flow` + `additional_flows` (orden de mención = índice), se ordenan por `Flow.priority` desc y mención asc; arranca el primero con `interpreter.start_flow`; el resto a `pending_intents` (`PendingIntent(flow, priority, mention_order)`), más acuse (`pending_ack`) **después** de los mensajes del flow.
- Con flow activo: `start_flow`/`additional_flows` van a `pending_intents` (mismo orden) con acuse. `continue` → `Resume("slot_answer", texto)` si el nodo es `collect`.
- `additional_flows` sin umbral y `slots` de Understand entran como `claimed` (nunca hechos; M2 los lee como ausentes hasta validarse).
- Al terminar el flow con pendientes: no se cierra el run; `pending_offer = flow_id`, `awaiting=input`, plantilla `pending_offer`. **Solo arranca con `affirm`** (`start_flow` de la pendiente).
- `deny` ante la oferta: **P1** — no implementar.

- [ ] **Step 1: Pruebas (fallan)**

```python
def test_t_m4_01_arranca_la_de_mayor_prioridad_y_la_otra_queda_pendiente_con_acuse() -> None:
    w = World(); w.open_run()
    w.understand.push(cmd("start_flow", flow="disputa", additional=("bloquear-tarjeta",)))
    result = w.turn("bloquea mi tarjeta y disputa este cargo")
    state = w.saved()
    assert state.active_flow.flow.id == "bloquear-tarjeta"               # prioridad 80 > 50
    assert [(p.flow, p.priority, p.mention_order) for p in state.pending_intents] == [("disputa", 50, 0)]
    assert result.messages[-1].text == w.text("acuse")

def test_empate_de_prioridad_desempata_por_orden_de_mencion() -> None: ...
def test_con_flow_activo_la_nueva_intencion_va_a_pendientes() -> None: ...
def test_una_intencion_pendiente_no_invalida_la_accion_en_confirmacion() -> None: ...
def test_continue_reanuda_collect_con_slot_answer() -> None: ...
def test_los_slots_de_understand_entran_claimed() -> None: ...
def test_t_m4_14_al_terminar_el_flow_se_ofrece_la_pendiente_y_run_queda_esperando() -> None: ...   # awaiting=input, pending_offer
def test_t_m4_14_la_oferta_solo_arranca_con_affirm() -> None: ...      # cualquier otro comando no la arranca
```
La prueba `deny` **no se escribe** hasta responder P1.
- [ ] **Step 2–5:** FAIL → implementar → PASS → calidad → commit (`feat(m4): intenciones pendientes y ofertas (sin deny)`). Anota en el commit "P1 pendiente".

---

### Task 14b: Rama `deny` de la oferta  ⛔ P1

**BLOQUEADA por P1.** Detente y pregunta (a) qué pasa al descartar la última pendiente (cierre y `outcome` o run abierto), (b) qué pasa con respuestas que no son `affirm`/`deny`. Solo entonces: pruebas (descarta la actual y ofrece la siguiente; caso "última") → implementar en `intents.py` → actualizar spec (§3.3 y quitar el Abierto de §11 **con el texto del usuario**) → commit `feat(m4): deny ante la oferta de intención pendiente`.

---

### Task 15: Paso 13 — responder y registrar (transcript, `transcript_fp`)

**Files:** Modify `engine.py`; Test `tests/m04/test_record.py`.

**Comportamiento:** dentro de `meter.stage("response")`: `final_model` = mensajes del turno (vista `model`) unidos con `"\n\n"`; `refs = recorder.record_turn(run_id, turn_id, user_msg_model=frame.text_model, final_model, rejected=frame.rejected)`; el `Fingerprint` de la entrada del asistente (`refs[1]`) se copia con `model_copy` a `response_emitted.payload.transcript_fp` de **cada** `response_emitted` del turno sin huella, antes de pasar los eventos a la cadena (M0 §2.10). C10: M4 no emite `response_emitted`. Los mensajes de `TurnResult` pasan por `runtime.render`. Si `record_turn` lanza, el turno falla y se reintenta (m11 Abierto propuesto: no perder el texto en silencio).

- [ ] **Step 1: Pruebas (fallan)**

```python
def test_el_transcript_recibe_texto_en_vista_model_y_borradores_rechazados() -> None: ...
def test_transcript_fp_se_rellena_en_response_emitted_sin_mutar_los_demas_campos() -> None: ...
def test_los_mensajes_del_resultado_salen_renderizados_y_el_transcript_en_model() -> None: ...  # FakeRuntime.render
def test_si_el_recorder_falla_el_turno_falla_y_no_persiste_estado() -> None: ...
def test_turno_sin_response_emitted_no_falla() -> None: ...                       # plantillas (C10)
```
- [ ] **Step 2–5:** FAIL → implementar → PASS → calidad → commit (`feat(m4): registro del turno en el transcript`).

---

### Task 16: Pasos 11–12 y 14 — avanzar, cerrar, escalar y persistir en una transacción (T-M4-06, T-M4-08 completo, T-M4-09 y "bug de task")

**Files:** Modify `engine.py`, `closing.py`, `results.py`; Tests `tests/m04/test_closing.py`, `tests/m04/test_persist.py`.

**Avanzar (11):** `state = begin_turn(...)` antes de la guarda de presupuestos; `ctx = replace(runtime.step, locale=state.locale, degraded=frame.degraded, record=sink.record, turn_id=turn_id, release=…, agent=…)`; `outcome = advance(state, ctx, frame.resume)`; `state = outcome.state`; los eventos de `outcome.events` van al buffer (los de `execute_write` ya los persistió M3). El resultado de M2 fija `awaiting`/`awaiting_node_id` con `awaiting_for(outcome.stop, state)`; `confirmation`/`step_up` pasan al `TurnResult`.

**Cerrar (12):**
1. `outcome.escalation` → `escalate_run(frame, request)`: `actions.invalidate(state, InvalidationReason.escalated, turn_id=…)` → `handoff.escalate(state, request, audit.read(run_id) + buffer.peek() + inv_events, uow=uow, turn_id=…)` → `(closed, [escalated], outbox, msg)`; `uow.enqueue_outbox(outbox)`; mensaje `msg` al turno; evento `run_closed(outcome=escalated, closed_by="escalation")` (`"revocation"` si el motivo es `release_revoked`). **Nunca** `run_closed` sin pasar por aquí (M4 es el único emisor).
2. `outcome.end_outcome` (`resolved|abstained|cancelled|clarify_exhausted|completed|failed`) y sin pendientes → `close_run(status="closed", outcome, closed_at=now, inactive_after=None, awaiting=none)` + `run_closed(closed_by="flow")`; con pendientes → oferta (Task 14).
3. Task mode: `stop != terminal` (bug) → `EscalationRequest("validation_failed", default_target_queue, "normal")` por el mismo camino; `outcome.output` se devuelve solo por `RunResult.output` (M9/M7 lo proyecta).

**Reparación (m04 §3.2):** después de cada turno, `repair_turns_used` (aclaraciones + reintentos de `collect` (M2) + `unclear` de `confirm`); si `> agent.max_repair_turns_per_run` → `escalate_run(low_confidence)`.

**Persistir (14):** `state = state.model_copy(update={"turn_count": +1, "last_activity_at": now, "inactive_after": now + ttl (solo open), "token_map": runtime.sealed_token_map() or state.token_map, "awaiting": …})`; `saved = uow.save_run(state, expected_version=state.state_version)` (el estado ya trae la versión que dejó el último commit de M3); `chain.append(uow, run_id, buffer.drain() + [turn_completed])` (turn_completed **último**, m04 §3.7); `uow.add_usage(principal_key, Δcosto, now)` (decisión 10); `uow.put_turn_result(run_id, client_turn_id, result)`; `uow.release_turn(run_id, turn_id)`. El `commit()` lo hace `handle_turn`.

- [ ] **Step 1: Pruebas (fallan)**

```python
def test_t_m4_06_superar_max_repair_turns_escala_sumando_unclear_de_confirm() -> None:
    w = World(agent_over={"max_repair_turns_per_run": 2}); w.at_confirm()     # confirm con max_attempts 5
    for _ in range(2):
        w.understand.push(cmd("out_of_scope")); w.turn("no sé")                # unclear ×2 → repair 2
    assert w.saved().status == "open"
    w.understand.push(cmd("out_of_scope")); result = w.turn("no sé")           # 3 > 2
    assert result.status == "escalated"
    assert [e.payload.reason_code for e in w.events() if e.type == "escalated"] == ["low_confidence"]

def test_t_m4_08_escalate_emite_handoff_created_y_el_turno_siguiente_da_410() -> None:
    w = World(); w.open_run(active=True)
    w.understand.push(cmd("handoff")); w.turn("quiero un asesor")
    assert [m.type for m in w.outbox().pending(10)] == ["handoff_created"]
    assert w.saved().status == "escalated" and w.saved().outcome.value == "escalated"
    with pytest.raises(EngineError) as exc:
        w.turn("¿hola?")
    assert exc.value.status == 410

def test_t_m4_08_estado_escalated_evento_y_outbox_en_la_misma_transaccion() -> None: ...   # falla del outbox revierte todo
def test_run_closed_lo_emite_solo_m4_y_una_vez() -> None: ...
def test_end_del_flow_cierra_el_run_con_run_closed_flow() -> None: ...
def test_los_flows_task_que_esperan_escalan_validation_failed() -> None: ...              # quita el skip de la Task 7
def test_release_revocada_cierra_con_closed_by_revocation() -> None: ...                  # quita el skip de la Task 8
def test_un_turno_es_una_sola_transaccion_de_estado() -> None: ...                        # CommitCounter == 1 sin escrituras; 3 con una escritura (1 + 2 de M3)
def test_una_falla_a_mitad_del_turno_no_commitea_y_el_reintento_lo_rehace() -> None: ...  # SimulatedCrash en commit; mismo client_turn_id
def test_save_run_usa_la_version_que_dejo_m3() -> None: ...                               # turno con escritura, sin VersionConflict
def test_add_usage_suma_el_costo_del_turno_en_decimal() -> None: ...                      # Understand 0.001 + run_cost delta
def test_token_map_sellado_se_persiste_en_el_run() -> None: ...
def test_el_resultado_por_client_turn_id_se_guarda_en_la_misma_transaccion() -> None: ...
```
- [ ] **Step 2–5:** FAIL → implementar → PASS (incluye quitar los `skip` de las Tasks 7 y 8) → `uv run pytest tests/m04 -v` → calidad → commit (`feat(m4): avanzar, cerrar, escalar y persistir en una transacción`).

---

### Task 17: `turn_completed` y medición en todas las salidas (T-M4-16, T-M4-17)

**Files:** Modify `engine.py`; Test `tests/m04/test_turn_completed.py`.

**Reglas (m04 §3.7):** un `turn_completed` por `start_run` y por `handle_turn` que llega al paso 14, incluidas salidas tempranas (`unsupported`, release revocada, abandono); no lo emiten duplicados, `409`, `410` ni `sweep`. `duration_ms` = desde la recepción (antes del paso 2) hasta antes de persistir. `stages`: `guards_ms` (paso 7), `understand_ms` (paso 8; `None` si botón), `flow_ms` (pasos 5, 9–12; incluye tools), `response_ms` (paso 13). `awaiting` = el del run al terminar (`none` si cerró). `degraded`.

- [ ] **Step 1: Pruebas (fallan)**

```python
def test_t_m4_16_duration_y_stages_exactos_con_fakeclock() -> None:
    w = World(guards_advance=ms(2), understand_advance=ms(30), tool_advance=ms(50), recorder_advance=ms(5))
    w.open_run(active=True); w.understand.push(cmd("continue"))
    w.turn("hola")
    payload = [e for e in w.events() if e.type == "turn_completed"][-1].payload
    assert (payload.stages.guards_ms, payload.stages.understand_ms, payload.stages.flow_ms,
            payload.stages.response_ms) == (2, 30, 50, 5) and payload.duration_ms == 87

def test_t_m4_16_boton_deja_understand_ms_en_none() -> None: ...
def test_t_m4_17_duplicado_409_y_410_no_emiten_turn_completed() -> None: ...
def test_t_m4_17_idioma_unsupported_emite_uno_con_flow_ms_none() -> None: ...
def test_start_run_emite_turn_completed_entry_start_run() -> None: ...
def test_release_revocada_y_abandono_tambien_emiten_turn_completed() -> None: ...   # abandono: tras P2
def test_los_campos_de_medicion_no_influyen_en_las_decisiones() -> None: ...          # dos corridas con relojes distintos → mismos estados/eventos salvo MEASURED_FIELDS
```
(La prueba del abandono se añade cuando P2 esté resuelta.)
- [ ] **Step 2–5:** FAIL → implementar → PASS → calidad → commit (`feat(m4): turn_completed con medición por etapas`).

---

### Task 18: Determinismo, PII y trazabilidad completa

**Files:** Test `tests/m04/test_determinism.py`, `test_pii.py`, `test_traceability.py`.

- [ ] **Step 1: Pruebas**

```python
def test_mismos_puertos_y_reloj_producen_los_mismos_eventos() -> None:
    a, b = run_script(World()), run_script(World())         # mismo guion de 4 turnos (intención, slot, confirmar, botón)
    def norm(events):   # M0 §2.10: se ignoran event_id/seq/prev_hash/hash/ts y MEASURED_FIELDS
        return [e.model_dump(exclude={"event_id", "seq", "prev_hash", "hash", "ts"}) for e in scrub_measured(events)]
    assert norm(a) == norm(b)

def test_ningun_evento_ni_outbox_ni_resultado_contiene_el_texto_crudo() -> None: ...       # email sintético user@example.test
def test_los_eventos_de_m4_no_llevan_texto_de_usuario() -> None: ...
def test_no_hay_float_en_el_costo() -> None: ...                                            # tipos Decimal en add_usage
def test_todo_evento_valida_contra_el_esquema_de_m0() -> None: ...                          # TypeAdapter(AnyEvent)
def test_los_seis_eventos_de_m4_se_emiten_en_el_recorrido_completo() -> None: ...
def test_cobertura_t_m4_01_a_17() -> None:   # lee este archivo y tests/m04: cada ID T-M4-NN aparece en un nombre de prueba
    ...
```
- [ ] **Step 2–4:** FAIL → ajustar (si algo no es determinista, arréglalo en el módulo, no en la prueba) → PASS → `uv run pytest tests/m04 -v`.
- [ ] **Step 5: Commit** (`test(m4): determinismo, PII y trazabilidad T-M4-01…17`).

---

### Task 19: Barrido (`sweep`) y `agentcore sweep` (T-M4-07, no depende de P2)

**Files:** Create `agent_core/turn/sweep.py`; Modify `agent_core/turn/__init__.py`, `agent_core/cli.py`; Test `tests/m04/test_sweep.py`, `tests/m04/test_cli_sweep.py`.

**Comportamiento:** `Sweeper.sweep(now) -> SweepReport(evaluated, abandoned, skipped)`: en lotes, `uow.list_inactive(now, config.sweep_batch)`; por cada `run_id`, **una UoW propia**: `acquire_turn(run_id, turn_id, now, ttl)` (si `TurnInProgress` → `skipped`, el turno vivo gana), `load_run`, reevalúa (`inactive_after < now` y `status == "open"`), emite `expiry_evaluated{now, last_activity_at, ttl, expired}` (siempre), y si vence: `actions.invalidate(abandoned)`, `close_run(abandoned)`, `run_closed(closed_by="abandonment")`, `save_run`, cadena, `release_turn`, `commit`. Sin `turn_completed` (m04 §3.7). Repite hasta que un lote venga vacío o menor que el tamaño. `TurnEngine.sweep(now)` delega en `Sweeper`.

- [ ] **Step 1: Pruebas (fallan)**

```python
def test_t_m4_07_inactividad_cierra_abandoned_con_acciones_canceladas_y_expiry_evaluated() -> None:
    w = World(); w.at_confirm(); w.clock.advance(timedelta(minutes=31))
    report = w.engine.sweep(w.clock.now())
    state = w.saved()
    assert (state.status, state.outcome.value) == ("closed", "abandoned") and report.abandoned == 1
    assert state.actions[0].state.value == "cancelled" and state.actions[0].cancel_reason.value == "abandoned"
    types = [e.type for e in w.events()]
    assert {"expiry_evaluated", "action_cancelled", "run_closed"} <= set(types) and "turn_completed" not in types[-3:]

def test_un_run_activo_no_se_cierra_y_no_hay_expiry_expired() -> None: ...
def test_un_turno_en_curso_gana_al_barrido() -> None: ...            # lease tomado → skipped
def test_el_barrido_procesa_mas_de_un_lote() -> None: ...            # sweep_batch=2, 5 runs
def test_sweep_es_idempotente() -> None: ...                         # segunda pasada: nada
def test_solo_los_runs_open_con_inactive_after_vencido() -> None: ...
def test_agentcore_sweep_construye_solo_lo_necesario() -> None: ...  # CLI con un `--registry` de prueba y un doble de UoW
```
CLI: `agentcore sweep [--registry DIR] [--dsn DSN] [--once]`. Por ahora acepta un `Sweeper` inyectable para pruebas; el cableado real a Postgres se completa en la Task 31.
- [ ] **Step 2–5:** FAIL → implementar → PASS → `uv run pytest tests/m04 -v` → calidad → commit (`feat(m4): barrido de inactividad y agentcore sweep`).

---

### Task 20: Verificación de Fase A

- [ ] **Step 1:** `uv run pytest tests/m04 -v` → todas verdes (salvo las bloqueadas por P1/P2 si siguen sin respuesta: deben estar **ausentes**, no `xfail`).
- [ ] **Step 2:** `uv run pytest && uv run lint-imports && uv run mypy && uv run ruff check . && uv run agentcore contracts --check` → todo verde. M4 no cambia tipos de M0: `contracts --check` no debe tener diferencias (si las hay, se tocó M0 sin querer).
- [ ] **Step 3:** Revisar que ninguna prueba de M2/M3/M6/M10 cambió y que `git diff --stat main` solo lista archivos de "Archivos que M4 puede tocar".
- [ ] **Step 4:** Actualiza el spec (§3, §6, §12) con cualquier decisión tomada durante la fase; ejecuta `.claude/agents/revisor-spec.md` si el usuario lo pide.
- [ ] **Step 5: Commit** (`docs(m4): spec al día tras la fase A`).

---

## Fase B — depende de M5 y M11 reales

> Requisito: existen `agent_core.decision.UnderstandService` y `agent_core.audit.TurnRecorder`/`AuditLog`. Si no existen, **no inventes sus tipos**: deja esta fase pendiente e infórmalo.

### Task 21: Adaptador de Understand (M5)  **[POR VERIFICAR]**

**Files:** Create `agent_core/turn/understand_adapter.py`; Test `tests/m04/test_understand_adapter.py`.

**Interfaz esperada (m05 §2), a contrastar con el código antes de escribir nada:**
```python
UnderstandService.run(model_view_text: str, context: UnderstandContext, locale: str)
    -> tuple[UnderstandResult, list[EngineEvent]]
UnderstandResult: command; flow: str | None; interrupt: str | None; additional_flows: list[str]
                  slots: dict[str, Any]; above_threshold: dict[str, bool]; decision_id
UnderstandContext: (no definido en m05: recent_turns en vista model, nodo actual, confirm pendiente)
```
**Discrepancias esperadas con m04:** m04 §2 llama `understand.run(model_view_text, state, locale)`; m04 §3.2 habla de `p_cal`/`below_threshold` (M5 solo da `above_threshold`); `UnderstandContext` lo define M5; el costo de Understand puede no venir en el resultado (`DecisionOutput.cost_usd` está en `decision_made`).

- [ ] **Step 1:** Lee el código real; compáralo con "Interfaz esperada"; lista las diferencias en un comentario del adaptador y en el spec (§12). Si el contexto exige algo que M4 no tiene (p. ej. `recent_turns` requiere `TranscriptStore`), **detente y pregunta**.
- [ ] **Step 2: Prueba (falla)** — el adaptador satisface `UnderstandPort` (`_conforms`), traduce `UnderstandResult` → `UnderstandOutcome` sin perder `above_threshold`/`decision_id`, y pasa `awaiting_confirmation`/`current_node` al contexto; prueba de humo con `ScriptedProvider` de M5 (T-M5-07: `slots` `claimed`, `additional_flows` sin umbral).
- [ ] **Step 3–5:** implementar → PASS → repetir los tests de Tasks 12–14 con el adaptador real → calidad → commit (`feat(m4): adaptador de Understand (M5)`).

### Task 22: Adaptadores de M11 (`TurnRecorder`, `AuditLog`)  **[POR VERIFICAR]**

**Files:** Create `agent_core/turn/recorder_adapter.py`; Test `tests/m04/test_recorder_adapter.py`.

**Interfaz esperada (m11 §2):**
```python
AuditLog.append(uow, run_id, events) -> list[ChainedEvent]      # ¿persiste por uow.append_events o solo devuelve?
TurnRecorder.record_turn(run_id, user_msg_model, final_model, rejected) -> list[TranscriptRef]   # sin turn_id ni uow
```
**Verificar:** (a) si `append` persiste (M0: "M11 ya los encadenó" en `append_events`) o solo devuelve; (b) cómo obtiene `turn_id` el transcript (`TranscriptEntry.turn_id` es obligatorio); (c) si `TranscriptRef` se devuelve en orden usuario/asistente/rechazados; (d) qué `AuditSink` usa `events_so_far` (mismo store que `uow.append_events`).

- [ ] **Step 1–5:** leer código → anotar diferencias → prueba (adaptadores satisfacen `EventChain`/`TurnRecorderPort`) → implementar → reemplazar `PlainChain`/`InMemoryTurnRecorder` en el `World` por los reales y repetir **toda** `tests/m04` → comprobar `verify_chain` sobre un run de la suite (cadena íntegra, T-M11-08 no se rompe) → commit (`feat(m4): adaptadores de M11 (cadena y transcript)`).

### Task 23: Verificación de Fase B

- [ ] `uv run pytest tests/m04 && uv run pytest` con los reales; `verify_chain` = `ok` en los runs de las pruebas; ningún request de Understand contiene texto crudo (revisar `capture` de M5, T-M5-10); actualizar "Estado de dependencias" (quitar **[POR VERIFICAR]**); commit `docs(m4): dependencias verificadas contra M5 y M11`.

---

## Fase C — Postgres (depende de C4 y Docker)

> Antes de la Task 27: **pide aprobación de C4** (dependencia `psycopg`, marcador `integration`, cambios de CI, edición mínima de `tests/m03/harness.py` y `tests/contracts/test_uow_contract.py`). Coordina con M11 el esquema de `events` (append-only por permisos de base de datos, m11 §4). Las Tasks 24–26 preparan esquema, contrato y `conftest`; 27–31 exigen Postgres en marcha.

### Task 24: Esquema SQL y convenciones (sin driver)

**Files:** Create `agent_core/adapters/postgres/__init__.py`, `agent_core/adapters/postgres/schema.sql`; Test `tests/m04/test_schema_sql.py`.

**Esquema (Postgres 16, sin Alembic):** `runs(run_id pk, session_id unique null, state jsonb, state_version int, status text, inactive_after timestamptz null)` + índice parcial `(inactive_after) where status = 'open'`; `turn_leases(run_id pk, turn_id, expires_at)`; `turn_results(run_id, client_turn_id, result jsonb, pk(run_id, client_turn_id))`; `run_idempotency(principal_type, principal_id, key, body_hash, result jsonb, pk(...))`; `handoffs(handoff_ref pk, packet jsonb)` (upsert, m10 D12); `events(run_id, seq bigserial, event jsonb, pk(run_id, seq))` (INSERT-only: `REVOKE UPDATE, DELETE`); `outbox(message_id pk, run_id, payload jsonb, created_at, delivered_at null)`; `usage(principal_type, principal_id, at timestamptz, cost_usd numeric(20,8))`. **`Decimal`:** JSON con `agent_core.domain.dumps`/`loads` (M0), jamás `float`; el instante siempre lo trae la aplicación (`Clock`), nunca `now()` de SQL.

- [ ] Prueba: el archivo `schema.sql` existe, es idempotente (`CREATE TABLE IF NOT EXISTS`), no contiene `now()` ni `float`, y `REVOKE UPDATE, DELETE ON events`. Commit `feat(m4): esquema SQL del adaptador Postgres`.

### Task 25: Contrato de `UnitOfWork` parametrizado por backend

**Files:** Modify `tests/contracts/test_uow_contract.py` (aprobación C4).

- [ ] El `Backend` del contrato ya existe (docstring: "se agrega como parámetro de `backend`"). Añade un `pytest.param("postgres", marks=integration)` que construye `Backend(factory, audit, outbox, costs)` sobre Postgres; los `check_*` no cambian. `uv run pytest tests/contracts` (sin Postgres) → el parámetro se salta; con `AGENT_CORE_INTEGRATION=1` corre. Commit `test(m4): contrato de UoW parametrizado con el backend Postgres`.

### Task 26: `conftest` de integración

**Files:** Create `tests/integration/__init__.py`, `tests/integration/conftest.py`; Modify `pyproject.toml` (marcador `integration`, C4).

- [ ] `conftest.py`: fixture `pg_dsn` (`AGENT_CORE_PG_DSN`, por defecto la de `docker-compose.yml`: usuario `agentcore`, base `agentcore`, host `127.0.0.1:5432`; la contraseña de desarrollo local no es secreta y se lee del entorno o de `docker-compose.yml`, no se copia en el código); crea un **esquema temporal por prueba** (`CREATE SCHEMA t_<id>`; `SET search_path`; aplica `schema.sql`; `DROP SCHEMA … CASCADE` al final); salta con `pytest.skip` si `AGENT_CORE_INTEGRATION != "1"` o si no conecta.
- Run: `docker compose up -d postgres && AGENT_CORE_INTEGRATION=1 uv run pytest tests/integration -q`
- Expected: 0 tests (aún) y sin errores de colección; sin la variable: todo `skipped`. Commit `test(m4): conftest de integración con Postgres`.

### Task 27: Adaptador `PostgresUoW`

**Files:** Create `agent_core/adapters/postgres/uow.py` (+ `counters.py` para `AuditSink`, `Outbox`, `CostCounters`); Modify `pyproject.toml`/`uv.lock` (`psycopg[binary]>=3.2`, C4); Test `tests/contracts/test_uow_contract.py` (parámetro postgres).

**Semántica que debe cumplir (idéntica al doble, `testing/fakes/storage.py`):**
- `acquire_turn`: conexión **autocommit** aparte; `INSERT … ON CONFLICT (run_id) DO UPDATE SET turn_id = excluded.turn_id, expires_at = excluded.expires_at WHERE turn_leases.expires_at <= %(now)s OR turn_leases.turn_id = excluded.turn_id RETURNING 1`; sin fila → `TurnInProgress`. `now` y `ttl` vienen por parámetro (el adaptador nunca lee la hora; `adapters` está bajo la regla TID251 salvo los dos archivos de sistema).
- `release_turn`: se aplica con `commit()`. `save_run`: `UPDATE runs SET state = %s, state_version = %s + 1 … WHERE run_id = %s AND state_version = %s`; 0 filas → `VersionConflict`; alta (`expected_version == 0`) con `INSERT`; devuelve el estado con `state_version + 1`.
- `list_inactive(now, limit)`: `WHERE status = 'open' AND inactive_after < %s ORDER BY inactive_after, run_id LIMIT %s`.
- `put_run_idempotency` duplicado: gana el primero (`ON CONFLICT DO NOTHING`); `put_handoff` upsert; outbox con `ON CONFLICT (message_id) DO NOTHING`; `append_events` solo INSERT.
- JSON: `dumps`/`loads` de M0 (`Decimal` intacto); `RunState.model_validate` al leer.
- `adapters` solo importa `domain` y `ports` (contrato `adapters` de `.importlinter`).

- [ ] **Step 1:** `docker compose up -d postgres`; corre la suite de contrato con el parámetro postgres → FAIL (`ModuleNotFoundError`).
- [ ] **Step 2–4:** implementar → `AGENT_CORE_INTEGRATION=1 uv run pytest tests/contracts/test_uow_contract.py -v` → todos los `check_*` en verde en ambos backends.
- [ ] **Step 5:** `uv run mypy && uv run ruff check . && uv run lint-imports`; commit `feat(m4): adaptador PostgresUoW`.

### Task 28: Bloqueo optimista, `409` y transacción única (integración)

**Files:** Test `tests/integration/test_turn_postgres.py`.

- [ ] **Step 1: Pruebas**

```python
pytestmark = pytest.mark.integration

def test_bloqueo_optimista_dos_uow_sobre_la_misma_version(pg_world) -> None:
    ...  # A y B cargan v1; A commitea (v2); B save_run(expected=1) → VersionConflict; el estado sigue siendo el de A

def test_409_con_dos_conexiones_y_el_lease_visible_de_inmediato(pg_world) -> None:
    # hilo 1 entra a `understand` y se bloquea en un Event; hilo 2 llama handle_turn → EngineError 409;
    # se libera el hilo 1 → commit → un tercer turno pasa
    ...

def test_un_turno_es_una_sola_transaccion_de_estado_en_postgres(pg_world) -> None:
    ...  # CommitCounter: 1 commit de M4 sin escrituras; 3 con una escritura (1 + 2 de M3)

def test_turno_repetido_por_client_turn_id_devuelve_el_resultado_guardado(pg_world) -> None: ...
def test_una_caida_antes_del_commit_no_deja_nada_y_el_reintento_rehace_el_turno(pg_world) -> None: ...
def test_lease_vencido_lo_toma_otro_turno(pg_world) -> None: ...      # FakeClock avanza > ttl
def test_sweep_usa_list_inactive_de_postgres(pg_world) -> None: ...
def test_el_estado_persistido_conserva_decimal_sin_float(pg_world) -> None: ...
def test_events_no_admite_update_ni_delete(pg_world) -> None: ...     # permiso de base de datos (m11 §4)
```
`pg_world` = el `World` de `tests/m04/harness.py` con `uow_factory=PostgresUoW(...)` (parametrizar `World` con `store_factory`), sin cambiar los dobles de puertos.
- [ ] **Step 2–4:** FAIL → corregir (en el adaptador o el motor; nunca debilitando la prueba) → PASS con `AGENT_CORE_INTEGRATION=1`.
- [ ] **Step 5: Commit** (`test(m4): integración con Postgres (bloqueo optimista, 409, transacción única)`).

### Task 29: `crash_then_recover` de M3 sobre Postgres (DoD diferida de M3)

**Files:** Modify `tests/m03/harness.py` (`World(uow_factory=None)`; por defecto `store.uow`; aprobación C4); Test `tests/integration/test_m3_crash_postgres.py`.

- [ ] Parametriza `tests/m03/scenarios.py::crash_then_recover` con los tres puntos (`after_commit_1`, `after_call`, `on_commit_2`) sobre un `World` con `PostgresUoW`. Aserciones ya definidas por el escenario: la acción cargada va a `verify` sin re-ejecutarse (`writes_before_recovery` = 1 escritura), `pending_recovery == [action_id]`. `AGENT_CORE_INTEGRATION=1 uv run pytest tests/integration/test_m3_crash_postgres.py -v` → 3 passed; `uv run pytest tests/m03` (in-memory) sigue verde. Commit `test(m4): crash_then_recover de M3 sobre Postgres`.

### Task 30: CI con Postgres

**Files:** Modify `.github/workflows/ci.yml` (aprobación C4).

- [ ] Añade un job `integration` con `services: postgres: image: postgres:16` (las variables de entorno de desarrollo del `docker-compose.yml`, sin secretos reales), `AGENT_CORE_INTEGRATION=1` y `uv run pytest tests/integration tests/contracts`. Mantén las acciones fijadas por SHA como en el job `check`. Commit `ci(m4): job de integración con Postgres`.

### Task 31: `agentcore sweep` contra Postgres (demo)

**Files:** Modify `agent_core/cli.py`; Test `tests/integration/test_cli_sweep_postgres.py`.

- [ ] `agentcore sweep --dsn DSN --registry DIR [--once]` compone `Sweeper(PostgresUoW factory, registry_from_directory…, SystemClock, SystemIds, ActionManager, chain)` (solo lo necesario; la cadena real de M11 o `PlainChain` según Fase B). Prueba: siembra un run inactivo en Postgres, corre `main(["sweep", …])` con un `Clock` inyectable y comprueba `status=closed/abandoned`. Salida: una línea con `evaluated/abandoned/skipped` (sin datos de runs). Commit `feat(m4): agentcore sweep sobre Postgres`.

---

## Definición de terminado (m04 §10 y común, marcar al cerrar)

- [ ] Pipeline completo con dobles: T-M4-01…17 en verde (Tasks 8–19).
- [ ] Integración con Postgres: bloqueo optimista, `409` y transacción única por turno (Task 28).
- [ ] `sweep` invocable por `agentcore sweep` (Tasks 19 y 31).
- [ ] Interfaz pública exportada y con tipos (`agent_core/turn/__init__.py`, `mypy` strict).
- [ ] `lint-imports` en verde (contrato `turn`: sin `views`, `flows`, `response`, `knowledge`).
- [ ] Eventos emitidos validados contra el esquema de M0 (Task 18) y `EVENT_EMITTERS` respetado (`run_closed` solo M4).
- [ ] `ruff` (Clock/IdSource), `mypy`, `agentcore contracts --check` sin diferencias.
- [ ] Sin TODO sin issue.
- [ ] P1 y P2 resueltas por el usuario y reflejadas en el spec (§3.1, §3.3, §11).
- [ ] Fase B: adaptadores a M5/M11 reales verificados; **[POR VERIFICAR]** eliminado del plan y del spec.
- [ ] DoD diferida de M3 (`crash_then_recover` sobre Postgres) cumplida (Task 29).
- [ ] Spec rev. 2 actualizado (decisiones, C1–C15, discrepancias); LOC registradas en el spec.

## Discrepancias spec/código detectadas al planear

1. `m04 §2`: `Interpreter` como dependencia — M2 son funciones (`advance`, `start_flow`, `begin_turn`) y un `StepContext` que exige `ViewService`/`TokenVault` de M7 (prohibido para M4). → `RuntimeFactory` (C1).
2. `m04 §3.6`: `uow.list_inactive(now)` — el puerto real es `list_inactive(now, limit)`.
3. `m04 §3.2`: `p_cal` y `below_threshold` — M5 entrega `above_threshold` (m05 §2); `UnderstandContext` sin definir; m04 §3.1 llama `understand.run(model_view_text, state, locale)`.
4. `m04 §3.1 paso 8`: `model_view_text` — nadie lo produce; `RunState.token_map` — nadie lo sella.
5. `m04 §2`: constructor sin `AuditSink` (necesario para `events_so_far` de M10), sin `EventChain` (M3 `EventRecorder`) ni `AuthzPort`.
6. `m11 §2`: `TurnRecorder.record_turn(run_id, user_msg_model, final_model, rejected)` sin `turn_id`/`uow`, y `AuditLog.append` devuelve `list[ChainedEvent]` sin aclarar si persiste.
7. `m04 §3.5` vs `EVENT_EMITTERS`: M4 rellena `response_emitted.transcript_fp` pero solo M8 puede emitirlo; nadie emite `response_emitted` para plantillas del motor (M2 D6 abierto) → C10.
8. `RunClosedPayload.closed_by` (`flow|abandonment|escalation|revocation`) no cubre `abstained`/`clarify_exhausted`/`cancel` cerrados por M4 → C8, C11.
9. `TurnResult.trace_id` obligatorio sin fuente definida → C6. `EngineTemplates` sin plantilla de cancelación → C8.
10. `m04 §2` `start_run -> TurnResult` vs M9 `create_run` que necesita `RunResult` (`output`, `first_turn`) → C5.
11. M0 rev. 5 hace a M4 dueño de `add_usage`/`CostCounters`; el spec de M4 no lo lista → decisión 10.
12. No hay `tests/integration/`, driver Postgres, esquema ni marcador `integration`; `tests/contracts/test_uow_contract.py` promete el parámetro `backend`; `tests/m03/harness.py::World` fija `store.uow` (impide correr `crash_then_recover` sobre Postgres sin un cambio mínimo) → C4.
13. M3 Abierto (b): `yes` por botón con token rotado/vencido devuelve `unclear` sin contar intento; T-M4-05 ("botón nunca `unclear`") solo se cumple del lado de M4 (no pasa por Understand) → C14.
14. `Interrupt.signal_policy`: los datos que evalúa la política no están definidos → C3. El flow interrumpido por una interrupción `start_flow` no tiene destino definido → C7.
15. `m04 §3.3`: al descartar la última pendiente con `deny` (P1) y el `outcome` del run tras ofertas rechazadas no están definidos (subpreguntas de P1).
