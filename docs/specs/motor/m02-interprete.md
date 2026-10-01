# M2 — Intérprete de nodos

- Estado: implementado (rev. 4: nodo `knowledge`, M12) · Fase 1
- Paquete: `agent_core.interpreter`
- Origen: spec general §4.7, §5, §8 (slots, hechos, decisiones), §10 (fallas de tools y presupuesto)
- ADRs: 0004 (flows deterministas), 0010 (step-up), 0011 (`compute`), 0009 (`rule` con `policy`)
- Usa: M0, M1 (`derive_claims`, `release_view`, `JSONLOGIC_OPS`, rutas y plantillas), M3, M5, M7, M8, M12 (interfaz pública: `KnowledgeService`, `KnowledgeContext`) · Lo usa: M4

## 1. Propósito y límites

Dado un `RunState` con un flow activo, ejecuta nodos hasta llegar a uno que **espera al principal** o a uno **terminal**, y devuelve el estado nuevo, los mensajes y los eventos.

**No hace:** decidir qué flow corre ni manejadores globales (M4), la mecánica de acciones (M3; M2 solo la invoca), validar el texto generado (M8), persistir (M4), construir el handoff (M10: M2 devuelve una *solicitud* de escalamiento).

## 2. Interfaz pública

```python
class StepContext:                    # dataclass congelada
    release: Release; agent: Agent; locale: str; clock: Clock; degraded: bool
    registry: RegistryPort; tools: ToolExecutor
    decisions: DecisionPort           # puerto local de M2; M5 lo adapta (D1)
    actions: ActionManager            # M3
    responder: ResponderPort          # puerto local de M2; M8 lo adapta (D1)
    views: ViewService; vault: TokenVault                                       # M7
    ids: IdSource; uow_factory: UnitOfWorkFactory                               # D2
    knowledge: KnowledgeService | None = None    # M12; sin él, un nodo `knowledge` es error de cableado
    bound_params: Mapping[str, str] = {}
    record: EventRecorder = append_events                                       # M3; M4 lo reemplaza
    turn_id: str | None = None
    breaker: CircuitBreaker = CircuitBreaker()                                  # D10

class Resume:                         # por qué se reanuda el nodo actual
    kind: Literal["slot_answer", "confirm_answer", "step_up_retry", "none"]
    value: JsonValue = None           # texto del slot, o "yes"|"no"|"unclear" para confirm
    token: str | None = None          # token del botón de confirmación (M3 `answer`) (D3)

class Stop(StrEnum): awaiting_slot, awaiting_confirmation, awaiting_step_up, awaiting_user, terminal
# M4 traduce Stop → RunState.awaiting: slot, confirmation, step_up, input (awaiting_user), none (terminal)
# EscalationRequest, ConfirmationPrompt y StepUpPrompt son tipos de M0 (los consumen M4 y M10)
class StepOutcome:
    state: RunState; stop: Stop; messages: list[Message]; events: list[EngineEvent]
    end_outcome: Outcome | None; escalation: EscalationRequest | None
    confirmation: ConfirmationPrompt | None; step_up: StepUpPrompt | None
    output: dict | None               # `end.output_map` en modo task (D3)
    rejected_drafts: list[RejectedDraft]   # borradores que M8 rechazó en `respond(generate)` (D3)

def begin_turn(state: RunState, clock: Clock) -> RunState             # reinicia contadores por turno (D4)
def advance(state: RunState, ctx: StepContext, resume: Resume) -> StepOutcome
def start_flow(state: RunState, flow: Flow) -> RunState               # primer nodo = flow.nodes[0]; reinicia node_attempts (D5)

class Projector:                      # puente hacia M7 (rev. 3): vista `model`/`audit` de slots y hechos del run
    def __init__(self, state: RunState, ctx: StepContext)
    def model_value(self, path: Path, *, wrap_slots: bool = False) -> JsonValue
    def audit_value(self, path: Path) -> JsonValue
```

`Projector` se exporta desde la rev. 3 (2026-09-29): el cableado del motor (`agent_core.composition`) lo necesita para armar el `ResponderContext` de M8 con la misma proyección que usa `decide`, en vez de duplicarla.

