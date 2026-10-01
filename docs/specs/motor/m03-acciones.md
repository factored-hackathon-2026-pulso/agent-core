# M3 — Protocolo de escritura (acciones)

- Estado: implementado (rev. 3, 2026-09-30) · Fase 1
- Paquete: `agent_core.actions`
- Origen: spec general §8.2, §4 (invalidación de acciones), §5 (`confirm`, escritura, `verify`), §10
- ADRs: 0007 (acción congelada, outbox de intención, idempotencia, readback, `claims`, confirmación acotada)
- Usa: M0 · Lo usan: M2 (nodos), M4 (recuperación e invalidación)

## 1. Propósito y límites

Es el dueño de `RunState.actions` y del invariante **confirm → act → verify**. Congela la acción, maneja el token de confirmación, ejecuta la escritura con dos commits propios, la verifica por clave y recupera tras una caída.

**No hace:** decidir cuándo se confirma (lo decide M4 con Understand o el botón y se lo pasa a M2), validar estáticamente los flows (M1), ni la tool en sí (unidad 3).

## 2. Interfaz pública

```python
@dataclass(frozen=True)
class ActionContext:                       # lo arma M2 (M4 en la recuperación); M3 no importa M1, M2, M7 ni M11
    uow_factory: UnitOfWorkFactory         # cada commit propio es una UoW nueva
    tools: ToolExecutor
    call: ToolCallContext                  # run, release, principal, sujeto y turn_id
    render: TemplateRenderer               # (RefSpec, RunState) -> Message            plantillas (M1/M2)
    predicate: PredicateEvaluator          # (expr, data) -> bool                      JSON Logic (M2)
    resolve: RefResolver = exact_ref       # RefSpec -> EntityRef                      release_view (M1)
    record: EventRecorder = append_events  # (uow, state, events) en cada commit       M4 + M11
    audit: AuditProjector = RedactAll()    # vista audit y huella de args y resultados M7
    bound_params: Mapping[str, str] = {}

class ActionManager:
    def __init__(self, ids: IdSource, clock: Clock)
    def propose(self, state, confirm_node, resolved_args, tool_def, ctx) -> tuple[RunState, ConfirmationPrompt, list[EngineEvent]]
    def answer(self, state, confirm_node, answer: Literal["yes", "no", "unclear"], token: str | None, ctx)
        -> tuple[RunState, Literal["yes", "no", "unclear", "max_attempts"], list[EngineEvent]]
    def execute_write(self, state, write_node, ctx)
        -> tuple[RunState, Literal["ok", "denied", "uncertain", "step_up_required"], list[EngineEvent]]  # eventos ya persistidos
    def freeze_draft_write(self, state, write_node, resolved_args, tool_def, ctx) -> RunState   # ADR 0019: sin confirm, sin token
    def verify(self, state, verify_node, ctx) -> tuple[RunState, Literal["verified", "failed"], list[EngineEvent]]
    def pending_recovery(self, state) -> list[str]           # action_ids en `executing`
    def invalidate(self, state, reason: InvalidationReason, *, turn_id: str | None = None) -> tuple[RunState, list[EngineEvent]]
    def expire_tokens(self, state, *, turn_id: str | None = None) -> tuple[RunState, list[EngineEvent]]

# InvalidationReason, ConfirmationPrompt, Action y ActionState son tipos de M0 (§2.6, §2.8).
# "no" del usuario cancela con reason = denied_by_user.
```

- `action_id` sale de `IdSource.new_id("action")` y el token de `IdSource.secret_token()` (M0 §2.9). `args_hash = sha256_hex(canonical_bytes(args))` con la utilidad JCS de M0. Todo instante sale del `Clock` del constructor.
- **Eventos:** los de `execute_write` ya quedaron persistidos en sus commits (vía `ctx.record`); M4 no los vuelve a agregar. Los de los demás métodos los persiste M4 con el turno.
- **Valores por defecto de los ganchos:**
  - `exact_ref` exige referencias exactas;
  - `append_events` agrega sin encadenar (fase 1, sin M11);
  - `RedactAll` reemplaza cada argumento por `***` y omite el resultado y la huella, hasta que se cablee M7.

