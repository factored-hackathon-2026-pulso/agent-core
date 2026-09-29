# M2 — Intérprete de nodos

- Estado: borrador · Fase 1
- Paquete: `agent_core.interpreter`
- Origen: spec general §4.7, §5, §8 (slots, hechos, decisiones), §10 (fallas de tools y presupuesto)
- ADRs: 0004 (flows deterministas), 0010 (step-up), 0011 (`compute`), 0009 (`rule` con `policy`)
- Usa: M0, M1 (`derive_claims`, `release_view`, `JSONLOGIC_OPS`, rutas y plantillas), M3, M5, M7, M8 · Lo usa: M4

## 1. Propósito y límites

Dado un `RunState` con un flow activo, ejecuta nodos hasta llegar a uno que **espera al principal** o a uno **terminal**, y devuelve el estado nuevo, los mensajes y los eventos.

**No hace:** decidir qué flow corre ni manejadores globales (M4), la mecánica de acciones (M3; M2 solo la invoca), validar el texto generado (M8), persistir (M4), construir el handoff (M10: M2 devuelve una *solicitud* de escalamiento).

## 2. Interfaz pública

```python
class StepContext:
    release: Release; agent: Agent; locale: str; clock: Clock; degraded: bool
    registry: RegistryPort; tools: ToolExecutor; decisions: DecisionService   # M5
    actions: ActionManager                                                     # M3
    responder: Responder                                                       # M8
    views: ViewService                                                         # M7
    bound_params: dict

class Resume:                         # por qué se reanuda el nodo actual
    kind: Literal["slot_answer", "confirm_answer", "step_up_retry", "none"]
    value: Any = None                 # texto del slot, o "yes"|"no"|"unclear" para confirm

class Stop(StrEnum): awaiting_slot, awaiting_confirmation, awaiting_step_up, awaiting_user, terminal
# M4 traduce Stop → RunState.awaiting: slot, confirmation, step_up, input (awaiting_user), none (terminal)
# EscalationRequest, ConfirmationPrompt y StepUpPrompt son tipos de M0 (los consumen M4 y M10)
class StepOutcome:
    state: RunState; stop: Stop; messages: list[Message]; events: list[EngineEvent]
    end_outcome: Outcome | None; escalation: EscalationRequest | None
    confirmation: ConfirmationPrompt | None; step_up: StepUpPrompt | None

def advance(state: RunState, ctx: StepContext, resume: Resume) -> StepOutcome
def start_flow(state: RunState, flow_ref: EntityRef) -> RunState      # fija active_flow en el primer nodo
```

Internamente, un registro `HANDLERS: dict[str, NodeHandler]`; cada handler es `(node, state, ctx, resume) -> NodeResult{result_key | stop, state, messages, events}`. Agregar un tipo de nodo es agregar un handler y su esquema en M1.

## 3. Comportamiento

### 3.1 Bucle

```
loop:
  node = flow.node(state.active_flow.node_id)
  check_budgets(ctx)            → si se agota: escalation(budget_exceeded)
  emit node_entered
  r = HANDLERS[node.type](node, state, ctx, resume); resume = none
  si r es Stop → devolver
  si node es terminal → devolver terminal
  state.active_flow.node_id = node.next[r.result_key]
```

### 3.2 Resolución de variables

Rutas permitidas en `args`, plantillas y `rule`: `slots.<x>`, `facts.<x>.value[.campo…]`, `decisions.<x>.<campo>` (solo como argumento de tool `compute`, ya validado por G0-10) y literales. La gramática, la distinción ruta/literal y el recorrido recursivo de `args` son los de M1 §3.2 (`parse_path`, `value_paths`); las plantillas usan `{{ ruta }}` (M1 §3.3, `template_vars`). Una ruta inexistente en runtime → resultado `error` del nodo (nunca excepción).

### 3.3 Handlers