`DecisionPort` y `ResponderPort` (con `DecisionResult`, `GenerateRequest`, `GenerateResult`) viven en `interpreter/ports.py`: M5 y M8 aún no existen y sus adaptadores los implementarán (D1). M4 debe inyectar **un único `CircuitBreaker` compartido por todo el proceso** (el default por `StepContext` es solo para pruebas; un breaker por turno nunca acumularía fallas). Exporta además `CircuitBreaker`, `evaluate`, `truthy` y `NO_RESUME`.

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

Rutas permitidas en `args`, plantillas y `rule`: `slots.<x>`, `facts.<x>.value[.campo…]`, `decisions.<x>.<campo>` (solo como argumento de tool `compute`, ya validado por G0-10) y literales. La gramática, la distinción ruta/literal y el recorrido recursivo de `args` son los de M1 §3.2 (`parse_path`, `value_paths`); las plantillas usan `{{ ruta }}` (M1 §3.3, `template_vars`). Una ruta inexistente en runtime → resultado `error` del nodo (nunca excepción). En **toda** resolución un slot `claimed` cuenta como ausente (D7). Para `decide`, un slot se proyecta como `untrusted_text` envuelto; para plantillas, vista `model` normal (D8). Nodos sin rama `error` (`confirm`, `respond`, `verify`, `end`) → `escalate(validation_failed)`; `decide` → `low_confidence`; `rule` → `null` (D12).

### 3.3 Handlers

| Nodo | Comportamiento |
|---|---|
| `decide` | `ctx.decisions.decide(model, input_view ⊆ vista model, locale)`. Guarda en `decisions[save_as]`. Resultado = `branch_on` si supera su umbral, si no `low_confidence` (la decisión de umbral la toma M5) |
| `rule` | Evalúa `policy@v.expr` o `expr` con el evaluador JSON Logic sobre `facts.*.value` (vista `full`) y `slots` con `status: validated`. Un slot `claimed` se trata como ausente (`null`). Emite `rule_evaluated {policy@v?, inputs (audit), result}` |
| `collect` | Sin `resume`: emite la plantilla `prompt_ref` y para en `awaiting_slot`. Con `slot_answer`: aplica `validator` (tipo, regex, enum o `decide`); si pasa, `slots[slot] = {value, validated}` → `ok`; si no, `node_attempts += 1` y repregunta; al llegar a `max_attempts` → `max_attempts`. Cada reintento suma a `repair_turns_used` (M4 lo lee) (D15). Validadores (D14): `type` (`string` no vacío, `integer`, `decimal`), `regex` (`re.fullmatch`), `enum` (sin distinguir mayúsculas); `decide` no está soportado y levanta `NotImplementedError` (Abierto) |
| `tool` lectura/`compute` | `ctx.tools.execute(...)` con `bound_params`. `ok` → `facts[save_as] = {value: result_full, source: {tool|compute, ref, inputs}}`. `inputs` = `fact_id`/`decision_id` leídos en `args`. Retries: ≤2 solo si `tool_def.idempotent` y el estado fue `timeout`/`error`. Circuit breaker simple por `tool@v` en el proceso: se abre con 5 fallas en 60 s, se cierra al envejecer y un `ok` las borra; el corte emite `tool_called` con `error="circuit_open"` y `latency_ms=0` (D10). Emite `tool_called` por intento, con `latency_ms` medido con `Clock.monotonic_ns()` alrededor de `execute` (un corte por circuit breaker da `0`) |
| `tool` escritura | Delega en `ctx.actions.execute_write(state, node, action_ctx)` (M3), con el `ActionContext` que arma desde `StepContext`: sus ganchos de plantillas, JSON Logic, `release_view`, el `EventRecorder` de M4 y la vista `audit` de M7 (los eventos los persiste M3; M2 no los duplica). Resultado `ok`/`denied`/`uncertain`, o `step_up_required` (la acción vuelve a `confirmed` y aplica §3.4) |
| `confirm` | Sin `resume`: `ctx.actions.propose(...)` → para en `awaiting_confirmation` con el `ConfirmationPrompt`. Con `confirm_answer`: `ctx.actions.answer(...)` → `yes`/`no`/`unclear`/`max_attempts` |
| `verify` | `ctx.actions.verify(...)` → `verified`/`failed`; guarda el readback en `facts[save_as]` |
| `respond` | `template_ref`: M2 renderiza la plantilla del `locale` con los hechos (vista `model`); emite `response_emitted` con `kind=template` (`validator.ok`, `llm=None`, `claims` de M1; D6 resuelto 2026-09-30); M4 rellena `transcript_fp`. `generate`: `ctx.responder.generate(...)`; en modo degradado usa `fallback_template_ref` sin llamar al modelo. Si `await: true`, avanza el puntero a `next` y para en `awaiting_user` (D16); si no, sigue por `next` |
| `knowledge` | Delega en `ctx.knowledge.read(node, state, KnowledgeContext(release, clock, ids, views, vault, turn_id))` (M12): M12 escribe `RunState.pages` y construye `knowledge_read`; el handler solo entrega el estado nuevo, el evento y la rama (`ok`, `not_found` o `denied`). No usa el modelo, así que el modo degradado no lo impide. Sin `ctx.knowledge` lanza `IllegalTransition` (cableado, como el `AgentPort`) |
| `escalate` | Devuelve `EscalationRequest{reason_code, target_queue = agent.default_target_queue, priority: priority_expr evaluada o "normal"}` (D13); `stop = terminal` |
| `end` | `end_outcome = config.outcome`; aplica `output_map` en modo task; `stop = terminal` |