## 3. Comportamiento

### 3.1 Máquina de estados

| Desde | Evento | Hacia |
|---|---|---|
| — | `propose` | `proposed` |
| `proposed` | `answer(yes)` con token vigente | `confirmed` |
| `proposed` | `answer(no)` | `cancelled` |
| `proposed` | `max_attempts` / token vencido / invalidación | `cancelled` |
| `confirmed` | `execute_write` (commit 1) | `executing` |
| `confirmed` | invalidación antes de ejecutar | `cancelled` |
| `executing` | resultado de la tool (commit 2) | `executed` · `uncertain` · `denied` |
| `executing` | `step_up_required` (commit 2) | `confirmed` |
| `executed` · `uncertain` | `verify` | `verified` · `failed` |
| `executing` (al cargar) | recuperación | → `verify` |
| — | `freeze_draft_write` (ADR 0019, §3.7) | `confirmed` (sin token) |

Cualquier otra transición lanza `IllegalTransition` (bug, no error de usuario).

### 3.2 `propose` y reentrada

- Si el `confirm` **no** tiene acción `proposed`: congela `{tool, args}`, calcula `args_hash = sha256(JCS(args))`, crea `action_id` (opaco, de `IdSource.new_id(IdKind.action)`), token aleatorio de 128 bits (se guarda solo su hash), `token_exp = now + tool_def.confirmation_ttl` (5 min por defecto). Emite el prompt con `summary_template`.
- Si ya tiene una `proposed` con token vigente: **no crea otra**. Conserva `action_id` y `token_exp`, pero **rota el token** (nuevo token, nuevo hash), porque solo se guarda el hash y el anterior no se puede devolver. El token anterior deja de confirmar. Usa `reprompt_template` o, si no hay, `summary_template`.
- Si la `proposed` tiene el token vencido: la pasa a `cancelled` y congela una nueva.
- Invariante: nunca hay dos acciones `proposed` del mismo `confirm`.

### 3.3 `answer`

- `yes`: exige token vigente. Por botón, el token del request debe coincidir con el hash; por texto (`affirm`), se usa el token de la acción `proposed` del nodo. Token vencido → `cancelled` y el resultado es `unclear` para reentrar y congelar de nuevo. Emite `action_confirmed`. Un token de botón cuyo hash no coincide (p. ej. el anterior a una rotación) da `unclear`: no cambia el estado, no suma intento y no emite evento. La vigencia es `now < token_exp`.
- `no` → `cancelled`.
- `unclear` → `node_attempts[confirm] += 1`; si llega a `max_attempts` → `cancelled` y resultado `max_attempts`. Cada `unclear` también cuenta en `repair_turns_used` (lo suma M4).
- Caveat (ADR 0007 §8, T-M4-05 "botón nunca `unclear`"): un `yes` por botón con token rotado o vencido devuelve `unclear` sin contar intento, así que M4 debe sumar `repair_turns_used` solo cuando `node_attempts` creció.

### 3.4 `execute_write` (dos transacciones propias)

1. **Commit 1:** `state = executing` + `action_dispatched{action_id, tool@v, args_fp}` (huella con clave de M7; `Action.args_hash` sigue en el estado).
2. `tools.execute(tool, frozen_args, bound_params, run_ctx, idempotency_key=action_id)`. Los `args` **siempre** salen de la acción congelada, nunca del nodo.
3. **Commit 2:** mapea el estado de la tool:
   - `ok` → `executed`;
   - `denied` (bloqueo de política antes de llamar) → `denied`;
   - cualquier otra cosa (error, 5xx, reset, timeout, excepción) → `uncertain`, con el error original en `tool_called`. Una excepción de la tool se registra solo por su tipo (`error = type(exc).__name__`), nunca por su mensaje.
   - `step_up_required` → la acción vuelve a `confirmed` en el commit 2, sin hecho, y `execute_write` devuelve `step_up_required`. M2 aplica su §3.4. (La unidad 3 debe devolverlo **antes** de cualquier efecto.)