| Nodo | Comportamiento |
|---|---|
| `decide` | `ctx.decisions.decide(model, input_view ⊆ vista model, locale)`. Guarda en `decisions[save_as]`. Resultado = `branch_on` si supera su umbral, si no `low_confidence` (la decisión de umbral la toma M5) |
| `rule` | Evalúa `policy@v.expr` o `expr` con el evaluador JSON Logic sobre `facts.*.value` (vista `full`) y `slots` con `status: validated`. Un slot `claimed` se trata como ausente (`null`). Emite `rule_evaluated {policy@v?, inputs (audit), result}` |
| `collect` | Sin `resume`: emite la plantilla `prompt_ref` y para en `awaiting_slot`. Con `slot_answer`: aplica `validator` (tipo, regex, enum o `decide`); si pasa, `slots[slot] = {value, validated}` → `ok`; si no, `node_attempts += 1` y repregunta; al llegar a `max_attempts` → `max_attempts`. Cada reintento suma a `repair_turns_used` (M4 lo lee) |
| `tool` lectura/`compute` | `ctx.tools.execute(...)` con `bound_params`. `ok` → `facts[save_as] = {value: result_full, source: {tool|compute, ref, inputs}}`. `inputs` = `fact_id`/`decision_id` leídos en `args`. Retries: ≤2 solo si `tool_def.idempotent` y el estado fue `timeout`/`error`. Circuit breaker simple por tool en el proceso (N fallas en ventana → `error` inmediato). Emite `tool_called` por intento, con `latency_ms` medido con `Clock.monotonic_ns()` alrededor de `execute` (un corte por circuit breaker da `0`) |
| `tool` escritura | Delega en `ctx.actions.execute_write(state, action_from, ...)` (M3). Resultado `ok`/`denied`/`uncertain` |
| `confirm` | Sin `resume`: `ctx.actions.propose(...)` → para en `awaiting_confirmation` con el `ConfirmationPrompt`. Con `confirm_answer`: `ctx.actions.answer(...)` → `yes`/`no`/`unclear`/`max_attempts` |
| `verify` | `ctx.actions.verify(...)` → `verified`/`failed`; guarda el readback en `facts[save_as]` |
| `respond` | `template_ref`: renderiza la plantilla del `locale` con los hechos (vista `model`) y la entrega a M8 para el render final. `generate`: `ctx.responder.generate(...)`; en modo degradado usa `fallback_template_ref` sin llamar al modelo. Si `await: true`, para en `awaiting_user`; si no, sigue por `next` |
| `escalate` | Devuelve `EscalationRequest{reason_code, target_queue, priority_expr evaluada}`; `stop = terminal` |
| `end` | `end_outcome = config.outcome`; aplica `output_map` en modo task; `stop = terminal` |

### 3.4 Step-up (ADR 0010)

Si `ToolExecutor` devuelve `step_up_required`, el nodo **no avanza**: emite `step_up_requested`, suma `node_attempts[node]` y para en `awaiting_step_up` con `{required_level, reason}`. El siguiente turno llega con `Resume(step_up_retry)` y reintenta el mismo nodo. Al pasar `max_attempts` del nodo (o 2 por defecto) → `EscalationRequest(auth_insufficient)`.

### 3.5 Presupuestos

Antes de cada nodo y de cada llamada a modelo se descuenta de `budgets_used`: `max_nodes_per_turn`, `max_model_calls_per_turn`, `max_tokens_per_run`, `max_cost_per_run`, `max_wall_ms_per_turn` (medido con `ctx.clock`). Al agotarse cualquiera → `EscalationRequest(budget_exceeded)`.

### 3.6 Evaluador JSON Logic

Subconjunto cerrado: `JSONLOGIC_OPS` de M1 (`var`, `==`, `!=`, `>`, `>=`, `<`, `<=`, `and`, `or`, `!`, `in`, `if`, `missing`, con su aridad). Un operador fuera de la lista es error de esquema (G0-01), no de runtime. Aritmética con `Decimal`, nunca `float`.

## 4. Invariantes

- **Determinismo:** con el mismo estado, las mismas salidas de puertos y el mismo `Clock`, `advance` produce el mismo estado y los mismos eventos (base del replay, M11).
- Sin I/O fuera de `ctx`.
- Un `rule` nunca lee `decisions.*` ni slots `claimed`.
- Una escritura solo ocurre por `ctx.actions`; M2 nunca llama a una tool `write_*` directamente.
- Los hechos guardan la vista `full`; todo lo que sale a modelos pasa por `ctx.views`.