**Contadores `node_attempts`:** `collect` los limpia al pasar y al salir por `max_attempts`; `confirm`, al resolver `yes`, `no` o `max_attempts`; una `tool` de lectura los limpia en `ok`; una `tool` de escritura, en cualquier resultado distinto de `denied`. Así un flow que vuelve al mismo nodo arranca de cero.

### 3.4 Step-up (ADR 0010)

Si `ToolExecutor` devuelve `step_up_required`, el nodo **no avanza**: emite `step_up_requested`, suma `node_attempts[node]` y para en `awaiting_step_up` con `{required_level, reason}`. El siguiente turno llega con `Resume(step_up_retry)` y reintenta el mismo nodo. Cada `step_up_required` suma un intento y al **superar** `step_up_max_attempts` (2 por defecto; la 3.ª solicitud) → `EscalationRequest(auth_insufficient)` (D9). El contador se limpia al terminar bien el nodo.

### 3.5 Presupuestos

Antes de cada nodo y de cada llamada a modelo se descuenta de `budgets_used`: `max_nodes_per_turn`, `max_model_calls_per_turn`, `max_tokens_per_run`, `max_cost_per_run`, `max_wall_ms_per_turn` (medido con `ctx.clock`). Al agotarse cualquiera → `EscalationRequest(budget_exceeded)`.

### 3.6 Evaluador JSON Logic

Subconjunto cerrado: `JSONLOGIC_OPS` de M1 (`var`, `==`, `!=`, `>`, `>=`, `<`, `<=`, `and`, `or`, `!`, `in`, `if`, `missing`, con su aridad). Un operador fuera de la lista es error de esquema (G0-01), no de runtime. Aritmética con `Decimal`, nunca `float`. `evaluate(expr, data, reads=None)` registra las rutas `var`/`missing` que lee, para `rule_evaluated.inputs` (D11).

### 3.7 Nodo `agent` (ADR 0019; implementado: `handlers/agent.py`)

ReAct acotado de **solo lectura y cálculo**. Lo usan el copiloto del asesor y el agente constructor. M2 solo conoce el puerto `AgentPort`; su adaptador real es `LLMAgentPort` (unidad 5, spec del gateway §3.8).

**Configuración** (M0, m00 §2.5): `tools_allowed`, `max_steps`, `prompt_ref`, `goal`, `save_as` (nombre del hecho donde entra la salida), `output_schema` (JSON Schema de la respuesta final) e `input_view` (rutas `slots.*`/`facts.*` que el modelo ve; vacío por defecto).

**Entradas** (2026-09-30). Antes del primer paso, `input_view` se proyecta **una vez** con la misma función que `decide` (`projection.model_inputs`): vista `model`, slots envueltos como `untrusted_text` (D8) y hechos tokenizados según su origen. Es la vía por la que el modelo ve lo que pidió la persona (un `collect` previo) o lo que ya se leyó. Una ruta que no resuelve (ausente o slot `claimed`, D7) termina el nodo en `gave_up` **sin llamar al modelo**. Las mismas entradas viajan en todos los pasos.