4. Si fue `ok`, guarda el resultado en `facts[save_as]` con `source = {kind: tool, ref: action_id}`.

### 3.5 `verify`

Solo admite `by: idempotency_key` (otro valor → `ValueError`). Actúa sobre la única acción del flow activo en `executed`, `uncertain` o `executing` (recuperación). Llama `tools.execute(ctx.resolve(readback), {idempotency_key: action_id})` y evalúa `predicate` con `ctx.predicate` sobre `{"readback": <resultado full>}`. Si se encontró, guarda el readback en `facts[save_as]` con `source.ref = action_id`. `true` → `verified`; `false`, no encontrado, error, excepción o predicado que falla → `failed`. Emite `tool_called` (readback) y `action_verified{action_id, result, readback_call_id}`.

### 3.6 Recuperación e invalidación

- `pending_recovery`: al cargar un run (M4), toda acción en `executing` se lleva a su `verify` **sin re-ejecutar**. M4 posiciona el flow en el `verify` que sigue al nodo de escritura (lo resuelve con `write_node.next.uncertain`).
- `invalidate`: toda acción `proposed` o `confirmed` pasa a `cancelled`. Las `executing` o posteriores **no** se tocan.

### 3.7 Escritura `draft` (ADR 0019)

Una tool `write_draft` se invoca con un nodo `tool` con `draft: true` y **sin `confirm`**. `freeze_draft_write` crea la acción directamente en `confirmed`: `write_node_id` es el nodo; `confirm_node_id`, token y vencimiento son nulos; `idempotency_key = action_id`. Con una acción `confirmed` de ese nodo en el flow activo (reentrada tras un `step_up_required`) conserva la acción congelada y sus args. Lanza `ValueError` si el nodo no es draft o la tool no es la `write_draft` que declara, e `IllegalTransition` sin flow activo. `execute_write` busca la acción por `write_node_id` en vez de por `action_from`. Desde ahí siguen `execute_write` (commit 1, tool, commit 2), `verify` y la recuperación `executing → verify` exactamente como en §3.4 a §3.6. `invalidate` cancela una acción draft `confirmed` que aún no corrió; `expire_tokens` la ignora (no tiene token). Pruebas: `tests/m03/test_draft_write.py`.

## 4. Invariantes

- `idempotency_key == action_id` en todo reintento.
- Nunca se invoca una escritura sin un commit previo de `executing`.
- Una acción en `executing` nunca se re-ejecuta.
- A lo sumo una acción `proposed` por `confirm`.
- Un token vencido nunca confirma.

## 5. Fallas

| Falla | Comportamiento |
|---|---|
| Caída entre commit 1 y la llamada | al cargar: `executing` → `verify`; el readback dice si hubo efecto |
| Caída entre la llamada y commit 2 | igual que la anterior |
| Falla del commit 2 | igual; el efecto queda probado por el readback |
| Reintento del turno completo | mismo `action_id` → la tool devuelve el recurso existente |
| Token vencido | `cancelled`; nuevo `confirm` |

## 6. Eventos que emite

`action_confirmed`, `action_cancelled{action_id, reason}` (invalidación, `no`, token vencido, `max_attempts`), `action_dispatched`, `tool_called` (escritura y readback), `action_verified`.

## 7. Pruebas

Con `FakeToolExecutor` guionable. Las fallas se inyectan por punto: `after_commit_1` y `on_commit_2` con `CrashingFactory`, que envuelve cualquier `UnitOfWorkFactory`; `after_call` con `CrashAfterCall` (`tests/m03/harness.py`). El escenario de caída es reutilizable: `tests/m03/scenarios.py`.