## 5. Fallas

| Falla | Comportamiento |
|---|---|
| Tool de lectura: timeout/error | retries si es idempotente → rama `error`/`timeout` |
| Tool: `denied` | rama `denied`; M2 emite `access_denied{tool_denied}` |
| Proveedor de decisión caído | lo absorbe M5 → `low_confidence` |
| Ruta de variable inexistente | rama `error` del nodo |
| Presupuesto agotado | `escalate(budget_exceeded)` |
| Step-up agotado | `escalate(auth_insufficient)` |

## 6. Eventos que emite

`node_entered`, `rule_evaluated`, `tool_called` (lectura y `compute`), `step_up_requested`, `access_denied` (`tool_denied`). `decision_made` lo emite M5 y los de acciones M3; M2 los agrega a la lista del `StepOutcome`.

## 7. Pruebas

Tabla por tipo de nodo con `FakeToolExecutor`, `ScriptedProvider` y `FakeClock`.

| ID | Caso | §13 |
|---|---|---|
| T-M2-01 | `disputa-cargo` (conversacional) manejado por el arnés de fase 1 con `Resume` guionados, camino feliz, termina en `end(resolved)` | 2 (fixture) |
| T-M2-02 | `rule` con slot `claimed` lo trata como `null` | — |
| T-M2-03 | `rule` con `policy` emite `rule_evaluated` con `policy@v`, entradas en vista `audit` y resultado | — |
| T-M2-04 | `compute` guarda procedencia con los `fact_id`/`decision_id` de entrada | — |
| T-M2-05 | `collect` repregunta y sale por `max_attempts` al agotar | 11 |
| T-M2-06 | `step_up_required` para en el mismo nodo; el reintento con nivel suficiente continúa | 5 |
| T-M2-07 | Step-up agotado escala con `auth_insufficient` | 5 |
| T-M2-08 | Cada presupuesto agotado escala con `budget_exceeded` | — |
| T-M2-09 | Retries solo en tools idempotentes; nunca en `write_*` | — |
| T-M2-10 | Modo degradado: `respond(generate)` usa la plantilla sin llamar al gateway | — |
| T-M2-11 | Determinismo: dos ejecuciones con los mismos dobles producen eventos idénticos | 2 |
| T-M2-12 | Aritmética de `rule` con `Decimal` (`500.00 > 500` es falso) | — |

## 8. Evaluación

Aporta, vía `node_entered`: caída por nodo, turnos por run, nodos por turno, tasa de `budget_exceeded`, tasa de step-up por tool.

Vía `tool_called` (lectura y `compute`), por `tool@v`: latencia p50/p95, tasa por `status`, reintentos por llamada, cortes del circuit breaker y llamadas por run.

## 9. Puntos de iteración

- Tipo de nodo nuevo: handler + esquema en M1 + reglas G0.
- Política de retries y circuit breaker: parámetros por `tool_def`, sin cambiar la interfaz.
- El evaluador JSON Logic se puede reemplazar por CEL (riesgo §15) detrás de la misma función `evaluate(expr, data)`.

## 10. Definición de terminado

- Handlers MVP completos; `disputa-cargo` corre en task con dobles.
- T-M2-01…12 en verde; LOC del paquete registradas (estimación total del intérprete: 1.500–2.000 con pruebas).

## 11. Abiertos

- `max_attempts` del step-up: M0 rev. 2 lo declara como `step_up_max_attempts = 2` en `ToolConfig` y `WriteToolConfig`; confirmar al implementar M2.
- Todo ID (`fact_id`, `call_id`) sale de `ctx.ids` (`IdSource`, M0) para que el replay sea determinista; `StepContext` gana `ids: IdSource`.
- Quién llena `open_questions` (índice §10).
- Reclamos en runtime: `response_emitted.claims` sale de `derive_claims(flow, release_view(registry, release))` de M1, calculado una vez por `flow@v` (M1 §3.6).