**Puerto.** `AgentPort.step(AgentRequest, state) → AgentStepResult`: un paso del modelo, que devuelve `AgentToolCall(tool, args)` o `AgentFinal(output)` más `model_calls`, `tokens` y `cost_usd`. `AgentRequest` lleva las `inputs`, las `observations` de los pasos previos (en vista `model`) y, tras una salida rechazada, un `feedback` sin datos. `StepContext.agents` lo inyecta; sin él, un nodo `agent` es un error de cableado (`IllegalTransition`). Doble: `testing/fakes/agent.py` (`ScriptedAgent`).

**Bucle.** Hasta `max_steps` pasos. Antes de cada uno se revisan los presupuestos (modelo, tokens, costo y tiempo de pared): agotados → `EscalationRequest(budget_exceeded)`. Cada paso descuenta lo que informa el puerto.
- **Tool.** Se ejecuta solo si está en `tools_allowed` (referencia exacta de la release) y es `read` o `compute`; si no, `access_denied` (`tool_denied`) y el modelo recibe `denied`. Los argumentos que son exactamente un token del run (`⟦tag:n⟧`) vuelven a su valor antes de ejecutar: la tool nunca ve el token y el modelo nunca ve el valor. Usa el circuit breaker como el nodo `tool`. La autorización por llamada es de `ToolExecutor` (ADR 0006). El resultado vuelve al modelo en vista `model`. Un `error`, `timeout` o `denied` vuelve como resultado y cuenta como paso; `step_up_required` se trata como `denied` (no se puede elevar el nivel a mitad del bucle).
- **Respuesta final.** Se valida contra `output_schema` (subconjunto cerrado en `agent_core.domain.schema`, función `check_output`: `type`, `enum`, `properties`, `required`, `additionalProperties` booleano e `items`; una palabra clave fuera de la lista rechaza la salida). Si no cumple, **una** regeneración con el motivo (ruta y regla, nunca el valor); una segunda salida inválida → `gave_up`. La regeneración cuenta como paso.

**Falla del gateway.** Un `GatewayError` de `AgentPort.step` termina el nodo en `gave_up` y carga al presupuesto el uso que informe el error (la llamada cuenta aunque no informe tokens ni costo); cualquier otra excepción sube. Esta ruta no deja evento en el log (ver gateway spec §11).

**Resultados.** `answered`: `facts[save_as] = {value, source: {kind: agent, ref: <id del nodo>, inputs: <fact_id leídos por input_view, luego call_id de las tools del bucle>}}`. `gave_up`: se agotó `max_steps`, no hubo una salida válida, faltó una entrada de `input_view` o el turno está en modo degradado.

**Modo degradado** (spec general §4.1 paso 6): con `injection_flagged` el nodo no llama al modelo y devuelve `gave_up`.

**Presupuestos.** El nodo cuenta como un nodo (`max_nodes_per_turn`).

**Eventos.** Cada tool ejecutada emite su `tool_called` (argumentos y resultado en vista `audit`), y cada paso un `agent_step` (M0): `node_id`, `step`, `kind` (`tool`/`final`/`failed`), `tool`, `call_id` (enlaza con su `tool_called`), `status`, `text_fp` (huella con clave de la respuesta final), `error_kind` (solo con `failed`: el `GatewayErrorKind` que llevó a `gave_up`) y `latency_ms`. Nunca el razonamiento intermedio.

**Determinismo y replay.** El bucle usa solo puertos inyectados: con un `AgentPort` guionado produce los mismos eventos. El replay `audit` verifica la cadena de `agent_step` sin volver a llamar al modelo.

**Límites duros.**
- El nodo no escribe (G0-07 en el flow, y el motor lo repite en runtime).
- Solo escribe en `facts[save_as]`: no toca `slots` ni `decisions`.
- G0-22 impide que esa salida alimente una escritura, una `rule` o un `verify`.
- Los campos de la salida se clasifican por nombre en M7 (origen `agent`); los que no estén clasificados se tokenizan.

**Pruebas:** `tests/m02/test_agent.py` (16, más T-U5-17 para el `GatewayError` y 4 de `input_view`) y `tests/m02/test_output_schema.py`.

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