| ID | Caso | §13 |
|---|---|---|
| T-M3-01 | Camino feliz: `proposed → confirmed → executing → executed → verified` | 3 |
| T-M3-02 | 5xx o timeout → `uncertain` → `verify` por clave | 3 |
| T-M3-03 | Caída después de `action_dispatched` → al recargar va a `verify`, la tool se llamó una sola vez | 3 |
| T-M3-04 | El reintento del turno usa el mismo `idempotency_key` | 3 |
| T-M3-05 | Dos `unclear` con `max_attempts: 2` → `max_attempts`, acción `cancelled` | 11 |
| T-M3-06 | Reentrada con token vigente conserva `action_id` y `token_exp` y rota el token; nunca hay dos `proposed` | 11 |
| T-M3-07 | Reentrada con token vencido cancela y crea otra; `yes` con el token viejo no confirma | 11 |
| T-M3-08 | `invalidate` cancela `proposed`/`confirmed` y no toca `executing` | 10 |
| T-M3-09 | Los `args` ejecutados son los congelados aunque cambien los slots | — |
| T-M3-10 | Transición ilegal lanza `IllegalTransition` | — |
| T-M3-11 | `denied` no pasa por `verify` y no se reintenta | — |

## 8. Evaluación

Escrituras duplicadas (objetivo 0), tasa `uncertain`, tasa `uncertain → verified` frente a `failed`, tasa de confirmaciones rechazadas o vencidas, latencia de escritura + readback (`tool_called.latency_ms` de ambas llamadas, que M3 mide con `Clock.monotonic_ns()` alrededor de `execute`).

## 9. Puntos de iteración

- Worker asíncrono de producción: reemplaza `execute_write` detrás de la misma interfaz (el resultado `uncertain` ya modela la espera).
- TTL del token: por `tool_def`.
- Formato del token: interno; el cliente solo lo reenvía.

## 10. Definición de terminado

- Máquina de estados con tabla de transiciones como dato y T-M3-01…11 en verde.
- Prueba de integración con Postgres para la caída entre commits: **diferida a M4**, que es el dueño del `UnitOfWork` de Postgres. M4 corre `tests/m03/scenarios.py::crash_then_recover` con un `World` sobre Postgres (decisión del 2026-09-29). **Hecho** (2026-09-29): `tests/integration/test_m3_crash_postgres.py` (T-M3-03 y T-M3-04 sobre Postgres real, con los eventos de M3 encadenados por el `AuditLog` de M11).

## 11. Abiertos

- Resueltos en M0 rev. 2: `uncertain` y `denied` son estados explícitos (`denied` es terminal, sin `verify`), y la invalidación emite `action_cancelled`.
- **Resueltos el 2026-09-30 (rev. 3), aprobados por el usuario:**
  - **(a) y (f)** `propose` compara `args_hash` y la versión de la tool (no solo `tool.id`); si difieren cancela con `InvalidationReason.args_changed` y congela una acción nueva (`tests/m03/test_reentry_args.py`).
  - **(b)** El botón con token rotado o vencido no cuenta intento ni reparación y lleva a la rama `unclear` del nodo, que repropone con token nuevo; el ADR 0007 §8 se precisó (ya no dice "nunca da `unclear`" a secas). No se agrega un resultado `stale_token`: el comportamiento ya era ese y evita cambiar el esquema de `confirm`.
  - **(c)** `action_dispatched` lleva `args_fp` (HMAC con clave, ADR 0008) en vez de `args_hash` sin clave.
  - **(d)** `tool_called.attempt` de la escritura es `node_attempts[nodo] + 1` (el reintento tras un step-up es el intento 2).
  - **(e)** Un readback que no contesta (error, timeout, excepción) devuelve `unavailable`: la acción no pasa a `failed` (queda `executed`/`uncertain`; `executing` → `uncertain`) y el nodo `verify` sigue su rama `failed`. `failed` queda solo para "contestó y el efecto no está".
- Commits propios: cada uno es una `UnitOfWork` nueva con `save_run(expected_version)`; el lease del turno (M4) sigue tomado durante ellos.
- Rev. 2 (2026-09-29, con el usuario): token rotado en la reentrada; `EventRecorder` para los eventos de los commits propios; `ActionContext` con ganchos, porque M3 solo importa M0; `step_up_required` como resultado de `execute_write`; Postgres diferido a M4.