`node_entered`, `rule_evaluated`, `tool_called` (lectura y `compute`), `step_up_requested`, `access_denied` (`tool_denied`). El `knowledge_read` lo construye M12 y M2 lo agrega a la lista del `StepOutcome`. `decision_made` lo emite M5 y los de acciones M3; M2 los agrega a la lista del `StepOutcome`. Los eventos que devuelve `execute_write` ya los persistió el `EventRecorder` dentro de los commits de M3: no vuelven a agregarse. Para que el orden persistido sea `node_entered…`, `action_confirmed`, `action_dispatched`, `tool_called`, `advance` entrega a los handlers un `StepContext` cuyo `record` envuelve al de M4: antes de cada commit propio de M3 llama `record(uow, state, [*pendientes, *nuevos])` y vacía los pendientes (los eventos acumulados hasta ese momento, incluido el `node_entered` del nodo actual). Lo ya volcado así no se repite en `StepOutcome.events` ni se vuelca dos veces.

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
| T-M2-13 | Circuit breaker: se abre al umbral, se cierra al envejecer las fallas y un `ok` las borra; el corte no reintenta (`test_breaker`, `test_tool`) | — |
| T-M2-14 | Tool `denied` toma la rama `denied` y emite `access_denied` (`test_tool`, `test_write`) | — |
| T-M2-15 | Una excepción de la tool se convierte en rama `error` (`test_tool`) | — |
| T-M2-16 | `respond(generate)`: entrega, cobra presupuestos, respeta `max_model_calls`, reclamos por `derive_claims` y escalamiento del responder (`test_respond_generate`) | — |
| T-M2-17 | `disputa-cargo` con monto alto escala por `policy:escalamiento-disputa-monto` sin escribir nada (`test_disputa_cargo`) | — |
| T-M2-18 | Interfaz pública exacta e importar `agent_core.interpreter` no arrastra guards/handoff/audit/turn/api/registry/adapters (`test_public_api`) | — |
| T-M2-19 | Nodo `knowledge` (M12): sigue por `ok`, `not_found` y `denied`; emite `knowledge_read` después de `node_entered`; fuente caída sale por `not_found`; `navigate` cerrado; sin servicio es error de cableado (`tests/m02/test_knowledge.py`) | — |

Mapeo de archivos: 01 y 11 → `test_disputa_cargo`; 02/03/12 → `test_rule`; 04/09 → `test_tool` y `test_write`; 05 → `test_collect`; 06/07 → `test_tool` y `test_write`; 08 → `test_loop`, `test_budgets`, `test_decide`, `test_respond_generate`; 10 → `test_respond_generate`.

## 8. Evaluación

Aporta, vía `node_entered`: caída por nodo, turnos por run, nodos por turno, tasa de `budget_exceeded`, tasa de step-up por tool.

Vía `tool_called` (lectura y `compute`), por `tool@v`: latencia p50/p95, tasa por `status`, reintentos por llamada, cortes del circuit breaker y llamadas por run.

## 9. Puntos de iteración

- Tipo de nodo nuevo: handler + esquema en M1 + reglas G0.
- Política de retries y circuit breaker: parámetros por `tool_def`, sin cambiar la interfaz.
- El evaluador JSON Logic se puede reemplazar por CEL (riesgo §15) detrás de la misma función `evaluate(expr, data)`.

## 10. Definición de terminado

- [x] Handlers MVP completos (`decide`, `rule`, `collect`, `tool` lectura/compute y escritura, `confirm`, `verify`, `respond`, `escalate`, `end`); `disputa-cargo` corre en el arnés con dobles (T-M2-01).
- [x] T-M2-01…12 en verde (más T-M2-13…18).
- [x] Interfaz pública exportada y tipada; `import-linter`, `mypy` y `ruff` en verde; los eventos se construyen con los modelos de M0 (`Events`); `agentcore contracts --check` sin cambios (M2 no toca M0).
- [x] LOC registradas: `agent_core/interpreter` 1.356; `tests/m02` + `testing/fakes/decision.py` + `testing/fakes/responder.py` 1.524; total 2.880 (sobre la estimación de 1.500–2.000 con pruebas; el paquete solo queda dentro).
- [x] Sin TODO sin issue.
- [x] Handler `knowledge` (rev. 4, M12): T-M2-19 en verde; `StepContext.knowledge` y su cableado en `composition`.

## Decisiones D1–D16

| # | Decisión |
|---|---|
| D1 | M5/M8 no existen: `DecisionPort` y `ResponderPort` locales en `interpreter/ports.py`; se conservan los nombres `decisions` y `responder` |
| D2 | `StepContext` gana `ids`, `vault`, `uow_factory`, `record`, `turn_id`, `breaker` |
| D3 | `Resume.token`; `StepOutcome.output` y `rejected_drafts` |
| D4 | `begin_turn(state, clock)` reinicia contadores por turno (lo llama M4): `turn_nodes`, `turn_model_calls` y, desde la rev. 3, `turn_understand_calls` (que suma M4, no M2) |
| D5 | `start_flow(state, flow)`: entrada = `flow.nodes[0]`; reinicia `node_attempts` |
| D6 | El render de plantillas vive en M2; `ResponderPort` solo `generate`; M2 emite `response_emitted` (`kind=template`) al renderizar, también en modo degradado con `fallback_used=true` (2026-09-30) |
| D7 | Un slot `claimed` cuenta como ausente en toda resolución; ruta ausente → `error` o `escalate(validation_failed)` |
| D8 | Para `decide` un slot se proyecta como `untrusted_text`; para plantillas, vista `model` normal |
| D9 | Step-up: cada `step_up_required` suma un intento; al superar `step_up_max_attempts` → `auth_insufficient` |
| D10 | Breaker: 5 fallas en 60 s por `tool@v`; corte = `tool_called` `error="circuit_open"`; parámetros por constructor |
| D11 | El evaluador registra las rutas que lee (`reads`) para `rule_evaluated.inputs` |
| D12 | Nodos sin rama `error`: `escalate(validation_failed)`; `decide` → `low_confidence`; `rule` → `null` |
| D13 | Escalamientos del motor: cola por defecto del agente y prioridad `normal`; `priority_expr` no string → `normal` |
| D14 | Validadores de `collect`: `type`, `regex` (`fullmatch`), `enum` (sin mayúsculas); `decide` pendiente: M1 lo rechaza en G0-01 (2026-09-30) para que ninguna release llegue a ejecutarlo |
| D15 | `repair_turns_used` lo suma M2 (`collect`); M4 lo lee (y suma en `confirm`) |
| D16 | `respond(await)` avanza el puntero antes de parar en `awaiting_user` |

## 11. Abiertos

- ~~Validador `decide` de `collect`~~ **Decidido 2026-09-30 (tema #15):** no se soporta hasta la fase 2; M1 lo rechaza en G0-01 y `NotImplementedError` queda como defensa (D14). Contrato previsto: el `decision_model` declara un campo calibrado `valid` (booleano); el slot se acepta si es verdadero y supera el umbral, y si no cuenta como intento fallido.
- Quién llena `open_questions` (índice §10).
- **Nodo `agent` (§3.7, ADR 0019):**
  - ~~Falta el **adaptador real de `AgentPort`**~~ **Resuelto 2026-09-30:** `LLMAgentPort` (unidad 5), en `docs/specs/2026-09-28-llm-gateway-design.md` §3.8. El texto del prompt del bucle vive en el registro de la demo (gateway spec §11).
  - No se aplica la regla numérica de M8 a la salida: M2 no puede importar M8. La salida solo llega a la persona por un `respond`, que tiene su propio validador, y G0-22 impide que decida o escriba.
  - Un `output_schema` con palabras clave fuera del subconjunto rechaza toda salida en runtime; falta una regla de M1 que lo detecte al validar el flow.
  - Falta decidir si la salida debe marcarse como `untrusted` cuando el bucle leyó campos `untrusted_text`.
- Parámetros del circuit breaker por `tool_def` (hoy por constructor, D10).
- Reinicio de `node_attempts` al terminar un flow (hoy solo lo reinicia `start_flow`).
- (a) Step-up de escritura: M3 `execute_write` devuelve solo `"step_up_required"`, sin el nivel requerido; M2 usa `min_auth_level` de la tool. Requiere que M3 lo transporte.
- (b) `StepOutcome.output` es la vista completa: M4/M9 deben pasarlo por M7. Un `priority_expr` que lea hechos podría meter datos completos en `EscalationRequest.priority`.
- (c) Verificar que el hecho de readback de `verify` se proyecta con el `source`/`untrusted_fields` de la tool de escritura (M3 `FactSource` no tiene referencia al readback).
- (d) `GenerateRequest` no lleva el presupuesto restante de llamadas a modelo (nota para M8).
- (e) `step_up_requested` también se emite en la solicitud (max+1)-ésima, la que escala.
- (f) Seguimiento de G0: compilar la regex de `collect` en validación de flows.
