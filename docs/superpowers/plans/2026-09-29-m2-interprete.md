# M2 — Intérprete de nodos: plan de implementación

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Construir `agent_core.interpreter` (M2): dado un `RunState` con un flow activo, ejecuta nodos hasta uno que **espera al principal** o uno **terminal**, y devuelve el estado nuevo, los mensajes y los eventos.

**Architecture:**
- Un bucle (`advance`) recorre el flow y delega en un registro `HANDLERS: dict[str, NodeHandler]`, un handler por tipo de nodo. Agregar un tipo de nodo es agregar un handler.
- M2 no hace I/O fuera de `StepContext`. Habla con M3 (`ActionManager`), M5 (decisiones), M7 (`ViewService`) y M8 (generación) a través de sus interfaces. M5 y M8 **aún no existen**: M2 define sus puertos mínimos (`DecisionPort`, `ResponderPort`) y los dobles guionados viven en `testing/fakes/`.
- El evaluador de JSON Logic, la resolución de rutas y el render de plantillas son funciones puras dentro del paquete. El evaluador usa `Decimal`; los hechos guardan la vista `full` y todo lo que sale a modelos, eventos o mensajes pasa por `ctx.views`.

**Tech Stack:** Python 3.12, Pydantic v2, pytest, ruff, mypy strict, import-linter. **No hay dependencias nuevas.**

**Spec:** `docs/specs/motor/m02-interprete.md`. Este plan la lleva a la **rev. 2** con las decisiones de abajo. Lee también:
- `docs/specs/motor/00-indice.md` §3, §5 y §6;
- `docs/specs/motor/m01-validacion-estatica.md` §3.2, §3.3, §3.6, §3.10;
- `docs/specs/motor/m03-acciones.md` (M2 arma el `ActionContext` y aplica su §3.4 de step-up);
- `docs/specs/motor/m05-decision-model.md` §2 y `m08-validador-de-respuesta.md` §2 (los puertos que M2 reproduce);
- ADR 0004, 0009, 0010, 0011.

## Decisiones que hay que confirmar antes de la Task 1

El spec de M2 es ambiguo o incompleto en los puntos de abajo (CLAUDE.md: "si el spec es ambiguo, detente y pregunta"). El plan **asume la opción recomendada** de cada fila; si el usuario elige otra, cambia solo la task que se indica.

| # | Tema | Problema en el spec | Recomendación que asume el plan | Task |
|---|---|---|---|---|
| D1 | M5 y M8 no existen (fase 4) | `StepContext` pide `DecisionService` y `Responder`, pero `agent_core.decision` y `agent_core.response` están vacíos; la firma de `Responder.generate(node_config, state, ctx)` no define `ctx` | M2 define `DecisionPort` y `ResponderPort` en `interpreter/ports.py` (tipos de resultado como dataclasses). Los campos de `StepContext` conservan los nombres `decisions` y `responder`. M5/M8 los adaptan cuando existan | 1 |
| D2 | `StepContext` incompleto | Falta lo que M2 necesita para armar el `ActionContext` de M3 y para IDs deterministas | `StepContext` gana `ids`, `vault: TokenVault` (M7), `uow_factory`, `record: EventRecorder = append_events`, `turn_id` y `breaker: CircuitBreaker` (el spec §11 ya anticipa `ids`) | 1 |
| D3 | `Resume` y `StepOutcome` incompletos | `answer` de M3 exige el token del botón; `end.output_map` y los borradores rechazados de M8 no tienen dónde salir | `Resume` gana `token: str \| None`; `StepOutcome` gana `output: dict \| None` y `rejected_drafts: list[RejectedDraft]` | 1 |
| D4 | Reinicio de contadores por turno | Nadie dice quién pone en cero `turn_nodes`, `turn_model_calls` y fija `turn_started_at` | M2 exporta `begin_turn(state, clock) -> RunState`; M4 (y el arnés) lo llama al inicio de cada turno | 4 |
| D5 | `start_flow` sin registro | `start_flow(state, flow_ref)` no puede saber el primer nodo | `start_flow(state, flow: Flow)`: el nodo de entrada es `flow.nodes[0]` y reinicia `node_attempts` | 6 |
| D6 | Plantillas | El spec entrega el render a M8 (`Responder.template`), pero M3 necesita `render` ya (`ActionContext.render`) y M8 no existe | El render de `{{ ruta }}` vive en M2 (vista `model` de los hechos). `ResponderPort` solo tiene `generate`. En la fase 1 M2 **no** emite `response_emitted` (lo hará M8/M4 al cablearse) | 5, 12 |
| D7 | Slots `claimed` | Solo se dice que `rule` los trata como `null` | En **toda** resolución de M2 (args, plantillas, `decide`) un slot `claimed` cuenta como ausente. `args` o plantilla con ruta ausente → rama `error` (tool) o `escalate(validation_failed)` (nodos sin rama `error`) | 3 |
| D8 | Vista `model` de un slot | Un slot es texto libre del usuario; sin clasificar, M7 lo tokeniza y `decide` no podría clasificar un token | Para `decide` un slot se proyecta como `untrusted_text` (envuelto). Para plantillas, vista `model` normal (tokenizada; `views.render` la resuelve al entregar) | 5, 10 |
| D9 | Step-up agotado | "Al pasar `max_attempts`" no dice si `>` o `>=` | Cada `step_up_required` suma un intento; al **superar** `step_up_max_attempts` (2 por defecto → la 3.ª solicitud) escala `auth_insufficient`. El contador se limpia al terminar bien el nodo | 9 |
| D10 | Circuit breaker | "N fallas en ventana" sin valores ni cierre | Se abre con 5 fallas (`timeout`/`error`) en 60 s por `tool@v`; se cierra solo al envejecer las fallas; un `ok` las borra. Corte = `tool_called` con `status=error`, `error="circuit_open"`, `latency_ms=0`. Parámetros por constructor (hoy no por `tool_def`) | 1, 9 |
| D11 | Rutas de la expresión para `rule_evaluated.inputs` | `expr_paths` es interno de M1 y M2 solo importa la interfaz pública | El evaluador de M2 registra las rutas `var`/`missing` que **lee** (`reads`); no se toca la interfaz de M1 | 2, 7 |
| D12 | Nodos sin rama `error` | `confirm`, `respond`, `verify` y `end` no declaran `error`, pero "ruta inexistente → error del nodo" | Ruta inexistente en esos nodos → `escalate(validation_failed)`. `decide` → `low_confidence`. `rule` → `null` | 6–11 |
| D13 | Escalamientos que origina el motor | `EscalationRequest` exige `priority` y `target_queue` | `target_queue = agent.default_target_queue`; `priority = "normal"` (constante `DEFAULT_PRIORITY`). En `escalate`, `priority_expr` que no dé un string no vacío cae en `"normal"` | 6 |
| D14 | Validador de `collect` | `SlotValidator.kind` = `type\|regex\|enum\|decide` sin semántica | `type`: `string` (no vacío), `integer`, `decimal`. `regex`: `re.fullmatch`. `enum`: lista de strings, sin distinguir mayúsculas. **`decide` queda fuera** (levanta `NotImplementedError`; Abierto de M2) porque no se sabe qué campo de la decisión valida | 8 |
| D15 | `repair_turns_used` | El índice §5 dice que lo escribe M4; M2 §3.3 dice que M2 lo suma en cada reintento de `collect` | Se sigue el spec de M2 (suma M2) y se corrige el índice §5 en la Task 13 | 8, 13 |
| D16 | Reanudación de `respond(await: true)` | El spec no dice dónde queda el puntero | El handler avanza el puntero a `next["next"]` **antes** de parar en `awaiting_user`; el siguiente `advance` entra al nodo siguiente | 6 |

## Global Constraints

- **Stack:** Python `>=3.12,<3.13`, Pydantic v2. **No se agregan dependencias**, así que `pyproject.toml` no se toca.
- **Fronteras:** `agent_core.interpreter` puede importar `agent_core.domain`, `agent_core.ports` y la interfaz pública de `flows`, `actions`, `decision`, `views`, `response` y `knowledge` (`.importlinter`, contrato `interpreter`). Nunca importa `guards`, `handoff`, `audit`, `turn`, `api`, `adapters`, `cli`, `contracts` ni `registry`. `agent_core` nunca importa `testing` ni `tests`. **Nunca importes módulos internos de otro paquete** (p. ej. `agent_core.flows.jsonlogic`): solo su `__init__`.
- **Tiempo e IDs:** nunca `datetime.now()`, `time.*`, `uuid4()`, `random` ni `secrets`. Instantes y presupuesto de tiempo salen de `ctx.clock.now()`; latencias, de `ctx.clock.monotonic_ns()`; IDs (`fact_id`, `call_id`, `event_id`), de `ctx.ids`.
- **Dinero y cifras:** `Decimal`, nunca `float`. Todo JSON entra con `agent_core.domain.loads`; toda canonización usa `canonical_bytes`.
- **Datos:** solo sintéticos. Nunca datos reales del dataset ni las credenciales AWS del diccionario de datos.
- **PII:** los hechos guardan la vista `full`; **nada de `full` sale a modelos, eventos ni mensajes** sin pasar por `ctx.views` (`project(...).model` o `.audit`).
- **Escrituras:** solo por `ctx.actions.execute_write`. M2 nunca llama a una tool `write_*` (ni `risk_class` de escritura) con `ctx.tools.execute`.
- **Sin excepciones para el flujo normal:** una ruta inexistente, una tool caída o un validador que falla son ramas del flow, no excepciones. Solo los bugs (nodo sin handler, transición sin `next`) lanzan `IllegalTransition`.
- **Comandos:** `uv run pytest tests/m02`, `uv run pytest`, `uv run ruff check .`, `uv run mypy`, `uv run lint-imports`, `uv run agentcore contracts --check`.
- **Commits:** mensajes en español que terminan con `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`. Si el ejecutor es otro modelo, pone su nombre real.

## Estructura de archivos

```
agent_core/interpreter/
  __init__.py          interfaz pública
  context.py           StepContext, Resume, Stop, StepOutcome, NO_RESUME
  ports.py             DecisionPort, DecisionResult, ResponderPort, GenerateRequest, GenerateResult
  breaker.py           CircuitBreaker
  events.py            Events: node_entered, rule_evaluated, tool_called, step_up_requested, access_denied
  jsonlogic.py         evaluate(expr, data, reads), truthy
  resolve.py           MissingPath, resolve_path, resolve_value, resolve_args, input_ids, render_template, walk
  refs.py              exact_ref, make_resolver
  projection.py        Projector: vistas model/audit de slots y hechos
  templates.py         render_message
  audit.py             ViewsAudit (AuditProjector de M3), rule_inputs
  budgets.py           begin_turn, enter_node, model_budget_exhausted, charge_model
  calls.py             tool_call_context, build_action_context
  loop.py              advance, start_flow
  handlers/
    __init__.py        HANDLERS
    base.py            NodeResult, NodeHandler, escalate_now, request_step_up, clear_attempts
    terminal.py        handle_escalate, handle_end
    respond.py         handle_respond
    rule.py            handle_rule
    collect.py         handle_collect
    tool.py            handle_tool (lectura y compute)
    decide.py          handle_decide
    write.py           handle_write, handle_confirm, handle_verify
testing/fakes/decision.py    ScriptedDecision, make_decision
testing/fakes/responder.py   ScriptedResponder
tests/m02/...                una prueba por módulo + harness.py + test_disputa_cargo.py
```

## Trabajo en paralelo

- **Worktree propio:** `../agent-core-m2`, rama `feat/m2-interprete`, desde `main` (Task 0). No trabajes en el worktree principal.
- **Archivos que M2 puede tocar:** `agent_core/interpreter/**`, `testing/fakes/decision.py`, `testing/fakes/responder.py`, `tests/m02/**` y, en la Task 13, `docs/specs/motor/m02-interprete.md` y `docs/specs/motor/00-indice.md` (§5).
- **No tocar:** M0 (`agent_core/domain`, `agent_core/ports`), M1, M3, M7 ni `contracts/`. Si algo falta en su interfaz, detente y pregunta.

---

### Task 0: Worktree y línea base

**Files:** ninguno.

- [ ] **Step 1: Crear el worktree**

Run:
```bash
cd C:/Users/USUARIO/Documents/factored/agent-core
git worktree add ../agent-core-m2 -b feat/m2-interprete main
cd ../agent-core-m2
uv sync
```
Expected: rama `feat/m2-interprete` creada.

- [ ] **Step 2: Comprobar que la línea base está verde**

Run: `uv run pytest -q && uv run lint-imports && uv run mypy && uv run ruff check .`
Expected: todo en verde. Si algo falla en `main`, detente y avisa: no es de M2.

---

### Task 1: Contratos, puertos, breaker, eventos y arnés

**Files:**
- Create: `agent_core/interpreter/context.py`, `ports.py`, `breaker.py`, `events.py`
- Modify: `agent_core/interpreter/__init__.py`
- Create: `testing/fakes/decision.py`, `testing/fakes/responder.py`
- Create: `tests/m02/__init__.py`, `tests/m02/harness.py`, `tests/m02/test_events.py`, `tests/m02/test_breaker.py`

**Interfaces:**
- Consumes: M0 (`RunState`, `Release`, `Agent`, `EngineEvent`, payloads, `Decision`, `Message`, `RejectedDraft`, `EscalationRequest`), `ActionManager`, `EventRecorder`, `append_events` (M3), `ViewService`, `TokenVault` (M7), puertos de M0.
- Produces:
  - `StepContext`, `Resume(kind, value, token)`, `NO_RESUME`, `Stop`, `StepOutcome` (campos en Step 3).
  - `DecisionPort.decide(model: EntityRef, inputs_model_view: dict[str, JsonValue], locale: Locale) -> DecisionResult`; `ResponderPort.generate(request: GenerateRequest, state: RunState) -> GenerateResult`.
  - `CircuitBreaker(threshold=5, window=timedelta(seconds=60))` con `is_open(tool, now)`, `record_failure(tool, now)`, `record_success(tool)`.
  - `Events(ctx)` con `node_entered(state, flow, node_id, node_type, resume_kind)`, `rule_evaluated(state, node_id, policy, inputs, result)`, `tool_called(state, payload)`, `step_up_requested(state, node_id, required_level, attempt)`, `access_denied(state, tool)`.
  - Doubles: `ScriptedDecision(script)`, `make_decision(...)`, `ScriptedResponder(script)`.
  - Arnés `World` (ver Step 5) que usan todas las tasks siguientes.

- [ ] **Step 1: Escribir las pruebas que fallan**

`tests/m02/__init__.py` (vacío).

`tests/m02/test_breaker.py`:
```python
from datetime import timedelta

from agent_core.domain import EntityRef
from agent_core.interpreter import CircuitBreaker
from testing.builders import NOW

TOOL = EntityRef.parse("buscar@1.0.0")
OTHER = EntityRef.parse("otra@1.0.0")


def test_opens_at_threshold_and_closes_when_failures_age_out() -> None:
    breaker = CircuitBreaker(threshold=2, window=timedelta(seconds=60))
    breaker.record_failure(TOOL, NOW)
    assert not breaker.is_open(TOOL, NOW)
    breaker.record_failure(TOOL, NOW + timedelta(seconds=10))
    assert breaker.is_open(TOOL, NOW + timedelta(seconds=11))
    assert not breaker.is_open(OTHER, NOW + timedelta(seconds=11))  # por tool
    assert not breaker.is_open(TOOL, NOW + timedelta(seconds=71))  # la primera falla ya salió de la ventana


def test_success_clears_failures() -> None:
    breaker = CircuitBreaker(threshold=2)
    breaker.record_failure(TOOL, NOW)
    breaker.record_success(TOOL)
    breaker.record_failure(TOOL, NOW)
    assert not breaker.is_open(TOOL, NOW)
```

`tests/m02/test_events.py`:
```python
from agent_core.domain import AuthLevel, EntityRef
from agent_core.interpreter.events import Events
from testing.builders import NOW
from tests.m02.harness import World, flow


def test_envelope_and_payloads() -> None:
    w = World()
    state = w.state(flow({"id": "a", "type": "end", "config": {"outcome": "resolved"}}))
    events = Events(w.ctx(turn_id="turn-0007"))
    entered = events.node_entered(state, EntityRef.parse("f@1.0.0"), "a", "end", "none")
    assert (entered.type, entered.run_id, entered.turn_id, entered.ts) == ("node_entered", "run-0001", "turn-0007", NOW)
    assert entered.payload.node_type == "end" and entered.payload.resume_kind == "none"
    assert entered.event_id != events.access_denied(state, EntityRef.parse("t@1.0.0")).event_id
    up = events.step_up_requested(state, "a", AuthLevel.step_up, 1)
    assert up.payload.required_level is AuthLevel.step_up and up.payload.attempt == 1
    rule = events.rule_evaluated(state, "a", None, {"facts.x.value": "***"}, True)
    assert rule.payload.result is True and rule.payload.policy is None
    assert events.access_denied(state, EntityRef.parse("t@1.0.0")).payload.reason.value == "tool_denied"
```

- [ ] **Step 2: Correr las pruebas y confirmar que fallan**

Run: `uv run pytest tests/m02 -q`
Expected: FAIL (`ImportError`: `CircuitBreaker`, `harness`).

- [ ] **Step 3: Implementar contratos y piezas**

`agent_core/interpreter/ports.py`:
```python
"""Puertos de M2 hacia M5 (`decide`) y M8 (`respond(generate)`) mientras esos módulos no existen (D1).

M5 y M8 los adaptarán a su interfaz real; las firmas reproducen m05 §2 y m08 §2 en lo que M2 necesita."""

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Protocol

from agent_core.domain import (
    Decision,
    EngineEvent,
    EntityRef,
    EscalationRequest,
    GenerateConfig,
    JsonValue,
    Locale,
    Message,
    NodeId,
    RejectedDraft,
    RunState,
)


@dataclass(frozen=True)
class DecisionResult:
    """Salida de `decide`: la decisión guardable, qué campos superan su umbral y su costo."""

    decision: Decision
    above_threshold: dict[str, bool]
    events: list[EngineEvent] = field(default_factory=list)  # `decision_made`, lo emite M5
    model_calls: int = 1
    tokens: int = 0
    cost_usd: Decimal = Decimal("0")


class DecisionPort(Protocol):
    """Recibe solo la vista `model` (M5 §3.1)."""

    def decide(self, model: EntityRef, inputs_model_view: dict[str, JsonValue],
               locale: Locale) -> DecisionResult: ...


@dataclass(frozen=True)
class GenerateRequest:
    """Nodo `respond(generate)` que M8 debe resolver; `claims` sale de `derive_claims` (M1)."""

    node_id: NodeId
    config: GenerateConfig
    claims: frozenset[str]


@dataclass(frozen=True)
class GenerateResult:
    """Un mensaje o una solicitud de escalamiento (`validation_failed`), más el uso del modelo."""

    message: Message | None = None
    escalation: EscalationRequest | None = None
    rejected: list[RejectedDraft] = field(default_factory=list)
    events: list[EngineEvent] = field(default_factory=list)
    model_calls: int = 0
    tokens: int = 0
    cost_usd: Decimal = Decimal("0")


class ResponderPort(Protocol):
    """Generar → validar → regenerar → plantilla de respaldo (M8 §3.2)."""

    def generate(self, request: GenerateRequest, state: RunState) -> GenerateResult: ...
```

`agent_core/interpreter/breaker.py`:
```python
"""Circuit breaker simple por tool (M2 §3.3, D10). Sin reloj propio: recibe `now` del `Clock` del contexto."""

from collections import deque
from datetime import datetime, timedelta

from agent_core.domain import EntityRef


class CircuitBreaker:
    """Abierto con `threshold` fallas dentro de `window`. Se cierra solo cuando las fallas envejecen."""

    def __init__(self, threshold: int = 5, window: timedelta = timedelta(seconds=60)) -> None:
        if threshold < 1 or window <= timedelta(0):
            raise ValueError("threshold >= 1 y window positiva")
        self._threshold = threshold
        self._window = window
        self._failures: dict[EntityRef, deque[datetime]] = {}

    def _recent(self, tool: EntityRef, now: datetime) -> deque[datetime]:
        failures = self._failures.setdefault(tool, deque())
        while failures and now - failures[0] >= self._window:
            failures.popleft()
        return failures

    def is_open(self, tool: EntityRef, now: datetime) -> bool:
        return len(self._recent(tool, now)) >= self._threshold

    def record_failure(self, tool: EntityRef, now: datetime) -> None:
        self._recent(tool, now).append(now)

    def record_success(self, tool: EntityRef) -> None:
        self._failures.pop(tool, None)
```

`agent_core/interpreter/context.py`:
```python
"""Interfaz pública de M2: contexto, reanudación y resultado de `advance` (M2 §2, D2, D3)."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Literal

from agent_core.actions import ActionManager, EventRecorder, append_events
from agent_core.domain import (
    Agent,
    ConfirmationPrompt,
    EngineEvent,
    EscalationRequest,
    JsonValue,
    Locale,
    Message,
    Outcome,
    RejectedDraft,
    Release,
    RunState,
    StepUpPrompt,
)
from agent_core.interpreter.breaker import CircuitBreaker
from agent_core.interpreter.ports import DecisionPort, ResponderPort
from agent_core.ports import Clock, IdSource, RegistryPort, ToolExecutor, UnitOfWorkFactory
from agent_core.views import TokenVault, ViewService

ResumeKind = Literal["slot_answer", "confirm_answer", "step_up_retry", "none"]


class Stop(StrEnum):
    """Por qué se detiene `advance`. M4 lo traduce a `RunState.awaiting`."""

    awaiting_slot = "awaiting_slot"
    awaiting_confirmation = "awaiting_confirmation"
    awaiting_step_up = "awaiting_step_up"
    awaiting_user = "awaiting_user"
    terminal = "terminal"


@dataclass(frozen=True)
class Resume:
    """Por qué se reanuda el nodo actual. `value`: texto del slot, o "yes"|"no"|"unclear" para confirm."""

    kind: ResumeKind = "none"
    value: JsonValue = None
    token: str | None = None  # token de confirmación del botón (M3 `answer`)


NO_RESUME = Resume()


@dataclass(frozen=True)
class StepContext:
    """Todo lo que M2 necesita del exterior: sin esto, `advance` no hace I/O (M2 §4)."""

    release: Release
    agent: Agent
    locale: Locale
    clock: Clock
    degraded: bool
    registry: RegistryPort
    tools: ToolExecutor
    decisions: DecisionPort
    actions: ActionManager
    responder: ResponderPort
    views: ViewService
    vault: TokenVault
    ids: IdSource
    uow_factory: UnitOfWorkFactory
    bound_params: Mapping[str, str] = field(default_factory=dict)
    record: EventRecorder = append_events
    turn_id: str | None = None
    breaker: CircuitBreaker = field(default_factory=CircuitBreaker)


@dataclass(frozen=True)
class StepOutcome:
    """Resultado de `advance`. Los eventos de `execute_write` ya están persistidos y no vuelven aquí."""

    state: RunState
    stop: Stop
    messages: list[Message] = field(default_factory=list)
    events: list[EngineEvent] = field(default_factory=list)
    end_outcome: Outcome | None = None
    escalation: EscalationRequest | None = None
    confirmation: ConfirmationPrompt | None = None
    step_up: StepUpPrompt | None = None
    output: dict[str, JsonValue] | None = None  # `end.output_map` en modo task
    rejected_drafts: list[RejectedDraft] = field(default_factory=list)
```

`agent_core/interpreter/events.py`:
```python
"""Eventos que emite M2 (índice §6; payloads en M0 §2.10). `seq`/`hash` los asigna M11."""

from agent_core.domain import (
    AccessDenied,
    AccessDeniedPayload,
    AccessDeniedReason,
    AuthLevel,
    EntityRef,
    JsonValue,
    NodeEntered,
    NodeEnteredPayload,
    RuleEvaluated,
    RuleEvaluatedPayload,
    RunState,
    StepUpRequested,
    StepUpRequestedPayload,
    ToolCalled,
    ToolCalledPayload,
)
from agent_core.interpreter.context import ResumeKind, StepContext
from agent_core.ports import IdKind


class Events:
    """Arma la envoltura común con los puertos del contexto."""

    def __init__(self, ctx: StepContext) -> None:
        self._ctx = ctx

    def _envelope(self, state: RunState) -> dict[str, object]:
        return {
            "event_id": self._ctx.ids.new_id(IdKind.event),
            "run_id": state.run_id,
            "turn_id": self._ctx.turn_id,
            "session_id": state.session_id,
            "release": state.release,
            "ts": self._ctx.clock.now(),
        }

    def node_entered(self, state: RunState, flow: EntityRef, node_id: str, node_type: str,
                     resume_kind: ResumeKind) -> NodeEntered:
        payload = NodeEnteredPayload(flow=flow, node_id=node_id, node_type=node_type, resume_kind=resume_kind)
        return NodeEntered.model_validate({**self._envelope(state), "payload": payload})

    def rule_evaluated(self, state: RunState, node_id: str, policy: EntityRef | None,
                       inputs: dict[str, JsonValue], result: bool) -> RuleEvaluated:
        payload = RuleEvaluatedPayload(node_id=node_id, policy=policy, inputs=inputs, result=result)
        return RuleEvaluated.model_validate({**self._envelope(state), "payload": payload})

    def tool_called(self, state: RunState, payload: ToolCalledPayload) -> ToolCalled:
        return ToolCalled.model_validate({**self._envelope(state), "payload": payload})

    def step_up_requested(self, state: RunState, node_id: str, required_level: AuthLevel,
                          attempt: int) -> StepUpRequested:
        payload = StepUpRequestedPayload(node_id=node_id, required_level=required_level, attempt=attempt)
        return StepUpRequested.model_validate({**self._envelope(state), "payload": payload})

    def access_denied(self, state: RunState, tool: EntityRef) -> AccessDenied:
        payload = AccessDeniedPayload(reason=AccessDeniedReason.tool_denied, tool=tool)
        return AccessDenied.model_validate({**self._envelope(state), "payload": payload})
```

`agent_core/interpreter/__init__.py`:
```python
"""M2 — intérprete de nodos (docs/specs/motor/m02-interprete.md). Interfaz pública."""

from agent_core.interpreter.breaker import CircuitBreaker
from agent_core.interpreter.context import NO_RESUME, Resume, StepContext, StepOutcome, Stop
from agent_core.interpreter.ports import (
    DecisionPort,
    DecisionResult,
    GenerateRequest,
    GenerateResult,
    ResponderPort,
)

__all__ = [
    "NO_RESUME", "CircuitBreaker", "DecisionPort", "DecisionResult", "GenerateRequest", "GenerateResult",
    "ResponderPort", "Resume", "StepContext", "StepOutcome", "Stop",
]
```

- [ ] **Step 4: Escribir los dobles**

`testing/fakes/decision.py`:
```python
"""Doble de `DecisionPort` (M2): resultados guionados en orden. No decide nada."""

from collections import deque
from collections.abc import Sequence
from copy import deepcopy
from decimal import Decimal
from typing import TYPE_CHECKING

from agent_core.domain import Decision, EntityRef, JsonValue, Locale
from agent_core.interpreter import DecisionResult


def make_decision(value: dict[str, JsonValue], above: dict[str, bool], *, decision_id: str = "decision-0001",
                  tokens: int = 0, cost: str = "0") -> DecisionResult:
    """Decisión sintética: `p_cal` alto en los campos sobre el umbral y bajo en los demás."""
    decision = Decision(
        decision_id=decision_id, value=value,
        p_cal={field: (0.95 if flag else 0.2) for field, flag in above.items()},
        provider_used="scripted", model_version="scripted-1",
    )
    return DecisionResult(decision=decision, above_threshold=dict(above), tokens=tokens, cost_usd=Decimal(cost))


class ScriptedDecision:
    def __init__(self, script: Sequence[DecisionResult] = ()) -> None:
        self._script = deque(script)
        self.calls: list[tuple[EntityRef, dict[str, JsonValue], str]] = []

    def push(self, *results: DecisionResult) -> None:
        self._script.extend(results)

    def decide(self, model: EntityRef, inputs_model_view: dict[str, JsonValue], locale: Locale) -> DecisionResult:
        self.calls.append((model, deepcopy(inputs_model_view), locale))
        if not self._script:
            raise AssertionError("ScriptedDecision sin resultado guionado")
        return self._script.popleft()


if TYPE_CHECKING:
    from agent_core.interpreter import DecisionPort

    def _conforms(x: ScriptedDecision) -> DecisionPort:
        return x
```

`testing/fakes/responder.py`:
```python
"""Doble de `ResponderPort` (M2): resultados de `generate` guionados en orden."""

from collections import deque
from collections.abc import Sequence
from typing import TYPE_CHECKING

from agent_core.domain import RunState
from agent_core.interpreter import GenerateRequest, GenerateResult


class ScriptedResponder:
    def __init__(self, script: Sequence[GenerateResult] = ()) -> None:
        self._script = deque(script)
        self.calls: list[GenerateRequest] = []

    def push(self, *results: GenerateResult) -> None:
        self._script.extend(results)

    def generate(self, request: GenerateRequest, state: RunState) -> GenerateResult:
        self.calls.append(request)
        if not self._script:
            raise AssertionError("ScriptedResponder sin resultado guionado")
        return self._script.popleft()


if TYPE_CHECKING:
    from agent_core.interpreter import ResponderPort

    def _conforms(x: ScriptedResponder) -> ResponderPort:
        return x
```

- [ ] **Step 5: Escribir el arnés (lo usan todas las tasks)**

`tests/m02/harness.py`:
```python
"""Arnés de M2: registro, tools, decisiones, responder y contexto sintéticos. Solo datos sintéticos."""

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from agent_core.actions import ActionManager
from agent_core.domain import (
    ENTITY_KIND,
    Agent,
    EntityKind,
    EntityRef,
    Fact,
    Flow,
    JsonValue,
    RegistryEntity,
    Release,
    RunState,
    Slot,
    Template,
    ToolDef,
)
from agent_core.interpreter import CircuitBreaker, Resume, StepContext, StepOutcome
from agent_core.views import DEFAULT_CATALOG, FieldClassifier, FieldRule, TokenVault, ViewService
from testing.builders import NOW, run_state
from testing.fakes.clock import FakeClock
from testing.fakes.decision import ScriptedDecision
from testing.fakes.ids import FakeIds
from testing.fakes.keys import FakeKeyProvider
from testing.fakes.registry import InMemoryRegistry
from testing.fakes.responder import ScriptedResponder
from testing.fakes.storage import InMemoryStore
from testing.fakes.tools import FakeToolExecutor, Handler, Scripted

RUN_ID = "run-0001"
RELEASE_ID = "rel-2026-09-28"

CATALOG = {
    **DEFAULT_CATALOG,
    "amount": FieldRule(field_class="financial"),
    "currency": FieldRule(field_class="public"),
    "status": FieldRule(field_class="public"),
    "id": FieldRule(field_class="public"),
}

AGENT = Agent.model_validate({
    "id": "atencion", "version": "1.0.0", "mode": "conversational", "entry_flow": "f@1",
    "invocable_by": ["customer"], "min_auth_level": "session", "subject_kinds": ["customer"],
    "supported_locales": ["es", "pt"], "default_locale": "es",
    "budgets": {"max_nodes_per_turn": 40, "max_model_calls_per_turn": 3, "max_tokens_per_run": 20000,
                "max_cost_per_run": "0.50", "max_wall_ms_per_turn": 8000},
    "templates": {k: "t/x@1" for k in ("clarify", "abstain", "handoff", "pending_ack", "pending_offer",
                                       "unsupported_language", "input_too_large")},
    "max_clarifications": 2, "on_clarify_exhausted": "escalate", "default_target_queue": "general",
})


class _Authz:
    """Solo `can_read_field`: es lo único que llama `ViewService`."""

    def can_read_field(self, *_: Any) -> bool:
        return True


def flow(*nodes: dict[str, Any], id: str = "f") -> Flow:
    return Flow.model_validate({"id": id, "version": "1.0.0", "priority": 1, "nodes": list(nodes)})


def template(id: str, es: str, pt: str | None = None) -> Template:
    return Template.model_validate({"id": id, "version": "1.0.0", "locales": {"es": es, "pt": pt or es}})


def tool_def(id: str, risk: str = "read", *, idempotent: bool = True, level: str = "session",
             source: str | None = None) -> ToolDef:
    data: dict[str, Any] = {"id": id, "version": "1.0.0", "risk_class": risk, "min_auth_level": level,
                            "idempotent": idempotent, "source": source}
    if risk.startswith("write") or risk == "money_movement":
        data["readback_by"] = "idempotency_key"
    return ToolDef.model_validate(data)


def fact(value: JsonValue, *, fact_id: str = "fact-0001", ref: str = "t@1.0.0", kind: str = "tool") -> Fact:
    return Fact.model_validate({"fact_id": fact_id, "value": value,
                                "source": {"kind": kind, "ref": ref}, "ts": NOW})


def slot(value: JsonValue, status: str = "validated") -> Slot:
    return Slot.model_validate({"value": value, "status": status, "source_turn": 1})


class SlowTools:
    """Cada llamada avanza el reloj `delta` (para medir `latency_ms`)."""

    def __init__(self, inner: FakeToolExecutor, clock: FakeClock, delta: timedelta) -> None:
        self.inner, self.clock, self.delta = inner, clock, delta

    def definition(self, tool: EntityRef) -> ToolDef:
        return self.inner.definition(tool)

    def execute(self, *args: Any, **kwargs: Any) -> Any:
        self.clock.advance(self.delta)
        return self.inner.execute(*args, **kwargs)


@dataclass
class World:
    agent: Agent = AGENT
    clock: FakeClock = field(default_factory=FakeClock)
    ids: FakeIds = field(default_factory=FakeIds)
    store: InMemoryStore = field(default_factory=InMemoryStore)

    def __post_init__(self) -> None:
        keys = FakeKeyProvider.default()
        self.registry = InMemoryRegistry()
        self.tools = FakeToolExecutor(self.ids)
        self.decisions = ScriptedDecision()
        self.responder = ScriptedResponder()
        self.views = ViewService(keys, _Authz(), self.clock, FieldClassifier(CATALOG))  # type: ignore[arg-type]
        self.vault = TokenVault(RUN_ID, keys, self.ids)
        self.manager = ActionManager(self.ids, self.clock)
        self.breaker = CircuitBreaker()
        self._entities: list[RegistryEntity] = []
        self.add(self.agent)

    def add(self, *entities: RegistryEntity) -> None:
        for entity in entities:
            if entity not in self._entities:
                self._entities.append(entity)
        self.registry.add(*entities)

    def add_tool(self, definition: ToolDef, *, script: Sequence[Scripted] = (),
                 handler: Handler | None = None) -> None:
        self.add(definition)
        self.tools.register(definition, script=script, handler=handler)

    def release(self) -> Release:
        entities: dict[EntityKind, dict[str, str]] = {}
        for entity in self._entities:
            entities.setdefault(ENTITY_KIND[type(entity)], {})[entity.id] = entity.version
        return Release.model_validate({
            "id": RELEASE_ID, "status": "active", "entities": entities,
            "language_detection": "lang@1.0.0",
        })

    def ctx(self, **over: Any) -> StepContext:
        base: dict[str, Any] = {
            "release": self.release(), "agent": self.agent, "locale": "es", "clock": self.clock,
            "degraded": False, "registry": self.registry, "tools": self.tools,
            "decisions": self.decisions, "actions": self.manager, "responder": self.responder,
            "views": self.views, "vault": self.vault, "ids": self.ids, "uow_factory": self.store.uow,
            "turn_id": "turn-0001", "breaker": self.breaker,
        }
        return StepContext(**(base | over))

    def state(self, f: Flow, node_id: str | None = None, **over: Any) -> RunState:
        self.add(f)
        active = {"flow": f"{f.id}@{f.version}", "node_id": node_id or f.nodes[0].id}
        return run_state(release=RELEASE_ID, active_flow=active, **over)

    def step(self, state: RunState, resume: Resume | None = None, **ctx_over: Any) -> StepOutcome:
        """Un turno: `begin_turn` + `advance`, como hará M4."""
        from agent_core.interpreter import advance, begin_turn

        return advance(begin_turn(state, self.clock), self.ctx(**ctx_over), resume or Resume())

    def persist(self, state: RunState) -> RunState:
        """Lo que hace M4 al cerrar un turno: guarda el estado en su propia transacción."""
        with self.store.uow() as uow:
            saved = uow.save_run(state, state.state_version)
            uow.commit()
        return saved
```

Nota: `step` importa `advance` y `begin_turn` de forma perezosa porque aún no existen; desde la Task 4 y la 6 ya se pueden importar arriba. Déjalo perezoso.

- [ ] **Step 6: Correr las pruebas y confirmar que pasan**

Run: `uv run pytest tests/m02 -q`
Expected: PASS (`test_breaker`, `test_events`). Si `Release.model_validate` rechaza `entities` con claves `EntityKind` como str, deja que pydantic las convierta (son `StrEnum`); si `ViewService` no arranca, revisa que `FakeKeyProvider.default()` cubra todos los `KeyPurpose`.

- [ ] **Step 7: Lint, tipos y fronteras**

Run: `uv run ruff check . && uv run mypy && uv run lint-imports`
Expected: verde. (`agent_core.interpreter` importa `actions` y `views`: está permitido.)

- [ ] **Step 8: Commit**

```bash
git add agent_core/interpreter testing/fakes/decision.py testing/fakes/responder.py tests/m02
git commit -m "feat(m2): contratos, puertos de decision y respuesta, breaker, eventos y arnes

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Evaluador de JSON Logic

**Files:**
- Create: `agent_core/interpreter/jsonlogic.py`, `tests/m02/test_jsonlogic.py`
- Modify: `agent_core/interpreter/__init__.py`

**Interfaces:**
- Consumes: `JSONLOGIC_OPS` (M1).
- Produces: `evaluate(expr: JsonValue, data: JsonValue, reads: list[str] | None = None) -> JsonValue`; `truthy(value: JsonValue) -> bool`. Lanza `ValueError` ante un operador fuera de la lista (es un error de esquema G0-01, no de runtime) o una profundidad > 64.

- [ ] **Step 1: Escribir las pruebas que fallan**

`tests/m02/test_jsonlogic.py`:
```python
from decimal import Decimal

import pytest

from agent_core.flows import JSONLOGIC_OPS
from agent_core.interpreter import evaluate, truthy
from agent_core.interpreter.jsonlogic import OPS


def test_implements_exactly_the_closed_operator_set() -> None:
    assert set(OPS) == set(JSONLOGIC_OPS)


def test_t_m2_12_decimal_comparison_is_exact() -> None:
    assert evaluate({">": [{"var": "x"}, 500]}, {"x": Decimal("500.00")}) is False
    assert evaluate({">=": [{"var": "x"}, 500]}, {"x": Decimal("500.00")}) is True
    assert evaluate({">": [{"var": "x"}, 500]}, {"x": Decimal("500.01")}) is True
    assert evaluate({"==": [{"var": "x"}, Decimal("0.3")]}, {"x": Decimal("0.30")}) is True


@pytest.mark.parametrize(("expr", "data", "expected"), [
    ({"==": [1, True]}, {}, False),  # bool no es número
    ({"==": ["1", 1]}, {}, False),  # sin coerción de tipos
    ({"!=": ["a", "b"]}, {}, True),
    ({"<": ["a", "b"]}, {}, True),
    ({"<": [1, "b"]}, {}, False),  # tipos incomparables → False, nunca excepción
    ({">": [{"var": "ausente"}, 500]}, {}, False),  # None > 500
    ({"and": [True, 1, "x"]}, {}, "x"),
    ({"and": [True, 0, "x"]}, {}, 0),
    ({"or": [0, "", "y"]}, {}, "y"),
    ({"!": [0]}, {}, True),
    ({"in": ["a", ["a", "b"]]}, {}, True),
    ({"in": ["ol", "hola"]}, {}, True),
    ({"in": [1, "hola"]}, {}, False),
    ({"if": [False, "a", True, "b", "c"]}, {}, "b"),
    ({"if": [False, "a", "c"]}, {}, "c"),
    ({"missing": ["a", "b"]}, {"a": 1}, ["b"]),
    ({"var": ["ausente", 7]}, {}, 7),
    ({"var": "a.b"}, {"a": {"b": 3}}, 3),
])
def test_operators(expr: object, data: object, expected: object) -> None:
    assert evaluate(expr, data) == expected  # type: ignore[arg-type]


def test_reads_trace_the_paths_actually_read() -> None:
    reads: list[str] = []
    evaluate({"and": [{"var": "a"}, {"missing": ["b"]}, {"var": "c"}]}, {"a": True}, reads)
    assert reads == ["a", "b", "c"]
    short: list[str] = []
    evaluate({"or": [{"var": "a"}, {"var": "c"}]}, {"a": True}, short)
    assert short == ["a"]  # cortocircuito: solo lo que se leyó


def test_unknown_operator_and_depth_are_errors() -> None:
    with pytest.raises(ValueError, match="operador"):
        evaluate({"+": [1, 2]}, {})
    deep: object = 1
    for _ in range(70):
        deep = {"!": [deep]}
    with pytest.raises(ValueError, match="profundidad"):
        evaluate(deep, {})  # type: ignore[arg-type]


def test_truthy() -> None:
    assert [truthy(v) for v in (None, False, 0, Decimal("0"), "", [], True, 1, "a", [0])] == [
        False, False, False, False, False, False, True, True, True, True]
```

- [ ] **Step 2: Correr y confirmar que falla**

Run: `uv run pytest tests/m02/test_jsonlogic.py -q`
Expected: FAIL (`ImportError: evaluate`).

- [ ] **Step 3: Implementar**

`agent_core/interpreter/jsonlogic.py`:
```python
"""Evaluador del subconjunto cerrado de JSON Logic (M2 §3.6). Aritmética con `Decimal`, nunca `float`.

Semántica deliberadamente estricta y sin excepciones para datos: sin coerción de tipos, `bool` no es número y
una comparación de orden entre tipos incomparables da `False`. Solo un operador fuera del conjunto o una
profundidad excesiva lanzan `ValueError` (son errores de esquema que M1 rechaza al cargar, G0-01)."""

from collections.abc import Callable
from decimal import Decimal

from agent_core.domain import JsonValue

MAX_DEPTH = 64

type _Op = Callable[[list[JsonValue], JsonValue, list[str], int], JsonValue]


def _number(value: JsonValue) -> Decimal | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, Decimal):
        return value
    return None


def truthy(value: JsonValue) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, str | list | dict):
        return len(value) > 0
    number = _number(value)
    return number != 0 if number is not None else True


def _equal(a: JsonValue, b: JsonValue) -> bool:
    na, nb = _number(a), _number(b)
    if na is not None or nb is not None:
        return na is not None and nb is not None and na == nb
    if isinstance(a, bool) or isinstance(b, bool):
        return isinstance(a, bool) and isinstance(b, bool) and a == b
    return a == b


def _order(a: JsonValue, b: JsonValue) -> int | None:
    na, nb = _number(a), _number(b)
    if na is not None and nb is not None:
        return (na > nb) - (na < nb)
    if isinstance(a, str) and isinstance(b, str):
        return (a > b) - (a < b)
    return None


def _eval(node: JsonValue, data: JsonValue, reads: list[str], depth: int) -> JsonValue:
    if depth > MAX_DEPTH:
        raise ValueError("profundidad máxima excedida")
    if isinstance(node, list):
        return [_eval(item, data, reads, depth + 1) for item in node]
    if not isinstance(node, dict):
        return node
    if len(node) != 1:
        raise ValueError("un nodo JSON Logic tiene exactamente una clave")
    ((op, raw),) = node.items()
    handler = OPS.get(op)
    if handler is None:
        raise ValueError(f"operador no permitido: {op}")
    return handler(raw if isinstance(raw, list) else [raw], data, reads, depth + 1)


def _lookup(data: JsonValue, path: str) -> tuple[bool, JsonValue]:
    current = data
    for key in path.split("."):
        if isinstance(current, dict) and key in current:
            current = current[key]
        else:
            return False, None
    return True, current


def _path(arg: JsonValue) -> str:
    if not isinstance(arg, str):
        raise ValueError("la ruta debe ser un string")
    return arg


def _var(args: list[JsonValue], data: JsonValue, reads: list[str], depth: int) -> JsonValue:
    path = _path(args[0])
    reads.append(path)
    found, value = _lookup(data, path)
    if found:
        return value
    return _eval(args[1], data, reads, depth) if len(args) > 1 else None


def _missing(args: list[JsonValue], data: JsonValue, reads: list[str], depth: int) -> JsonValue:
    absent: list[JsonValue] = []
    for arg in args:
        path = _path(arg)
        reads.append(path)
        found, value = _lookup(data, path)
        if not found or value is None:
            absent.append(path)
    return absent


def _equality(negate: bool) -> _Op:
    def op(args: list[JsonValue], data: JsonValue, reads: list[str], depth: int) -> JsonValue:
        same = _equal(_eval(args[0], data, reads, depth), _eval(args[1], data, reads, depth))
        return same != negate

    return op


def _compare(accept: Callable[[int], bool]) -> _Op:
    def op(args: list[JsonValue], data: JsonValue, reads: list[str], depth: int) -> JsonValue:
        order = _order(_eval(args[0], data, reads, depth), _eval(args[1], data, reads, depth))
        return order is not None and accept(order)

    return op


def _and(args: list[JsonValue], data: JsonValue, reads: list[str], depth: int) -> JsonValue:
    result: JsonValue = None
    for arg in args:
        result = _eval(arg, data, reads, depth)
        if not truthy(result):
            return result
    return result


def _or(args: list[JsonValue], data: JsonValue, reads: list[str], depth: int) -> JsonValue:
    result: JsonValue = None
    for arg in args:
        result = _eval(arg, data, reads, depth)
        if truthy(result):
            return result
    return result


def _not(args: list[JsonValue], data: JsonValue, reads: list[str], depth: int) -> JsonValue:
    return not truthy(_eval(args[0], data, reads, depth))


def _in(args: list[JsonValue], data: JsonValue, reads: list[str], depth: int) -> JsonValue:
    needle = _eval(args[0], data, reads, depth)
    haystack = _eval(args[1], data, reads, depth)
    if isinstance(haystack, list):
        return any(_equal(needle, item) for item in haystack)
    if isinstance(haystack, str) and isinstance(needle, str):
        return needle in haystack
    return False


def _if(args: list[JsonValue], data: JsonValue, reads: list[str], depth: int) -> JsonValue:
    i = 0
    while i + 1 < len(args):
        if truthy(_eval(args[i], data, reads, depth)):
            return _eval(args[i + 1], data, reads, depth)
        i += 2
    return _eval(args[i], data, reads, depth) if i < len(args) else None


OPS: dict[str, _Op] = {
    "var": _var, "missing": _missing, "==": _equality(False), "!=": _equality(True),
    ">": _compare(lambda c: c > 0), ">=": _compare(lambda c: c >= 0),
    "<": _compare(lambda c: c < 0), "<=": _compare(lambda c: c <= 0),
    "and": _and, "or": _or, "!": _not, "in": _in, "if": _if,
}


def evaluate(expr: JsonValue, data: JsonValue, reads: list[str] | None = None) -> JsonValue:
    """Evalúa `expr` sobre `data`. Si se pasa `reads`, se llena con las rutas `var`/`missing` leídas (D11)."""
    return _eval(expr, data, reads if reads is not None else [], 0)
```

`agent_core/interpreter/__init__.py`: agrega `from agent_core.interpreter.jsonlogic import evaluate, truthy` y `"evaluate"`, `"truthy"` a `__all__` (orden alfabético).

- [ ] **Step 4: Correr y confirmar que pasa**

Run: `uv run pytest tests/m02/test_jsonlogic.py -q && uv run mypy && uv run ruff check .`
Expected: PASS. Si `test_operators` con `{"==": [1, True]}` falla, revisa que `_equal` compruebe `_number` antes que `bool`.

- [ ] **Step 5: Commit**

```bash
git add agent_core/interpreter tests/m02/test_jsonlogic.py
git commit -m "feat(m2): evaluador de JSON Logic con Decimal y rutas leidas

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Resolución de rutas, argumentos y plantillas

**Files:**
- Create: `agent_core/interpreter/resolve.py`, `tests/m02/test_resolve.py`

**Interfaces:**
- Consumes: `Path`, `parse_path`, `value_paths`, `template_vars` (M1).
- Produces:
  - `class MissingPath(LookupError)`.
  - `walk(value: JsonValue, keys: Sequence[str], raw: str) -> JsonValue` (lanza `MissingPath`).
  - `resolve_path(state, path: Path) -> JsonValue` (vista `full`; `slots` solo `validated`; `decisions.<x>.<campo>`; lanza `MissingPath`).
  - `resolve_value(state, value: JsonValue) -> JsonValue` y `resolve_args(state, args: dict[str, JsonValue]) -> dict[str, JsonValue]`: sustituyen cada string que sea ruta; los literales pasan tal cual. Una ruta mal formada → `MissingPath`.
  - `input_ids(state, args: dict[str, JsonValue]) -> list[str]`: `fact_id`/`decision_id` leídos en `args`, en orden de aparición y sin repetir.
  - `render_template(text: str, values: Mapping[str, JsonValue]) -> str`.

- [ ] **Step 1: Escribir las pruebas que fallan**

`tests/m02/test_resolve.py`:
```python
from decimal import Decimal

import pytest

from agent_core.domain import Decision
from agent_core.flows import parse_path
from agent_core.interpreter.resolve import (
    MissingPath,
    input_ids,
    render_template,
    resolve_args,
    resolve_path,
)
from testing.builders import run_state
from tests.m02.harness import fact, slot


def _state():  # type: ignore[no-untyped-def]
    return run_state(
        slots={"s": slot("hola"), "c": slot("x", "claimed")},
        facts={"tx": fact({"amount": Decimal("9.50"), "cur": "USD"}, fact_id="fact-0007")},
        decisions={"d": Decision(decision_id="decision-0009", value={"match": "unica", "p": {"a": 1}},
                                 p_cal={"match": 0.9}, provider_used="s", model_version="1")},
    )


def _resolve(text: str):  # type: ignore[no-untyped-def]
    path = parse_path(text)
    assert path is not None
    return resolve_path(_state(), path)


def test_resolves_slots_facts_and_decisions() -> None:
    assert _resolve("slots.s") == "hola"
    assert _resolve("facts.tx.value.amount") == Decimal("9.50")
    assert _resolve("facts.tx.value") == {"amount": Decimal("9.50"), "cur": "USD"}
    assert _resolve("decisions.d.match") == "unica"
    assert _resolve("decisions.d.p.a") == 1


@pytest.mark.parametrize("text", [
    "slots.c",  # D7: un slot claimed cuenta como ausente
    "slots.nope", "facts.nope.value", "facts.tx.value.nope", "decisions.d.nope", "decisions.nope.match",
    "facts.tx",  # el hecho completo no es una ruta de valor
])
def test_missing_paths_raise(text: str) -> None:
    with pytest.raises(MissingPath):
        _resolve(text)


def test_resolve_args_is_recursive_and_keeps_literals() -> None:
    args = {"a": "slots.s", "n": {"x": ["facts.tx.value.cur", "USD", 3]}, "lit": "texto libre"}
    assert resolve_args(_state(), args) == {"a": "hola", "n": {"x": ["USD", "USD", 3]}, "lit": "texto libre"}


def test_resolve_args_malformed_or_missing_path_is_missing() -> None:
    with pytest.raises(MissingPath):
        resolve_args(_state(), {"a": "slots.Mal"})
    with pytest.raises(MissingPath):
        resolve_args(_state(), {"a": "facts.nope.value"})


def test_input_ids_in_order_without_repeats() -> None:
    args = {"m": "facts.tx.value.amount", "c": "facts.tx.value.cur", "t": "decisions.d.match", "s": "slots.s"}
    assert input_ids(_state(), args) == ["fact-0007", "decision-0009"]


def test_render_template_formats_values() -> None:
    values = {"facts.a.value.x": Decimal("1E+2"), "slots.b": "hola", "facts.a.value.n": 3,
              "facts.a.value.z": None, "facts.a.value.t": True}
    text = "{{ facts.a.value.x }}|{{slots.b}}|{{ facts.a.value.n }}|[{{ facts.a.value.z }}]|{{ facts.a.value.t }}"
    assert render_template(text, values) == "100|hola|3|[]|true"
```

- [ ] **Step 2: Correr y confirmar que falla**

Run: `uv run pytest tests/m02/test_resolve.py -q`
Expected: FAIL (`ModuleNotFoundError: resolve`).

- [ ] **Step 3: Implementar**

`agent_core/interpreter/resolve.py`:
```python
"""Resolución de rutas, argumentos y plantillas (M2 §3.2). La gramática es la de M1 (`parse_path`)."""

import re
from collections.abc import Mapping, Sequence
from decimal import Decimal

from agent_core.domain import JsonValue, RunState, dumps
from agent_core.flows import Path, parse_path, value_paths


class MissingPath(LookupError):
    """La ruta no existe en el estado (o es un slot `claimed`). Es una rama del flow, no un bug (D7)."""


def walk(value: JsonValue, keys: Sequence[str], raw: str) -> JsonValue:
    for key in keys:
        if isinstance(value, dict) and key in value:
            value = value[key]
        else:
            raise MissingPath(raw)
    return value


def resolve_path(state: RunState, path: Path) -> JsonValue:
    """Valor en vista `full`. Solo para tools, `rule` y `end`; a modelos y mensajes llega vía `Projector`."""
    name = path.name or ""
    if path.ns == "slots":
        found = state.slots.get(name)
        if found is None or found.status != "validated":
            raise MissingPath(path.raw)
        return found.value
    if path.ns == "facts":
        fact = state.facts.get(name)
        if fact is None or not path.rest or path.rest[0] != "value":
            raise MissingPath(path.raw)
        return walk(fact.value, path.rest[1:], path.raw)
    if path.ns == "decisions":
        decision = state.decisions.get(name)
        if decision is None:
            raise MissingPath(path.raw)
        return walk(decision.value, path.rest, path.raw)
    raise MissingPath(path.raw)


def parse_runtime_path(text: str) -> Path | None:
    """`None` si es un literal; una ruta mal formada es `MissingPath` (nunca `ValueError`)."""
    try:
        return parse_path(text)
    except ValueError:
        raise MissingPath(text) from None


def resolve_value(state: RunState, value: JsonValue) -> JsonValue:
    if isinstance(value, str):
        path = parse_runtime_path(value)
        return value if path is None else resolve_path(state, path)
    if isinstance(value, list):
        return [resolve_value(state, item) for item in value]
    if isinstance(value, dict):
        return {key: resolve_value(state, item) for key, item in value.items()}
    return value


def resolve_args(state: RunState, args: dict[str, JsonValue]) -> dict[str, JsonValue]:
    return {key: resolve_value(state, item) for key, item in args.items()}


def input_ids(state: RunState, args: dict[str, JsonValue]) -> list[str]:
    """`fact_id` y `decision_id` que leen los `args` (procedencia de `compute`, M2 §3.3)."""
    ids: list[str] = []
    for path in value_paths(args, strict=False):
        name = path.name or ""
        if path.ns == "facts" and name in state.facts:
            ident = state.facts[name].fact_id
        elif path.ns == "decisions" and name in state.decisions:
            ident = state.decisions[name].decision_id
        else:
            continue
        if ident not in ids:
            ids.append(ident)
    return ids


_VAR = re.compile(r"\{\{(?P<body>[^{}]*)\}\}")


def _text(value: JsonValue) -> str:
    if isinstance(value, str):
        return value
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, int):
        return str(value)
    return dumps(value)


def render_template(text: str, values: Mapping[str, JsonValue]) -> str:
    """Sustituye cada `{{ ruta }}` por `values[ruta]`. Las claves son las rutas ya recortadas."""
    return _VAR.sub(lambda match: _text(values[match["body"].strip()]), text)
```

- [ ] **Step 4: Correr y confirmar que pasa**

Run: `uv run pytest tests/m02/test_resolve.py -q && uv run mypy && uv run ruff check .`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add agent_core/interpreter/resolve.py tests/m02/test_resolve.py
git commit -m "feat(m2): resolucion de rutas, argumentos y plantillas

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Presupuestos

**Files:**
- Create: `agent_core/interpreter/budgets.py`, `tests/m02/test_budgets.py`
- Modify: `agent_core/interpreter/__init__.py` (exporta `begin_turn`)

**Interfaces:**
- Consumes: `Agent.budgets`, `BudgetsUsed`, `Clock`.
- Produces:
  - `begin_turn(state: RunState, clock: Clock) -> RunState`: pone `turn_nodes = 0`, `turn_model_calls = 0`, `turn_started_at = clock.now()`.
  - `enter_node(state: RunState, ctx: StepContext) -> tuple[RunState, bool]`: el `bool` es "presupuesto agotado" (no cuenta el nodo). Agotado = `turn_nodes >= max_nodes_per_turn`, `run_tokens >= max_tokens_per_run`, `run_cost >= max_cost_per_run` o tiempo transcurrido `>= max_wall_ms_per_turn`.
  - `model_budget_exhausted(state, ctx) -> bool` (`turn_model_calls >= max_model_calls_per_turn`).
  - `charge_model(state, *, calls: int, tokens: int, cost: Decimal) -> RunState`.

- [ ] **Step 1: Escribir las pruebas que fallan**

`tests/m02/test_budgets.py`:
```python
from datetime import timedelta
from decimal import Decimal

from agent_core.domain import Budgets
from agent_core.interpreter import begin_turn
from agent_core.interpreter.budgets import charge_model, enter_node, model_budget_exhausted
from tests.m02.harness import AGENT, World, flow

END = {"id": "fin", "type": "end", "config": {"outcome": "resolved"}}


def _world(**limits: object) -> World:
    budgets = Budgets.model_validate({**AGENT.budgets.model_dump(), **limits})
    return World(agent=AGENT.model_copy(update={"budgets": budgets}))


def test_begin_turn_resets_turn_counters_only() -> None:
    w = World()
    state = w.state(flow(END), budgets_used={"run_tokens": 5, "turn_nodes": 9, "turn_model_calls": 2})
    fresh = begin_turn(state, w.clock)
    used = fresh.budgets_used
    assert (used.turn_nodes, used.turn_model_calls, used.run_tokens) == (0, 0, 5)
    assert used.turn_started_at == w.clock.now()


def test_enter_node_counts_and_exhausts_nodes() -> None:
    w = _world(max_nodes_per_turn=2)
    state = begin_turn(w.state(flow(END)), w.clock)
    state, exhausted = enter_node(state, w.ctx())
    assert (state.budgets_used.turn_nodes, exhausted) == (1, False)
    state, exhausted = enter_node(state, w.ctx())
    assert (state.budgets_used.turn_nodes, exhausted) == (2, False)
    state, exhausted = enter_node(state, w.ctx())
    assert exhausted and state.budgets_used.turn_nodes == 2  # el nodo agotado no se cuenta


def test_tokens_cost_and_wall_time_exhaust() -> None:
    w = _world(max_tokens_per_run=100, max_cost_per_run="1.00", max_wall_ms_per_turn=1000)
    f = flow(END)
    assert enter_node(begin_turn(w.state(f, budgets_used={"run_tokens": 100}), w.clock), w.ctx())[1]
    assert enter_node(begin_turn(w.state(f, budgets_used={"run_cost": Decimal("1.00")}), w.clock), w.ctx())[1]
    state = begin_turn(w.state(f), w.clock)
    w.clock.advance(timedelta(milliseconds=999))
    assert not enter_node(state, w.ctx())[1]
    w.clock.advance(timedelta(milliseconds=1))
    assert enter_node(state, w.ctx())[1]


def test_model_calls_budget() -> None:
    w = _world(max_model_calls_per_turn=2)
    state = begin_turn(w.state(flow(END)), w.clock)
    assert not model_budget_exhausted(state, w.ctx())
    state = charge_model(state, calls=2, tokens=30, cost=Decimal("0.01"))
    assert model_budget_exhausted(state, w.ctx())
    assert (state.budgets_used.run_tokens, state.budgets_used.run_cost) == (30, Decimal("0.01"))
```

- [ ] **Step 2: Correr y confirmar que falla**

Run: `uv run pytest tests/m02/test_budgets.py -q`
Expected: FAIL (`ImportError: begin_turn`).

- [ ] **Step 3: Implementar**

`agent_core/interpreter/budgets.py`:
```python
"""Presupuestos duros por turno y por run (M2 §3.5). Nada de esto lee la hora fuera de `ctx.clock`."""

from datetime import timedelta
from decimal import Decimal

from agent_core.domain import RunState
from agent_core.interpreter.context import StepContext
from agent_core.ports import Clock


def begin_turn(state: RunState, clock: Clock) -> RunState:
    """Pone en cero los contadores del turno (D4). Lo llama M4 (y el arnés) antes de `advance`."""
    used = state.budgets_used.model_copy(
        update={"turn_nodes": 0, "turn_model_calls": 0, "turn_started_at": clock.now()}
    )
    return state.model_copy(update={"budgets_used": used})


def enter_node(state: RunState, ctx: StepContext) -> tuple[RunState, bool]:
    """Descuenta un nodo. Devuelve `True` si algún presupuesto ya estaba agotado (el nodo no se cuenta)."""
    used = state.budgets_used
    limits = ctx.agent.budgets
    now = ctx.clock.now()
    started = used.turn_started_at or now
    elapsed_ms = (now - started) // timedelta(milliseconds=1)
    exhausted = (
        used.turn_nodes >= limits.max_nodes_per_turn
        or used.run_tokens >= limits.max_tokens_per_run
        or used.run_cost >= limits.max_cost_per_run
        or elapsed_ms >= limits.max_wall_ms_per_turn
    )
    update: dict[str, object] = {"turn_started_at": started}
    if not exhausted:
        update["turn_nodes"] = used.turn_nodes + 1
    return state.model_copy(update={"budgets_used": used.model_copy(update=update)}), exhausted


def model_budget_exhausted(state: RunState, ctx: StepContext) -> bool:
    return state.budgets_used.turn_model_calls >= ctx.agent.budgets.max_model_calls_per_turn


def charge_model(state: RunState, *, calls: int, tokens: int, cost: Decimal) -> RunState:
    used = state.budgets_used
    charged = used.model_copy(update={
        "turn_model_calls": used.turn_model_calls + calls,
        "run_tokens": used.run_tokens + tokens,
        "run_cost": used.run_cost + cost,
    })
    return state.model_copy(update={"budgets_used": charged})
```

`agent_core/interpreter/__init__.py`: agrega `from agent_core.interpreter.budgets import begin_turn` y `"begin_turn"` en `__all__`.

- [ ] **Step 4: Correr y confirmar que pasa**

Run: `uv run pytest tests/m02/test_budgets.py -q && uv run mypy && uv run ruff check .`
Expected: PASS. En el arnés (`tests/m02/harness.py`, `World.step`) cambia el import perezoso de `begin_turn` por uno al inicio del archivo.

- [ ] **Step 5: Commit**

```bash
git add agent_core/interpreter tests/m02
git commit -m "feat(m2): presupuestos por turno y por run

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Referencias, vistas `model`/`audit` y plantillas

**Files:**
- Create: `agent_core/interpreter/refs.py`, `projection.py`, `templates.py`, `audit.py`
- Create: `tests/m02/test_projection.py`

**Interfaces:**
- Consumes: `release_view`, `parse_path`, `template_vars` (M1); `ViewService.project`, `Views`, `TokenVault` (M7); `AuditProjector` (M3); `resolve.*` (Task 3).
- Produces:
  - `exact_ref(ctx, kind: EntityKind, ref: RefSpec) -> EntityRef` (lanza `InvalidRuntimeRef` si la release no lo contiene); `make_resolver(ctx) -> RefResolver` (busca `tool` y `template`).
  - `Projector(state, ctx)` con `model_value(path: Path, *, wrap_slots: bool = False) -> JsonValue` (lanza `MissingPath`) y `audit_value(path: Path) -> JsonValue` (ausente → `None`).
  - `render_message(state, ctx, ref: RefSpec) -> Message` (`kind="template"`, locale = `ctx.locale`, vista `model`; lanza `MissingPath`; `SchemaError` si la plantilla no tiene el locale).
  - `ViewsAudit(views, vault)` que cumple `AuditProjector` de M3; `rule_inputs(state, ctx, reads: list[str]) -> dict[str, JsonValue]`.

- [ ] **Step 1: Escribir las pruebas que fallan**

`tests/m02/test_projection.py`:
```python
from decimal import Decimal

import pytest

from agent_core.domain import EntityKind, InvalidRuntimeRef, RefSpec, SchemaError
from agent_core.flows import parse_path
from agent_core.interpreter.audit import ViewsAudit, rule_inputs
from agent_core.interpreter.projection import Projector
from agent_core.interpreter.refs import exact_ref, make_resolver
from agent_core.interpreter.resolve import MissingPath
from agent_core.interpreter.templates import render_message
from tests.m02.harness import World, fact, flow, slot, template, tool_def

END = {"id": "fin", "type": "end", "config": {"outcome": "resolved"}}


def _path(text: str):  # type: ignore[no-untyped-def]
    path = parse_path(text)
    assert path is not None
    return path


def test_exact_ref_and_resolver() -> None:
    w = World()
    w.add(template("t/hola", "hola"), tool_def("buscar"))
    ctx = w.ctx()
    assert str(exact_ref(ctx, EntityKind.template, RefSpec.parse("t/hola@1"))) == "t/hola@1.0.0"
    assert str(make_resolver(ctx)(RefSpec.parse("buscar@1"))) == "buscar@1.0.0"
    with pytest.raises(InvalidRuntimeRef):
        exact_ref(ctx, EntityKind.template, RefSpec.parse("t/otra@1"))


def test_model_view_tokenizes_pii_and_keeps_financial_values() -> None:
    w = World()
    state = w.state(flow(END), facts={"cli": fact({"email": "ana@example.com", "amount": Decimal("9.50")})})
    projector = Projector(state, w.ctx())
    assert projector.model_value(_path("facts.cli.value.amount")) == Decimal("9.50")
    token = projector.model_value(_path("facts.cli.value.email"))
    assert isinstance(token, str) and token.startswith("⟦email:") and "ana@example.com" not in token
    audit = projector.audit_value(_path("facts.cli.value.email"))
    assert audit == "***"


def test_slots_claimed_missing_and_wrapped_for_models() -> None:
    w = World()
    state = w.state(flow(END), slots={"s": slot("cargo raro"), "c": slot("x", "claimed")})
    projector = Projector(state, w.ctx())
    with pytest.raises(MissingPath):
        projector.model_value(_path("slots.c"))
    assert projector.audit_value(_path("slots.c")) is None
    plain = projector.model_value(_path("slots.s"))
    wrapped = projector.model_value(_path("slots.s"), wrap_slots=True)
    assert isinstance(plain, str) and plain.startswith("⟦")  # sin clasificar → token (fail closed)
    assert isinstance(wrapped, str) and wrapped.startswith("<datos_no_confiables") and "cargo raro" in wrapped


def test_fact_source_falls_back_when_ref_is_an_action_id() -> None:
    w = World()
    state = w.state(flow(END), facts={"pqr": fact({"id": "pqr-1"}, ref="action-0001")})
    assert Projector(state, w.ctx()).model_value(_path("facts.pqr.value.id")) == "pqr-1"  # `id` es public


def test_render_message_uses_model_view_and_locale() -> None:
    w = World()
    w.add(template("t/pqr", "Radicada {{ facts.pqr.value.id }} por {{ facts.pqr.value.amount }}.",
                   "Registrada {{ facts.pqr.value.id }}."))
    state = w.state(flow(END), facts={"pqr": fact({"id": "pqr-1", "amount": Decimal("10.50")})})
    message = render_message(state, w.ctx(), RefSpec.parse("t/pqr@1"))
    assert (message.kind, message.locale, message.text) == ("template", "es", "Radicada pqr-1 por 10.50.")
    assert render_message(state, w.ctx(locale="pt"), RefSpec.parse("t/pqr@1")).text == "Registrada pqr-1."


def test_render_message_missing_variable_and_locale() -> None:
    w = World()
    w.add(template("t/x", "{{ facts.nope.value.id }}"))
    state = w.state(flow(END))
    with pytest.raises(MissingPath):
        render_message(state, w.ctx(), RefSpec.parse("t/x@1"))
    with pytest.raises(SchemaError):
        render_message(state, w.ctx(locale="fr"), RefSpec.parse("t/x@1"))


def test_views_audit_and_rule_inputs_never_expose_full() -> None:
    w = World()
    state = w.state(flow(END), facts={"m": fact(Decimal("620.00"))}, slots={"s": slot("secreto")})
    inputs = rule_inputs(state, w.ctx(), ["facts.m.value", "slots.s", "facts.m.value", "slots.ausente", "no.es.ruta"])
    assert list(inputs) == ["facts.m.value", "slots.s", "slots.ausente"]  # sin duplicados ni no-rutas
    assert "620.00" not in repr(inputs) and "secreto" not in repr(inputs)
    assert inputs["slots.ausente"] is None
    definition = tool_def("buscar", source="tx")
    args = ViewsAudit(w.views, w.vault).args({"email": "ana@example.com"}, definition)
    assert "ana@example.com" not in repr(args)
    result, fingerprint = ViewsAudit(w.views, w.vault).result({"id": "x"}, definition)
    assert fingerprint is not None and result == {"id": "x"}
```

- [ ] **Step 2: Correr y confirmar que falla**

Run: `uv run pytest tests/m02/test_projection.py -q`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Implementar**

`agent_core/interpreter/refs.py`:
```python
"""Referencias de autoría → referencias exactas de la release (M1 `release_view`)."""

from agent_core.actions import RefResolver
from agent_core.domain import EntityKind, EntityRef, InvalidRuntimeRef, RefSpec
from agent_core.flows import release_view
from agent_core.interpreter.context import StepContext


def exact_ref(ctx: StepContext, kind: EntityKind, ref: RefSpec) -> EntityRef:
    entity = release_view(ctx.registry, ctx.release).resolve(kind, ref)
    if entity is None:
        raise InvalidRuntimeRef(f"{kind.value} {ref} no está en la release {ctx.release.id}")
    return EntityRef(id=entity.id, version=entity.version)


def make_resolver(ctx: StepContext) -> RefResolver:
    """`RefResolver` de M3: sus referencias son tools (`action.tool`, `readback`) y plantillas."""

    def resolve(ref: RefSpec) -> EntityRef:
        for kind in (EntityKind.tool, EntityKind.template):
            try:
                return exact_ref(ctx, kind, ref)
            except InvalidRuntimeRef:
                continue
        raise InvalidRuntimeRef(f"{ref} no está en la release {ctx.release.id}")

    return resolve
```

`agent_core/interpreter/projection.py`:
```python
"""Puente hacia M7: vistas `model` y `audit` de slots y hechos del run. `full` solo sale por `ctx.views`."""

from agent_core.domain import Fact, InvalidRuntimeRef, JsonValue, RunState, ToolDef
from agent_core.domain.refs import EntityRef
from agent_core.flows import Path
from agent_core.interpreter.context import StepContext
from agent_core.interpreter.resolve import MissingPath, resolve_path, walk
from agent_core.views import Views


class Projector:
    """Proyecta cada hecho una sola vez por instancia (el `TokenVault` da el mismo token al mismo valor)."""

    def __init__(self, state: RunState, ctx: StepContext) -> None:
        self._state = state
        self._ctx = ctx
        self._facts: dict[str, Views] = {}

    def model_value(self, path: Path, *, wrap_slots: bool = False) -> JsonValue:
        return self._value(path, "model", wrap_slots)

    def audit_value(self, path: Path) -> JsonValue:
        try:
            return self._value(path, "audit", False)
        except MissingPath:
            return None

    def _value(self, path: Path, view: str, wrap_slots: bool) -> JsonValue:
        name = path.name or ""
        if path.ns == "facts":
            if not path.rest or path.rest[0] != "value":
                raise MissingPath(path.raw)
            views = self._fact_views(name)
            return walk(views.model if view == "model" else views.audit, path.rest[1:], path.raw)
        if path.ns == "slots":
            found = self._state.slots.get(name)
            if found is None or found.status != "validated":
                raise MissingPath(path.raw)
            untrusted = ["slots"] if wrap_slots else []  # D8
            views = self._ctx.views.project(found.value, "slots", untrusted, self._ctx.vault)
            return views.model if view == "model" else views.audit
        return resolve_path(self._state, path)  # decisions: ya se calcularon sobre la vista `model`

    def _fact_views(self, name: str) -> Views:
        cached = self._facts.get(name)
        if cached is not None:
            return cached
        fact = self._state.facts.get(name)
        if fact is None:
            raise MissingPath(f"facts.{name}")
        source, untrusted = self._source(fact)
        views = self._ctx.views.project(fact.value, source, untrusted, self._ctx.vault)
        self._facts[name] = views
        return views

    def _source(self, fact: Fact) -> tuple[str, list[str]]:
        if fact.source.kind in ("tool", "compute"):
            definition = self._tool_def(fact.source.ref)
            if definition is not None:
                return definition.source or definition.id, definition.untrusted_fields
        return fact.source.ref, []

    def _tool_def(self, ref: str) -> ToolDef | None:
        """`ref` es `tool@X.Y.Z`, o un `action_id` (los hechos de M3 de escritura y readback)."""
        try:
            return self._ctx.tools.definition(EntityRef.parse(ref))
        except (InvalidRuntimeRef, KeyError):
            pass
        for action in self._state.actions:
            if action.action_id == ref:
                try:
                    return self._ctx.tools.definition(action.tool)
                except KeyError:
                    return None
        return None
```

`agent_core/interpreter/templates.py`:
```python
"""Render de plantillas `{{ ruta }}` en vista `model` (M2 §3.3, D6). Los tokens los resuelve `views.render`."""

from agent_core.domain import EntityKind, Message, RefSpec, SchemaError, Template
from agent_core.flows import template_vars
from agent_core.interpreter.context import StepContext
from agent_core.interpreter.projection import Projector
from agent_core.interpreter.refs import exact_ref
from agent_core.interpreter.resolve import parse_runtime_path, render_template
from agent_core.domain import RunState


def render_message(state: RunState, ctx: StepContext, ref: RefSpec) -> Message:
    template = ctx.registry.get(exact_ref(ctx, EntityKind.template, ref), Template)
    text = template.locales.get(ctx.locale)
    if text is None:
        raise SchemaError(f"la plantilla {template.id} no tiene el locale {ctx.locale}")
    projector = Projector(state, ctx)
    values = {}
    for raw in template_vars(text):
        path = parse_runtime_path(raw)
        assert path is not None  # `template_vars` solo devuelve rutas
        values[raw] = projector.model_value(path)
    return Message(kind="template", text=render_template(text, values), locale=ctx.locale)
```
(Ordena los imports con `ruff --fix`; deja `RunState` junto al resto de `agent_core.domain`.)

`agent_core/interpreter/audit.py`:
```python
"""Vista `audit` para eventos de M2 y para `ActionContext.audit` de M3 (M7 es la única salida de `full`)."""

from agent_core.domain import Fingerprint, JsonValue, RunState, ToolDef
from agent_core.interpreter.context import StepContext
from agent_core.interpreter.projection import Projector
from agent_core.interpreter.resolve import parse_runtime_path, MissingPath
from agent_core.views import TokenVault, ViewService


class ViewsAudit:
    """`AuditProjector` de M3: argumentos y resultados de una tool en vista `audit` + huella con clave."""

    def __init__(self, views: ViewService, vault: TokenVault) -> None:
        self._views = views
        self._vault = vault

    @staticmethod
    def _source(tool: ToolDef) -> str:
        return tool.source or tool.id

    def args(self, args: dict[str, JsonValue], tool: ToolDef) -> dict[str, JsonValue]:
        audit = self._views.project(args, self._source(tool), tool.untrusted_fields, self._vault).audit
        return audit if isinstance(audit, dict) else {}

    def result(self, result_full: JsonValue, tool: ToolDef) -> tuple[JsonValue, Fingerprint | None]:
        views = self._views.project(result_full, self._source(tool), tool.untrusted_fields, self._vault)
        return views.audit, views.fingerprint


def rule_inputs(state: RunState, ctx: StepContext, reads: list[str]) -> dict[str, JsonValue]:
    """Entradas de un `rule` en vista `audit`: solo las rutas `slots.*`/`facts.*` que la expresión leyó."""
    projector = Projector(state, ctx)
    inputs: dict[str, JsonValue] = {}
    for raw in dict.fromkeys(reads):
        try:
            path = parse_runtime_path(raw)
        except MissingPath:
            continue
        if path is not None and path.ns in ("slots", "facts"):
            inputs[raw] = projector.audit_value(path)
    return inputs
```

- [ ] **Step 4: Correr y confirmar que pasa**

Run: `uv run ruff check . --fix && uv run pytest tests/m02/test_projection.py -q && uv run mypy && uv run lint-imports`
Expected: PASS. Ajustes esperables: si `audit_value` de un email no da exactamente `"***"`, cambia la aserción por `"ana@example.com" not in repr(audit)`; `mask()` de M7 solo conserva finales para `doc|tel|prod`.

- [ ] **Step 5: Commit**

```bash
git add agent_core/interpreter tests/m02/test_projection.py
git commit -m "feat(m2): referencias exactas, vistas model/audit y render de plantillas

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Bucle, `respond` (plantilla), `escalate`, `end` y presupuestos en el bucle

**Files:**
- Create: `agent_core/interpreter/handlers/__init__.py`, `handlers/base.py`, `handlers/terminal.py`, `handlers/respond.py`, `agent_core/interpreter/loop.py`
- Modify: `agent_core/interpreter/__init__.py` (exporta `advance`, `start_flow`)
- Create: `tests/m02/test_loop.py`

**Interfaces:**
- Consumes: Tasks 1–5; `node_kind` (M0).
- Produces:
  - `NodeResult(state, result_key=None, stop=None, messages=[], events=[], end_outcome=None, escalation=None, confirmation=None, step_up=None, output=None, rejected=[])` (dataclass mutable) y `NodeHandler = Callable[[Any, RunState, StepContext, Resume], NodeResult]`.
  - `DEFAULT_PRIORITY = "normal"`, `escalation_request(ctx, reason, target_queue=None, priority=DEFAULT_PRIORITY) -> EscalationRequest`, `escalate_now(state, ctx, reason, events=None) -> NodeResult`, `clear_attempts(state, node_id) -> RunState`, `request_step_up(state, ctx, node_id, level, max_attempts, events) -> NodeResult`.
  - `advance(state, ctx, resume) -> StepOutcome`, `start_flow(state, flow: Flow) -> RunState` (D5).
  - `HANDLERS: Mapping[str, NodeHandler]` (aquí: `respond`, `escalate`, `end`; las demás tasks agregan el suyo).
- Regla del bucle: si un handler devuelve `stop` **y** `result_key` (solo `respond` con `await`), el puntero avanza antes de parar (D16).

- [ ] **Step 1: Escribir las pruebas que fallan**

`tests/m02/test_loop.py`:
```python
from datetime import timedelta
from decimal import Decimal

import pytest

from agent_core.domain import Budgets, IllegalTransition, Outcome
from agent_core.interpreter import Resume, Stop, advance, start_flow
from tests.m02.harness import AGENT, World, fact, flow, template

FIN = {"id": "fin", "type": "end", "config": {"outcome": "resolved"}}


def _respond(id: str, ref: str, nxt: str, **cfg: object) -> dict[str, object]:
    return {"id": id, "type": "respond", "config": {"template_ref": ref, **cfg}, "next": {"next": nxt}}


def test_respond_template_then_end() -> None:
    w = World()
    w.add(template("t/hola", "Total {{ facts.saldo.value.amount }}"))
    f = flow(_respond("r", "t/hola@1", "fin"), FIN)
    out = w.step(w.state(f, facts={"saldo": fact({"amount": Decimal("10.50")})}))
    assert (out.stop, out.end_outcome, out.escalation) == (Stop.terminal, Outcome.resolved, None)
    assert [m.text for m in out.messages] == ["Total 10.50"]
    assert [e.type for e in out.events] == ["node_entered", "node_entered"]  # type: ignore[attr-defined]
    assert out.state.active_flow is not None and out.state.active_flow.node_id == "fin"


def test_respond_await_moves_pointer_before_stopping() -> None:
    w = World()
    w.add(template("t/aclarar", "¿Cuál cargo?"))
    f = flow(_respond("a", "t/aclarar@1", "fin", **{"await": True}), FIN)
    out = w.step(w.state(f))
    assert out.stop is Stop.awaiting_user and [m.text for m in out.messages] == ["¿Cuál cargo?"]
    assert out.state.active_flow is not None and out.state.active_flow.node_id == "fin"  # D16
    resumed = w.step(out.state)
    assert resumed.stop is Stop.terminal and resumed.messages == []


def test_escalate_builds_request_with_priority_expr() -> None:
    w = World()
    esc = {"id": "e", "type": "escalate", "config": {
        "reason_code": "policy:monto", "target_queue": "disputas",
        "priority_expr": {"if": [{">": [{"var": "facts.m.value"}, 500]}, "high", "normal"]}}}
    out = w.step(w.state(flow(esc), facts={"m": fact(Decimal("620.00"))}))
    assert out.stop is Stop.terminal and out.end_outcome is None
    assert out.escalation is not None
    assert (out.escalation.reason_code, out.escalation.target_queue, out.escalation.priority) == (
        "policy:monto", "disputas", "high")


def test_escalate_defaults_queue_and_priority() -> None:
    w = World()
    out = w.step(w.state(flow({"id": "e", "type": "escalate", "config": {"reason_code": "tool_failure"}})))
    assert out.escalation is not None
    assert (out.escalation.target_queue, out.escalation.priority) == ("general", "normal")


def test_end_applies_output_map_only_in_task_mode() -> None:
    w = World(agent=AGENT.model_copy(update={"mode": "task"}))
    end = {"id": "fin", "type": "end", "config": {"outcome": "completed",
                                                   "output_map": {"pqr": "facts.p.value.id"}}}
    f = flow(end)
    out = w.step(w.state(f, mode="task", session_id=None, facts={"p": fact({"id": "pqr-9"})}))
    assert out.end_outcome is Outcome.completed and out.output == {"pqr": "pqr-9"}
    conversational = World().step(World().state(flow({**end, "config": {"outcome": "resolved",
                                                                        "output_map": {"pqr": "facts.p.value.id"}}})))
    assert conversational.output is None


def test_missing_template_variable_escalates_validation_failed() -> None:
    w = World()
    w.add(template("t/x", "{{ facts.nope.value.id }}"))
    out = w.step(w.state(flow(_respond("r", "t/x@1", "fin"), FIN)))
    assert out.escalation is not None and out.escalation.reason_code == "validation_failed"
    assert out.stop is Stop.terminal


def test_start_flow_points_to_first_node_and_resets_attempts() -> None:
    w = World()
    f = flow(_respond("r", "t/hola@1", "fin"), FIN)
    state = w.state(f).model_copy(update={"active_flow": None, "node_attempts": {"x": 2}})
    started = start_flow(state, f)
    assert started.active_flow is not None
    assert (str(started.active_flow.flow), started.active_flow.node_id) == ("f@1.0.0", "r")
    assert started.node_attempts == {}


def test_transition_without_next_is_a_bug() -> None:
    w = World()
    w.add(template("t/hola", "hola"))
    f = flow({"id": "r", "type": "respond", "config": {"template_ref": "t/hola@1"}, "next": {}})
    with pytest.raises(IllegalTransition):
        w.step(w.state(f))


def test_advance_requires_active_flow() -> None:
    w = World()
    state = w.state(flow(FIN)).model_copy(update={"active_flow": None})
    with pytest.raises(IllegalTransition):
        advance(state, w.ctx(), Resume())


# T-M2-08 (parte 1): cada presupuesto agotado escala con budget_exceeded.
def _tight(**limits: object) -> World:
    return World(agent=AGENT.model_copy(update={"budgets": Budgets.model_validate(
        {**AGENT.budgets.model_dump(), **limits})}))


def _chain() -> object:
    return flow(_respond("a", "t/hola@1", "b"), _respond("b", "t/hola@1", "fin"), FIN)


@pytest.mark.parametrize(("limits", "used"), [
    ({"max_nodes_per_turn": 2}, {}),
    ({"max_tokens_per_run": 100}, {"run_tokens": 100}),
    ({"max_cost_per_run": "1.00"}, {"run_cost": Decimal("1.00")}),
])
def test_t_m2_08_exhausted_budget_escalates(limits: dict[str, object], used: dict[str, object]) -> None:
    w = _tight(**limits)
    w.add(template("t/hola", "hola"))
    out = w.step(w.state(_chain(), budgets_used=used))  # type: ignore[arg-type]
    assert out.stop is Stop.terminal and out.end_outcome is None
    assert out.escalation is not None
    assert (out.escalation.reason_code, out.escalation.target_queue) == ("budget_exceeded", "general")


def test_t_m2_08_wall_time_escalates() -> None:
    w = _tight(max_wall_ms_per_turn=1000)
    w.add(template("t/hola", "hola"))
    from agent_core.interpreter import begin_turn

    state = begin_turn(w.state(_chain()), w.clock)  # type: ignore[arg-type]
    w.clock.advance(timedelta(seconds=2))
    out = advance(state, w.ctx(), Resume())
    assert out.escalation is not None and out.escalation.reason_code == "budget_exceeded"
    assert out.messages == [] and [e.type for e in out.events] == []  # type: ignore[attr-defined]
```

- [ ] **Step 2: Correr y confirmar que falla**

Run: `uv run pytest tests/m02/test_loop.py -q`
Expected: FAIL (`ImportError: advance`).

- [ ] **Step 3: Implementar**

`agent_core/interpreter/handlers/base.py`:
```python
"""Tipos y ayudas comunes de los handlers (M2 §2)."""

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from agent_core.domain import (
    AuthLevel,
    ConfirmationPrompt,
    EngineEvent,
    EscalationRequest,
    JsonValue,
    Outcome,
    RejectedDraft,
    Message,
    RunState,
    StepUpPrompt,
)
from agent_core.interpreter.context import Resume, StepContext, Stop
from agent_core.interpreter.events import Events

DEFAULT_PRIORITY = "normal"


@dataclass
class NodeResult:
    """Salida de un handler: la rama que sigue (`result_key`) o por qué se detiene (`stop`), o ambas (D16)."""

    state: RunState
    result_key: str | None = None
    stop: Stop | None = None
    messages: list[Message] = field(default_factory=list)
    events: list[EngineEvent] = field(default_factory=list)
    end_outcome: Outcome | None = None
    escalation: EscalationRequest | None = None
    confirmation: ConfirmationPrompt | None = None
    step_up: StepUpPrompt | None = None
    output: dict[str, JsonValue] | None = None
    rejected: list[RejectedDraft] = field(default_factory=list)


NodeHandler = Callable[[Any, RunState, StepContext, Resume], NodeResult]


def escalation_request(ctx: StepContext, reason: str, target_queue: str | None = None,
                       priority: str = DEFAULT_PRIORITY) -> EscalationRequest:
    return EscalationRequest(reason_code=reason, target_queue=target_queue or ctx.agent.default_target_queue,
                             priority=priority)


def escalate_now(state: RunState, ctx: StepContext, reason: str,
                 events: list[EngineEvent] | None = None) -> NodeResult:
    """Escalamiento que origina el motor (no un nodo `escalate`): presupuesto, step-up, ruta ausente (D12, D13)."""
    return NodeResult(state, stop=Stop.terminal, escalation=escalation_request(ctx, reason),
                      events=list(events or []))


def clear_attempts(state: RunState, node_id: str) -> RunState:
    if node_id not in state.node_attempts:
        return state
    return state.model_copy(update={"node_attempts": {k: v for k, v in state.node_attempts.items()
                                                      if k != node_id}})


def request_step_up(state: RunState, ctx: StepContext, node_id: str, level: AuthLevel, max_attempts: int,
                    events: list[EngineEvent]) -> NodeResult:
    """M2 §3.4 (ADR 0010, D9): el nodo no avanza; al superar `max_attempts` escala `auth_insufficient`."""
    attempts = state.node_attempts.get(node_id, 0) + 1
    state = state.model_copy(update={"node_attempts": {**state.node_attempts, node_id: attempts}})
    emitted = [*events, Events(ctx).step_up_requested(state, node_id, level, attempts)]
    if attempts > max_attempts:
        return escalate_now(state, ctx, "auth_insufficient", emitted)
    prompt = StepUpPrompt(required_level=level, reason=f"requires_{level.value}")
    return NodeResult(state, stop=Stop.awaiting_step_up, step_up=prompt, events=emitted)
```

`agent_core/interpreter/handlers/terminal.py`:
```python
"""`escalate` y `end` (M2 §3.3): terminales."""

from agent_core.domain import EndNode, EscalateNode, RunState
from agent_core.interpreter.context import Resume, StepContext, Stop
from agent_core.interpreter.handlers.base import (
    DEFAULT_PRIORITY,
    NodeResult,
    escalate_now,
    escalation_request,
)
from agent_core.interpreter.jsonlogic import evaluate
from agent_core.interpreter.resolve import MissingPath, resolve_value


def _rule_data(state: RunState) -> dict[str, object]:
    return {
        "slots": {name: s.value for name, s in state.slots.items() if s.status == "validated"},
        "facts": {name: {"value": f.value} for name, f in state.facts.items()},
    }


def rule_data(state: RunState):  # type: ignore[no-untyped-def]
    """Datos de `rule` y `priority_expr`: hechos `full` y slots `validated`. Nunca `decisions` ni `claimed`."""
    return _rule_data(state)


def handle_escalate(node: EscalateNode, state: RunState, ctx: StepContext, resume: Resume) -> NodeResult:
    cfg = node.config
    priority = DEFAULT_PRIORITY
    if cfg.priority_expr is not None:
        value = evaluate(cfg.priority_expr, rule_data(state))
        if isinstance(value, str) and value:
            priority = value
    request = escalation_request(ctx, cfg.reason_code, cfg.target_queue, priority)
    return NodeResult(state, stop=Stop.terminal, escalation=request)


def handle_end(node: EndNode, state: RunState, ctx: StepContext, resume: Resume) -> NodeResult:
    cfg = node.config
    output = None
    if cfg.output_map and state.mode == "task":
        try:
            output = {key: resolve_value(state, path) for key, path in cfg.output_map.items()}
        except MissingPath:
            return escalate_now(state, ctx, "validation_failed")
    return NodeResult(state, stop=Stop.terminal, end_outcome=cfg.outcome, output=output)
```
Nota de limpieza: deja **una sola** función pública `rule_data(state) -> JsonValue` (borra `_rule_data` y el `# type: ignore`): `return {"slots": {...}, "facts": {...}}` tipado como `dict[str, JsonValue]`. La reutiliza `rule.py` (Task 7).

`agent_core/interpreter/handlers/respond.py`:
```python
"""`respond` con `template_ref` (M2 §3.3). La rama `generate` llega en la Task 12."""

from agent_core.domain import RespondNode, RunState
from agent_core.interpreter.context import Resume, StepContext, Stop
from agent_core.interpreter.handlers.base import NodeResult, escalate_now
from agent_core.interpreter.resolve import MissingPath
from agent_core.interpreter.templates import render_message


def handle_respond(node: RespondNode, state: RunState, ctx: StepContext, resume: Resume) -> NodeResult:
    cfg = node.config
    if cfg.template_ref is None:
        raise NotImplementedError("respond(generate): Task 12")
    try:
        message = render_message(state, ctx, cfg.template_ref)
    except MissingPath:
        return escalate_now(state, ctx, "validation_failed")
    stop = Stop.awaiting_user if cfg.await_ else None
    return NodeResult(state, result_key="next", stop=stop, messages=[message])
```

`agent_core/interpreter/handlers/__init__.py`:
```python
"""Registro de handlers por tipo de nodo (M2 §2). Agregar un tipo de nodo = agregar su handler aquí."""

from collections.abc import Mapping
from types import MappingProxyType

from agent_core.interpreter.handlers.base import NodeHandler, NodeResult
from agent_core.interpreter.handlers.respond import handle_respond
from agent_core.interpreter.handlers.terminal import handle_end, handle_escalate

HANDLERS: Mapping[str, NodeHandler] = MappingProxyType({
    "respond": handle_respond,
    "escalate": handle_escalate,
    "end": handle_end,
})

__all__ = ["HANDLERS", "NodeHandler", "NodeResult"]
```

`agent_core/interpreter/loop.py`:
```python
"""Bucle de M2 (§3.1): ejecuta nodos hasta uno que espera al principal o uno terminal."""

from agent_core.domain import (
    ActiveFlow,
    EngineEvent,
    EntityRef,
    Flow,
    IllegalTransition,
    Message,
    Node,
    RejectedDraft,
    RunState,
    node_kind,
)
from agent_core.interpreter.budgets import enter_node
from agent_core.interpreter.context import NO_RESUME, Resume, StepContext, StepOutcome
from agent_core.interpreter.events import Events
from agent_core.interpreter.handlers import HANDLERS
from agent_core.interpreter.handlers.base import escalation_request
from agent_core.interpreter.context import Stop


def _node(flow: Flow, node_id: str) -> Node:
    for node in flow.nodes:
        if node.id == node_id:
            return node
    raise IllegalTransition(f"el flow {flow.id}@{flow.version} no tiene el nodo {node_id}")


def _move(state: RunState, node: Node, key: str) -> RunState:
    target = node.next.get(key)
    if target is None:
        raise IllegalTransition(f"el nodo {node.id} no tiene transición para {key!r}")
    assert state.active_flow is not None
    return state.model_copy(update={"active_flow": state.active_flow.model_copy(update={"node_id": target})})


def start_flow(state: RunState, flow: Flow) -> RunState:
    """Fija `active_flow` en el nodo de entrada (`flow.nodes[0]`) y reinicia `node_attempts` (D5)."""
    active = ActiveFlow(flow=EntityRef(id=flow.id, version=flow.version), node_id=flow.nodes[0].id)
    return state.model_copy(update={"active_flow": active, "node_attempts": {}})


def advance(state: RunState, ctx: StepContext, resume: Resume) -> StepOutcome:
    if state.active_flow is None:
        raise IllegalTransition("advance necesita un flow activo")
    events: list[EngineEvent] = []
    messages: list[Message] = []
    rejected: list[RejectedDraft] = []
    factory = Events(ctx)
    while True:
        active = state.active_flow
        assert active is not None
        flow = ctx.registry.get(active.flow, Flow)
        node = _node(flow, active.node_id)
        state, exhausted = enter_node(state, ctx)
        if exhausted:
            request = escalation_request(ctx, "budget_exceeded")
            return StepOutcome(state, Stop.terminal, messages, events, escalation=request,
                               rejected_drafts=rejected)
        kind = node_kind(node)
        events.append(factory.node_entered(state, active.flow, node.id, kind or "", resume.kind))
        handler = HANDLERS.get(kind or "")
        if handler is None:
            raise IllegalTransition(f"no hay handler para el tipo de nodo {kind!r}")
        result = handler(node, state, ctx, resume)
        resume = NO_RESUME
        state = result.state
        events.extend(result.events)
        messages.extend(result.messages)
        rejected.extend(result.rejected)
        if result.stop is not None:
            if result.result_key is not None:
                state = _move(state, node, result.result_key)
            return StepOutcome(state, result.stop, messages, events, result.end_outcome, result.escalation,
                               result.confirmation, result.step_up, result.output, rejected)
        if result.result_key is None:
            raise IllegalTransition(f"el handler de {node.id} no devolvió rama ni detención")
        state = _move(state, node, result.result_key)
```
(Deja los imports agrupados y ordenados con `ruff --fix`.)

`agent_core/interpreter/__init__.py`: agrega `from agent_core.interpreter.loop import advance, start_flow` y `"advance"`, `"start_flow"` en `__all__`. En `tests/m02/harness.py` sustituye el import perezoso de `advance` por uno al inicio.

- [ ] **Step 4: Correr y confirmar que pasa**

Run: `uv run ruff check . --fix && uv run pytest tests/m02 -q && uv run mypy && uv run lint-imports`
Expected: PASS. Notas: `RuleConfig` exige exactamente uno de `policy`/`expr`, así que en `test_escalate…` la política va solo en `priority_expr` (un `escalate` no lo valida M0). Si `Message` de `respond` no admite `next` vacío (`test_transition_without_next_is_a_bug`), el nodo se construye igualmente porque `next` es `dict` libre.

- [ ] **Step 5: Commit**

```bash
git add agent_core/interpreter tests/m02
git commit -m "feat(m2): bucle de nodos, respond con plantilla, escalate, end y presupuestos

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Nodo `rule`

**Files:**
- Create: `agent_core/interpreter/handlers/rule.py`, `tests/m02/test_rule.py`
- Modify: `agent_core/interpreter/handlers/__init__.py`

**Interfaces:**
- Consumes: `evaluate`, `truthy`, `rule_data` (Task 6), `rule_inputs` (Task 5), `exact_ref`, `Events.rule_evaluated`.
- Produces: `handle_rule(node: RuleNode, state, ctx, resume) -> NodeResult` (`result_key` = `"true"` o `"false"`; emite `rule_evaluated {policy@v?, inputs (audit), result}`).

- [ ] **Step 1: Escribir las pruebas que fallan**

`tests/m02/test_rule.py`:
```python
from decimal import Decimal

from agent_core.domain import Decision, EntityRef, Policy
from agent_core.interpreter import Stop
from tests.m02.harness import World, fact, flow, slot

ENDS = [
    {"id": "si", "type": "end", "config": {"outcome": "resolved"}},
    {"id": "no", "type": "end", "config": {"outcome": "cancelled"}},
]


def _rule(config: dict[str, object]) -> dict[str, object]:
    return {"id": "r", "type": "rule", "config": config, "next": {"true": "si", "false": "no"}}


def _branch(out) -> str:  # type: ignore[no-untyped-def]
    return out.state.active_flow.node_id


def test_t_m2_02_claimed_slot_is_null() -> None:
    w = World()
    f = flow(_rule({"expr": {"==": [{"var": "slots.monto"}, None]}}), *ENDS)
    claimed = w.step(w.state(f, slots={"monto": slot(Decimal("900"), "claimed")}))
    assert _branch(claimed) == "si"  # claimed → null → la igualdad con null es verdadera
    validated = w.step(w.state(f, slots={"monto": slot(Decimal("900"))}))
    assert _branch(validated) == "no"


def test_t_m2_03_policy_emits_rule_evaluated_with_audit_inputs() -> None:
    w = World()
    policy = Policy.model_validate({
        "id": "umbral", "version": "1.0.0", "owner": "riesgo", "rationale": "x",
        "expr": {">": [{"var": "facts.monto_usd.value"}, 500]}})
    w.add(policy)
    f = flow(_rule({"policy": "umbral@1"}), *ENDS)
    out = w.step(w.state(f, facts={"monto_usd": fact(Decimal("620.00"), kind="compute")}))
    assert _branch(out) == "si"
    rule_events = [e for e in out.events if e.type == "rule_evaluated"]  # type: ignore[attr-defined]
    assert len(rule_events) == 1
    payload = rule_events[0].payload  # type: ignore[attr-defined]
    assert (payload.node_id, payload.policy, payload.result) == ("r", EntityRef.parse("umbral@1.0.0"), True)
    assert list(payload.inputs) == ["facts.monto_usd.value"]
    assert "620.00" not in rule_events[0].model_dump_json()


def test_inline_expr_has_no_policy_ref() -> None:
    w = World()
    f = flow(_rule({"expr": {"in": [{"var": "slots.tipo"}, ["a", "b"]]}}), *ENDS)
    out = w.step(w.state(f, slots={"tipo": slot("a")}))
    event = next(e for e in out.events if e.type == "rule_evaluated")  # type: ignore[attr-defined]
    assert event.payload.policy is None and event.payload.result is True  # type: ignore[attr-defined]


def test_rule_never_reads_decisions() -> None:
    w = World()
    decision = Decision(decision_id="decision-0001", value={"match": "unica"}, p_cal={"match": 0.9},
                        provider_used="s", model_version="1")
    f = flow(_rule({"expr": {"==": [{"var": "decisions.d.match"}, "unica"]}}), *ENDS)
    out = w.step(w.state(f, decisions={"d": decision}))
    assert _branch(out) == "no" and out.stop is Stop.terminal


def test_t_m2_12_decimal_boundary_through_a_rule() -> None:
    w = World()
    f = flow(_rule({"expr": {">": [{"var": "facts.m.value"}, 500]}}), *ENDS)
    assert _branch(w.step(w.state(f, facts={"m": fact(Decimal("500.00"))}))) == "no"
    assert _branch(w.step(w.state(f, facts={"m": fact(Decimal("500.01"))}))) == "si"
```
Nota: `w.step(...)` termina en el `end`; `_branch` lee `active_flow.node_id`, que al ser terminal queda en el nodo `end` al que llegó (`si` o `no`).

- [ ] **Step 2: Correr y confirmar que falla**

Run: `uv run pytest tests/m02/test_rule.py -q`
Expected: FAIL (`IllegalTransition: no hay handler para el tipo de nodo 'rule'`).

- [ ] **Step 3: Implementar**

`agent_core/interpreter/handlers/rule.py`:
```python
"""`rule` (M2 §3.3, ADR 0009): JSON Logic sobre hechos `full` y slots `validated`. Nunca `decisions` ni `claimed`."""

from agent_core.domain import EntityKind, EntityRef, JsonValue, Policy, RuleNode, RunState
from agent_core.interpreter.audit import rule_inputs
from agent_core.interpreter.context import Resume, StepContext
from agent_core.interpreter.events import Events
from agent_core.interpreter.handlers.base import NodeResult
from agent_core.interpreter.handlers.terminal import rule_data
from agent_core.interpreter.jsonlogic import evaluate, truthy
from agent_core.interpreter.refs import exact_ref


def _expression(node: RuleNode, ctx: StepContext) -> tuple[JsonValue, EntityRef | None]:
    if node.config.policy is None:
        return node.config.expr, None
    ref = exact_ref(ctx, EntityKind.policy, node.config.policy)
    return ctx.registry.get(ref, Policy).expr, ref


def handle_rule(node: RuleNode, state: RunState, ctx: StepContext, resume: Resume) -> NodeResult:
    expr, policy = _expression(node, ctx)
    reads: list[str] = []
    result = truthy(evaluate(expr, rule_data(state), reads))
    event = Events(ctx).rule_evaluated(state, node.id, policy, rule_inputs(state, ctx, reads), result)
    return NodeResult(state, result_key="true" if result else "false", events=[event])
```
Mueve `rule_data` a `handlers/base.py` si prefieres evitar que `rule.py` importe `terminal.py` (ambas opciones son válidas; deja una sola definición).

`handlers/__init__.py`: agrega `from agent_core.interpreter.handlers.rule import handle_rule` y `"rule": handle_rule` a `HANDLERS`.

- [ ] **Step 4: Correr y confirmar que pasa**

Run: `uv run pytest tests/m02 -q && uv run mypy && uv run ruff check .`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add agent_core/interpreter tests/m02/test_rule.py
git commit -m "feat(m2): nodo rule con politicas, slots claimed como null y entradas en vista audit

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 8: Nodo `collect`

**Files:**
- Create: `agent_core/interpreter/handlers/collect.py`, `tests/m02/test_collect.py`
- Modify: `agent_core/interpreter/handlers/__init__.py`

**Interfaces:**
- Consumes: `render_message`, `clear_attempts`, `escalate_now`, `Resume`.
- Produces: `handle_collect(node: CollectNode, state, ctx, resume) -> NodeResult`.
  - Sin `resume` de tipo `slot_answer`: emite `prompt_ref` y `Stop.awaiting_slot`.
  - Con `slot_answer`: valida (D14). Éxito → `slots[slot] = Slot(value, "validated", source_turn=state.turn_count)`, limpia `node_attempts[node]`, rama `ok`. Fallo → `node_attempts += 1`, `repair_turns_used += 1` (D15); si `attempts >= max_attempts` → rama `max_attempts`; si no, repregunta.
  - `validate_slot(validator: SlotValidator | None, raw: JsonValue) -> tuple[bool, JsonValue]`.

- [ ] **Step 1: Escribir las pruebas que fallan**

`tests/m02/test_collect.py`:
```python
from decimal import Decimal

import pytest

from agent_core.domain import SlotValidator
from agent_core.interpreter import Resume, Stop
from agent_core.interpreter.handlers.collect import validate_slot
from tests.m02.harness import World, flow, template

TAIL = [
    {"id": "fin", "type": "end", "config": {"outcome": "resolved"}},
    {"id": "esc", "type": "escalate", "config": {"reason_code": "low_confidence"}},
]


def _collect(**over: object) -> dict[str, object]:
    config = {"slot": "codigo", "prompt_ref": "t/pedir@1", "max_attempts": 2,
              "validator": {"kind": "regex", "value": "[0-9]{4}"}, **over}
    return {"id": "c", "type": "collect", "config": config, "next": {"ok": "fin", "max_attempts": "esc"}}


def _world() -> World:
    w = World()
    w.add(template("t/pedir", "Dime el código"))
    return w


def _answer(text: object) -> Resume:
    return Resume("slot_answer", text)


def test_first_entry_prompts_and_waits() -> None:
    w = _world()
    out = w.step(w.state(flow(_collect(), *TAIL)))
    assert out.stop is Stop.awaiting_slot and [m.text for m in out.messages] == ["Dime el código"]
    assert out.state.active_flow.node_id == "c"  # type: ignore[union-attr]


def test_valid_answer_stores_validated_slot_and_continues() -> None:
    w = _world()
    state = w.step(w.state(flow(_collect(), *TAIL))).state
    out = w.step(state, _answer(" 1234 "))
    slot = out.state.slots["codigo"]
    assert (slot.value, slot.status, slot.source_turn) == ("1234", "validated", out.state.turn_count)
    assert out.stop is Stop.terminal and out.end_outcome is not None
    assert "c" not in out.state.node_attempts


def test_t_m2_05_reprompts_then_exits_by_max_attempts() -> None:
    w = _world()
    state = w.step(w.state(flow(_collect(), *TAIL))).state
    retry = w.step(state, _answer("abc"))
    assert retry.stop is Stop.awaiting_slot and [m.text for m in retry.messages] == ["Dime el código"]
    assert (retry.state.node_attempts["c"], retry.state.repair_turns_used) == (1, 1)
    assert "codigo" not in retry.state.slots
    exhausted = w.step(retry.state, _answer("xyz"))
    assert exhausted.escalation is not None and exhausted.escalation.reason_code == "low_confidence"
    assert (exhausted.state.node_attempts["c"], exhausted.state.repair_turns_used) == (2, 2)
    assert exhausted.state.active_flow.node_id == "esc"  # type: ignore[union-attr]


def test_non_text_answer_is_invalid() -> None:
    w = _world()
    state = w.step(w.state(flow(_collect(), *TAIL))).state
    assert w.step(state, _answer(None)).stop is Stop.awaiting_slot


@pytest.mark.parametrize(("validator", "raw", "ok", "value"), [
    (None, "  hola ", True, "hola"),
    (None, "   ", False, None),
    (SlotValidator(kind="type", value="string"), "x", True, "x"),
    (SlotValidator(kind="type", value="integer"), "42", True, 42),
    (SlotValidator(kind="type", value="integer"), "4.2", False, None),
    (SlotValidator(kind="type", value="decimal"), "12.50", True, Decimal("12.50")),
    (SlotValidator(kind="type", value="decimal"), "12,50", False, None),
    (SlotValidator(kind="regex", value="[A-Z]{2}[0-9]+"), "AB12", True, "AB12"),
    (SlotValidator(kind="regex", value="[A-Z]{2}[0-9]+"), "AB12x", False, None),  # fullmatch
    (SlotValidator(kind="enum", value=["Débito", "Crédito"]), "débito", True, "Débito"),
    (SlotValidator(kind="enum", value=["Débito", "Crédito"]), "otra", False, None),
])
def test_validate_slot(validator: SlotValidator | None, raw: object, ok: bool, value: object) -> None:
    assert validate_slot(validator, raw) == (ok, value)  # type: ignore[arg-type]


def test_decide_validator_is_not_supported_yet() -> None:
    with pytest.raises(NotImplementedError):
        validate_slot(SlotValidator(kind="decide", value="m@1"), "texto")
```

- [ ] **Step 2: Correr y confirmar que falla**

Run: `uv run pytest tests/m02/test_collect.py -q`
Expected: FAIL (sin handler `collect`).

- [ ] **Step 3: Implementar**

`agent_core/interpreter/handlers/collect.py`:
```python
"""`collect` (M2 §3.3): pregunta, valida y reintenta hasta `max_attempts`. Los validadores: D14."""

import re
from decimal import Decimal

from agent_core.domain import CollectNode, JsonValue, RunState, Slot, SlotValidator
from agent_core.interpreter.context import Resume, StepContext, Stop
from agent_core.interpreter.handlers.base import NodeResult, clear_attempts, escalate_now
from agent_core.interpreter.resolve import MissingPath
from agent_core.interpreter.templates import render_message

_INTEGER = re.compile(r"[+-]?[0-9]+")
_DECIMAL = re.compile(r"[+-]?[0-9]+(?:\.[0-9]+)?")


def _typed(kind: JsonValue, text: str) -> tuple[bool, JsonValue]:
    if kind == "string":
        return bool(text), text or None
    if kind == "integer":
        return (True, int(text)) if _INTEGER.fullmatch(text) else (False, None)
    if kind == "decimal":
        return (True, Decimal(text)) if _DECIMAL.fullmatch(text) else (False, None)
    raise ValueError(f"tipo de slot desconocido: {kind!r}")


def validate_slot(validator: SlotValidator | None, raw: JsonValue) -> tuple[bool, JsonValue]:
    """`(ok, valor)`; el valor ya viene normalizado (recortado, tipado o canónico del enum)."""
    if not isinstance(raw, str):
        return False, None
    text = raw.strip()
    if validator is None:
        return bool(text), text or None
    match validator.kind:
        case "type":
            return _typed(validator.value, text)
        case "regex":
            try:
                ok = re.fullmatch(str(validator.value), text) is not None
            except re.error:
                ok = False
            return ok, text if ok else None
        case "enum":
            allowed = validator.value if isinstance(validator.value, list) else []
            for option in allowed:
                if isinstance(option, str) and option.casefold() == text.casefold():
                    return True, option
            return False, None
        case _:
            raise NotImplementedError("validador de slot 'decide': pendiente (M2 §11)")


def handle_collect(node: CollectNode, state: RunState, ctx: StepContext, resume: Resume) -> NodeResult:
    cfg = node.config
    try:
        prompt = render_message(state, ctx, cfg.prompt_ref)
    except MissingPath:
        return escalate_now(state, ctx, "validation_failed")
    if resume.kind != "slot_answer":
        return NodeResult(state, stop=Stop.awaiting_slot, messages=[prompt])
    ok, value = validate_slot(cfg.validator, resume.value)
    if ok:
        slot = Slot(value=value, status="validated", source_turn=state.turn_count)
        state = clear_attempts(state, node.id).model_copy(update={"slots": {**state.slots, cfg.slot: slot}})
        return NodeResult(state, result_key="ok")
    attempts = state.node_attempts.get(node.id, 0) + 1
    state = state.model_copy(update={
        "node_attempts": {**state.node_attempts, node.id: attempts},
        "repair_turns_used": state.repair_turns_used + 1,  # D15: M4 lo lee
    })
    if attempts >= cfg.max_attempts:
        return NodeResult(state, result_key="max_attempts")
    return NodeResult(state, stop=Stop.awaiting_slot, messages=[prompt])
```
`handlers/__init__.py`: agrega `"collect": handle_collect`.

- [ ] **Step 4: Correr y confirmar que pasa**

Run: `uv run pytest tests/m02 -q && uv run mypy && uv run ruff check .`
Expected: PASS. Si `Slot(value=Decimal(...))` falla la validación de `JsonValue`, confirma que `Slot.value` acepta `Decimal` (lo hace: `JsonValue` incluye `Decimal`).

- [ ] **Step 5: Commit**

```bash
git add agent_core/interpreter tests/m02/test_collect.py
git commit -m "feat(m2): nodo collect con validadores type, regex y enum y salida por max_attempts

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 9: Nodo `tool` (lectura y `compute`), reintentos, breaker y step-up

**Files:**
- Create: `agent_core/interpreter/calls.py`, `agent_core/interpreter/handlers/tool.py`, `tests/m02/test_tool.py`
- Modify: `agent_core/interpreter/handlers/__init__.py`

**Interfaces:**
- Consumes: `resolve_args`, `input_ids`, `exact_ref`, `ViewsAudit`, `Events.tool_called`/`access_denied`, `request_step_up`, `clear_attempts`, `CircuitBreaker`, `ctx.tools.definition`, `ctx.tools.execute`.
- Produces:
  - `tool_call_context(state, ctx) -> ToolCallContext` (run, release, principal, on_behalf_of, subject, `turn_id`).
  - `handle_tool(node: ToolNode, state, ctx, resume) -> NodeResult`:
    - Ruta inexistente en `args` → rama `error`, sin llamar a la tool.
    - Hasta **2 reintentos** (3 intentos) solo si `tool_def.idempotent` y el estado es `timeout`/`error`; un corte del breaker no se reintenta. Cada intento emite `tool_called` (con `attempt` y `latency_ms`, `0` en un corte).
    - `ok` → `facts[save_as] = Fact(fact_id=ctx.ids.new_id(IdKind.fact), value=result_full, source=FactSource(kind="compute"|"tool", ref="tool@X.Y.Z", inputs=input_ids(...)), ts=now)`, limpia `node_attempts`, rama `ok`.
    - `denied` → rama `denied` + evento `access_denied{tool_denied}`. `step_up_required` → `request_step_up` (D9). `uncertain` en una lectura → rama `error`. Una excepción de `execute` → `error` (nunca se propaga; `BaseException` sí).

- [ ] **Step 1: Escribir las pruebas que fallan**

`tests/m02/test_tool.py`:
```python
from datetime import timedelta
from decimal import Decimal

from agent_core.domain import AuthLevel, Decision
from agent_core.interpreter import CircuitBreaker, Resume, Stop
from agent_core.ports import ToolStatus
from testing.builders import NOW, principal
from testing.fakes.tools import Scripted
from tests.m02.harness import SlowTools, World, fact, flow, tool_def

NEXT = {"ok": "fin", "error": "esc", "timeout": "esc", "denied": "esc"}
TAIL = [
    {"id": "fin", "type": "end", "config": {"outcome": "resolved"}},
    {"id": "esc", "type": "escalate", "config": {"reason_code": "tool_failure"}},
]


def _tool(config: dict[str, object]) -> dict[str, object]:
    return {"id": "t", "type": "tool", "config": {"tool": "buscar@1", "save_as": "res", **config}, "next": NEXT}


def _events(out, kind: str):  # type: ignore[no-untyped-def]
    return [e for e in out.events if e.type == kind]


def _node(out) -> str:  # type: ignore[no-untyped-def]
    return out.state.active_flow.node_id


def test_read_ok_stores_full_value_and_provenance() -> None:
    w = World()
    w.add_tool(tool_def("buscar", source="tx"), handler=lambda a: {"echo": a["texto"], "amount": Decimal("9.5")})
    f = flow(_tool({"args": {"texto": "slots.q"}}), *TAIL)
    from tests.m02.harness import slot

    out = w.step(w.state(f, slots={"q": slot("cargo")}))
    stored = out.state.facts["res"]
    assert stored.value == {"echo": "cargo", "amount": Decimal("9.5")}  # vista full en el hecho
    assert (stored.source.kind, stored.source.ref, stored.fact_id) == ("tool", "buscar@1.0.0", "fact-0001")
    assert stored.ts == NOW and _node(out) == "fin"
    (called,) = _events(out, "tool_called")
    assert called.payload.status is ToolStatus.ok and called.payload.attempt == 1
    assert called.payload.args == {"texto": "***"} and called.payload.result_fp is not None
    assert "cargo" not in called.model_dump_json()  # el arg (slot sin clasificar) sale en vista audit
    assert w.tools.calls[0].args == {"texto": "cargo"}  # pero la tool recibe el valor real


def test_t_m2_04_compute_records_provenance_of_inputs() -> None:
    w = World()
    w.add_tool(tool_def("convertir", "compute"), handler=lambda a: a["monto"] * a["tasa"])
    node = _tool({"tool": "convertir@1", "save_as": "monto_usd", "args": {
        "monto": "facts.tx.value.amount", "moneda": "facts.tx.value.currency", "tasa": "decisions.d.rate",
        "destino": "USD"}})
    decision = Decision(decision_id="decision-0009", value={"rate": Decimal("2")}, p_cal={},
                        provider_used="s", model_version="1")
    state = w.state(flow(node, *TAIL), facts={"tx": fact({"amount": Decimal("10.5"), "currency": "COP"},
                                                          fact_id="fact-0007")},
                    decisions={"d": decision})
    out = w.step(state)
    result = out.state.facts["monto_usd"]
    assert result.value == Decimal("21.0")
    assert (result.source.kind, result.source.ref) == ("compute", "convertir@1.0.0")
    assert result.source.inputs == ["fact-0007", "decision-0009"]


def test_missing_arg_path_takes_error_branch_without_calling() -> None:
    w = World()
    w.add_tool(tool_def("buscar"))
    out = w.step(w.state(flow(_tool({"args": {"texto": "slots.no_existe"}}), *TAIL)))
    assert _node(out) == "esc" and w.tools.calls == [] and _events(out, "tool_called") == []


def test_t_m2_09_retries_only_idempotent_tools() -> None:
    w = World()
    script = [Scripted(ToolStatus.timeout), Scripted(ToolStatus.error), Scripted(ToolStatus.ok, result={"a": 1})]
    w.add_tool(tool_def("buscar"), script=script)
    out = w.step(w.state(flow(_tool({}), *TAIL)))
    assert _node(out) == "fin" and len(w.tools.calls) == 3
    assert [e.payload.attempt for e in _events(out, "tool_called")] == [1, 2, 3]

    exhausted = World()
    exhausted.add_tool(tool_def("buscar"), script=[Scripted(ToolStatus.timeout)] * 3)
    out = exhausted.step(exhausted.state(flow(_tool({}), *TAIL)))
    assert _node(out) == "esc" and len(exhausted.tools.calls) == 3 and "res" not in out.state.facts

    single = World()
    single.add_tool(tool_def("buscar", idempotent=False), script=[Scripted(ToolStatus.timeout)])
    out = single.step(single.state(flow(_tool({}), *TAIL)))
    assert _node(out) == "esc" and len(single.tools.calls) == 1


def test_denied_takes_denied_branch_and_emits_access_denied() -> None:
    w = World()
    w.add_tool(tool_def("buscar"), script=[Scripted(ToolStatus.denied)])
    out = w.step(w.state(flow(_tool({}), *TAIL)))
    assert _node(out) == "esc" and len(w.tools.calls) == 1
    (denied,) = _events(out, "access_denied")
    assert denied.payload.reason.value == "tool_denied" and str(denied.payload.tool) == "buscar@1.0.0"


def test_latency_is_measured_with_the_clock() -> None:
    w = World()
    w.add_tool(tool_def("buscar"), handler=lambda a: {"ok": True})
    slow = SlowTools(w.tools, w.clock, timedelta(milliseconds=250))
    out = w.step(w.state(flow(_tool({}), *TAIL)), tools=slow)
    assert _events(out, "tool_called")[0].payload.latency_ms == 250


def test_circuit_breaker_cuts_immediately_and_does_not_retry_the_cut() -> None:
    w = World()
    w.add_tool(tool_def("buscar"), script=[Scripted(ToolStatus.error, error="boom")] * 5)
    out = w.step(w.state(flow(_tool({}), *TAIL)), breaker=CircuitBreaker(threshold=2))
    assert _node(out) == "esc" and len(w.tools.calls) == 2  # el 3.er intento lo corta el breaker
    calls = _events(out, "tool_called")
    assert [(c.payload.status, c.payload.error, c.payload.latency_ms) for c in calls][-1] == (
        ToolStatus.error, "circuit_open", 0)


def test_tool_exception_becomes_error_branch() -> None:
    w = World()
    w.add_tool(tool_def("buscar", idempotent=False))

    class Boom(SlowTools):
        def execute(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            raise RuntimeError("datos sensibles no deben filtrarse")

    out = w.step(w.state(flow(_tool({}), *TAIL)), tools=Boom(w.tools, w.clock, timedelta(0)))
    assert _node(out) == "esc"
    (called,) = _events(out, "tool_called")
    assert called.payload.error == "RuntimeError" and "sensibles" not in called.model_dump_json()


def _step_up_world(max_attempts: int) -> tuple[World, object]:
    w = World()
    w.add_tool(tool_def("buscar", level="step_up"), handler=lambda a: {"ok": True})
    node = _tool({"step_up_max_attempts": max_attempts})
    return w, flow(node, *TAIL)


def test_t_m2_06_step_up_stops_on_same_node_and_retry_continues() -> None:
    w, f = _step_up_world(2)
    first = w.step(w.state(f))  # type: ignore[arg-type]
    assert first.stop is Stop.awaiting_step_up and _node(first) == "t"
    assert first.step_up is not None and first.step_up.required_level is AuthLevel.step_up
    assert first.state.node_attempts == {"t": 1} and w.tools.calls[0].status is ToolStatus.step_up_required
    (requested,) = _events(first, "step_up_requested")
    assert (requested.payload.node_id, requested.payload.attempt) == ("t", 1)
    elevated = first.state.model_copy(update={"principal": principal(auth={"level": "step_up", "at": NOW})})
    second = w.step(elevated, Resume("step_up_retry"))
    assert second.stop is Stop.terminal and _node(second) == "fin"
    assert second.state.node_attempts == {} and "res" in second.state.facts


def test_t_m2_07_step_up_exhausted_escalates_auth_insufficient() -> None:
    w, f = _step_up_world(1)
    first = w.step(w.state(f))  # type: ignore[arg-type]
    assert first.stop is Stop.awaiting_step_up
    second = w.step(first.state, Resume("step_up_retry"))  # sigue sin elevar
    assert second.stop is Stop.terminal and second.escalation is not None
    assert second.escalation.reason_code == "auth_insufficient"
    assert [e.payload.attempt for e in _events(second, "step_up_requested")] == [2]
```

- [ ] **Step 2: Correr y confirmar que falla**

Run: `uv run pytest tests/m02/test_tool.py -q`
Expected: FAIL (sin handler `tool`).

- [ ] **Step 3: Implementar**

`agent_core/interpreter/calls.py`:
```python
"""Contexto de llamada a tools (M0 §2.9). `build_action_context` llega en la Task 11."""

from agent_core.domain import RunState
from agent_core.interpreter.context import StepContext
from agent_core.ports import ToolCallContext


def tool_call_context(state: RunState, ctx: StepContext) -> ToolCallContext:
    return ToolCallContext(run_id=state.run_id, release=state.release, principal=state.principal,
                           on_behalf_of=state.on_behalf_of, subject=state.subject, turn_id=ctx.turn_id)
```

`agent_core/interpreter/handlers/tool.py`:
```python
"""`tool` de lectura y `compute` (M2 §3.3, §3.4, §5). Las escrituras van por `write.py`."""

from agent_core.domain import (
    EngineEvent,
    EntityKind,
    EntityRef,
    Fact,
    FactSource,
    JsonValue,
    RiskClass,
    RunState,
    ToolCalledPayload,
    ToolNode,
)
from agent_core.interpreter.audit import ViewsAudit
from agent_core.interpreter.calls import tool_call_context
from agent_core.interpreter.context import Resume, StepContext
from agent_core.interpreter.events import Events
from agent_core.interpreter.handlers.base import NodeResult, clear_attempts, request_step_up
from agent_core.interpreter.refs import exact_ref
from agent_core.interpreter.resolve import MissingPath, input_ids, resolve_args
from agent_core.ports import IdKind, ToolCallContext, ToolResult, ToolStatus

MAX_RETRIES = 2
_RETRIABLE = frozenset({ToolStatus.timeout, ToolStatus.error})
_BRANCH = {ToolStatus.ok: "ok", ToolStatus.error: "error", ToolStatus.timeout: "timeout",
           ToolStatus.denied: "denied", ToolStatus.uncertain: "error"}
_NS_PER_MS = 1_000_000


def _call(ctx: StepContext, tool: EntityRef, args: dict[str, JsonValue],
          call: ToolCallContext) -> tuple[ToolResult, int, bool]:
    """`(resultado, latency_ms, cortado_por_el_breaker)`. Nunca propaga `Exception` de la tool."""
    if ctx.breaker.is_open(tool, ctx.clock.now()):
        cut = ToolResult(status=ToolStatus.error, call_id=ctx.ids.new_id(IdKind.call), error="circuit_open")
        return cut, 0, True
    started = ctx.clock.monotonic_ns()
    try:
        result = ctx.tools.execute(tool, args, dict(ctx.bound_params), call)
    except Exception as exc:  # noqa: BLE001 - una tool caída es una rama del flow; el mensaje puede traer PII
        result = ToolResult(status=ToolStatus.error, call_id=ctx.ids.new_id(IdKind.call),
                            error=type(exc).__name__)
    return result, (ctx.clock.monotonic_ns() - started) // _NS_PER_MS, False


def handle_tool(node: ToolNode, state: RunState, ctx: StepContext, resume: Resume) -> NodeResult:
    cfg = node.config
    tool = exact_ref(ctx, EntityKind.tool, cfg.tool)
    definition = ctx.tools.definition(tool)
    try:
        args = resolve_args(state, cfg.args)
    except MissingPath:
        return NodeResult(state, result_key="error")
    events = Events(ctx)
    audit = ViewsAudit(ctx.views, ctx.vault)
    audit_args = audit.args(args, definition)
    call = tool_call_context(state, ctx)
    emitted: list[EngineEvent] = []
    retries = MAX_RETRIES if definition.idempotent else 0
    for attempt in range(1, retries + 2):
        result, latency_ms, cut = _call(ctx, tool, args, call)
        ok = result.status is ToolStatus.ok
        shown, fingerprint = audit.result(result.result_full, definition) if ok else (None, None)
        payload = ToolCalledPayload(
            node_id=node.id, tool=tool, call_id=result.call_id, status=result.status, args=audit_args,
            result=shown, result_fp=fingerprint, error=result.error, attempt=attempt, latency_ms=latency_ms)
        emitted.append(events.tool_called(state, payload))
        if result.status in _RETRIABLE and not cut:
            ctx.breaker.record_failure(tool, ctx.clock.now())
            if attempt <= retries:
                continue
        break
    if result.status is ToolStatus.step_up_required:
        level = result.required_level or definition.min_auth_level
        return request_step_up(state, ctx, node.id, level, cfg.step_up_max_attempts, emitted)
    if result.status is ToolStatus.ok:
        ctx.breaker.record_success(tool)
        kind = "compute" if definition.risk_class is RiskClass.compute else "tool"
        fact = Fact(fact_id=ctx.ids.new_id(IdKind.fact), value=result.result_full,
                    source=FactSource(kind=kind, ref=str(tool), inputs=input_ids(state, cfg.args)),
                    ts=ctx.clock.now())
        state = clear_attempts(state, node.id).model_copy(update={"facts": {**state.facts, cfg.save_as: fact}})
    elif result.status is ToolStatus.denied:
        emitted.append(events.access_denied(state, tool))
    return NodeResult(state, result_key=_BRANCH[result.status], events=emitted)
```
`handlers/__init__.py`: agrega `"tool": handle_tool`.

- [ ] **Step 4: Correr y confirmar que pasa**

Run: `uv run pytest tests/m02 -q && uv run mypy && uv run ruff check .`
Expected: PASS. Ajustes esperables: (a) el `fact_id` de la primera llamada depende del orden en que `FakeIds` emite `call` y `fact`; los contadores son por `kind`, así que `fact-0001` es correcto; (b) si `test_read_ok…` falla en `args == {"texto": "***"}`, revisa `ViewsAudit.args` (slot sin clasificar bajo el `source` de la tool → `pii_direct` → `***`); si el catálogo lo dejara pasar, relaja la aserción a `"cargo" not in dumps`.

- [ ] **Step 5: Commit**

```bash
git add agent_core/interpreter tests/m02/test_tool.py
git commit -m "feat(m2): nodo tool de lectura y compute con reintentos, breaker, procedencia y step-up

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 10: Nodo `decide`

**Files:**
- Create: `agent_core/interpreter/handlers/decide.py`, `tests/m02/test_decide.py`
- Modify: `agent_core/interpreter/handlers/__init__.py`

**Interfaces:**
- Consumes: `ctx.decisions.decide(model, inputs_model_view, locale) -> DecisionResult` (Task 1), `Projector.model_value(path, wrap_slots=True)`, `model_budget_exhausted`, `charge_model`, `exact_ref`, `DecisionModelDef`.
- Produces: `handle_decide(node: DecideNode, state, ctx, resume) -> NodeResult`.
  - Entradas: `node.config.input_view` si existe, si no `model_def.input_view`; cada ruta va en vista `model` (slots envueltos, D8). Una ruta ausente → rama `low_confidence`.
  - Guarda `decisions[save_as] = result.decision` y agrega `result.events` (`decision_made`). Suma `model_calls`, `tokens` y `cost_usd` a `budgets_used`.
  - Rama = `decision.value[branch_on]` si `above_threshold[branch_on]`, si no `low_confidence`. Si `branch_on` no es un campo calibrado, no hay umbral y se ramifica por su valor.
  - Con `model_budget_exhausted` antes de llamar → `escalate(budget_exceeded)`.

- [ ] **Step 1: Escribir las pruebas que fallan**

`tests/m02/test_decide.py`:
```python
from decimal import Decimal

from agent_core.domain import DecisionModelDef
from agent_core.interpreter import Stop
from testing.fakes.decision import make_decision
from tests.m02.harness import AGENT, World, fact, flow, slot
from agent_core.domain import Budgets

NEXT = {"unica": "si", "ninguna": "no", "low_confidence": "poco"}
TAIL = [
    {"id": "si", "type": "end", "config": {"outcome": "resolved"}},
    {"id": "no", "type": "end", "config": {"outcome": "cancelled"}},
    {"id": "poco", "type": "escalate", "config": {"reason_code": "low_confidence"}},
]
MODEL = DecisionModelDef.model_validate({
    "id": "match", "version": "2.0.0", "output_schema": {"type": "object"}, "calibrated_fields": ["match"],
    "input_view": ["slots.q"], "providers": [{"provider": "classifier"}], "calibration": {"method": "none"}})


def _decide(**config: object) -> dict[str, object]:
    return {"id": "d", "type": "decide", "next": NEXT,
            "config": {"model": "match@2", "branch_on": "match", "save_as": "coincide", **config}}


def _world() -> World:
    w = World()
    w.add(MODEL)
    return w


def _node(out) -> str:  # type: ignore[no-untyped-def]
    return out.state.active_flow.node_id


def test_above_threshold_branches_on_value_and_stores_decision() -> None:
    w = _world()
    w.decisions.push(make_decision({"match": "unica", "transaction": "tx-1"}, {"match": True}, tokens=40,
                                   cost="0.002"))
    state = w.state(flow(_decide(), *TAIL), slots={"q": slot("cargo raro")})
    out = w.step(state)
    assert _node(out) == "si"
    assert out.state.decisions["coincide"].value == {"match": "unica", "transaction": "tx-1"}
    used = out.state.budgets_used
    assert (used.turn_model_calls, used.run_tokens, used.run_cost) == (1, 40, Decimal("0.002"))
    (model_ref, inputs, locale) = w.decisions.calls[0]
    assert str(model_ref) == "match@2.0.0" and locale == "es"
    assert list(inputs) == ["slots.q"]


def test_inputs_are_model_view_with_wrapped_slots() -> None:
    w = _world()
    w.decisions.push(make_decision({"match": "unica"}, {"match": True}))
    w.step(w.state(flow(_decide(), *TAIL), slots={"q": slot("cargo raro")}))
    text = w.decisions.calls[0][1]["slots.q"]
    assert isinstance(text, str) and text.startswith("<datos_no_confiables") and "cargo raro" in text


def test_node_input_view_overrides_the_model_default() -> None:
    w = _world()
    w.decisions.push(make_decision({"match": "unica"}, {"match": True}))
    node = _decide(input_view=["facts.cand.value.amount"])
    out = w.step(w.state(flow(node, *TAIL), facts={"cand": fact({"amount": Decimal("3")})}))
    assert _node(out) == "si" and w.decisions.calls[0][1] == {"facts.cand.value.amount": Decimal("3")}


def test_below_threshold_or_missing_input_is_low_confidence() -> None:
    w = _world()
    w.decisions.push(make_decision({"match": "unica"}, {"match": False}))
    assert _node(w.step(w.state(flow(_decide(), *TAIL), slots={"q": slot("x")}))) == "poco"
    missing = _world()
    out = missing.step(missing.state(flow(_decide(), *TAIL), slots={"q": slot("x", "claimed")}))
    assert _node(out) == "poco" and missing.decisions.calls == []  # claimed = ausente (D7), no se llama


def test_uncalibrated_branch_field_branches_on_its_value() -> None:
    w = World()
    w.add(MODEL.model_copy(update={"calibrated_fields": []}))
    w.decisions.push(make_decision({"match": "ninguna"}, {}))
    out = w.step(w.state(flow(_decide(), *TAIL), slots={"q": slot("x")}))
    assert _node(out) == "no"


def test_model_call_budget_escalates_before_calling() -> None:
    w = World(agent=AGENT.model_copy(update={"budgets": Budgets.model_validate(
        {**AGENT.budgets.model_dump(), "max_model_calls_per_turn": 1})}))
    w.add(MODEL)
    state = w.state(flow(_decide(), *TAIL), slots={"q": slot("x")},
                    budgets_used={"turn_model_calls": 1, "turn_started_at": w.clock.now()})
    out = advance(state, w.ctx(), Resume())  # sin `w.step`: `begin_turn` reiniciaría turn_model_calls
    assert out.stop is Stop.terminal and out.escalation is not None
    assert out.escalation.reason_code == "budget_exceeded" and w.decisions.calls == []
```
(En los imports del archivo: `from agent_core.interpreter import Resume, Stop, advance`.)

- [ ] **Step 2: Correr y confirmar que falla**

Run: `uv run pytest tests/m02/test_decide.py -q`
Expected: FAIL (sin handler `decide`).

- [ ] **Step 3: Implementar**

`agent_core/interpreter/handlers/decide.py`:
```python
"""`decide` (M2 §3.3): entrada en vista `model`, umbral decidido por M5, rama = valor o `low_confidence`."""

from agent_core.domain import DecideNode, DecisionModelDef, EntityKind, JsonValue, RunState
from agent_core.interpreter.budgets import charge_model, model_budget_exhausted
from agent_core.interpreter.context import Resume, StepContext
from agent_core.interpreter.handlers.base import NodeResult, escalate_now
from agent_core.interpreter.projection import Projector
from agent_core.interpreter.refs import exact_ref
from agent_core.interpreter.resolve import MissingPath, parse_runtime_path

LOW_CONFIDENCE = "low_confidence"


def _inputs(paths: list[str], state: RunState, ctx: StepContext) -> dict[str, JsonValue]:
    projector = Projector(state, ctx)
    inputs: dict[str, JsonValue] = {}
    for raw in paths:
        path = parse_runtime_path(raw)
        if path is None:
            raise MissingPath(raw)
        inputs[raw] = projector.model_value(path, wrap_slots=True)
    return inputs


def handle_decide(node: DecideNode, state: RunState, ctx: StepContext, resume: Resume) -> NodeResult:
    cfg = node.config
    if model_budget_exhausted(state, ctx):
        return escalate_now(state, ctx, "budget_exceeded")
    ref = exact_ref(ctx, EntityKind.decision_model, cfg.model)
    model = ctx.registry.get(ref, DecisionModelDef)
    paths = cfg.input_view if cfg.input_view is not None else model.input_view
    try:
        inputs = _inputs(paths, state, ctx)
    except MissingPath:
        return NodeResult(state, result_key=LOW_CONFIDENCE)
    result = ctx.decisions.decide(ref, inputs, ctx.locale)
    state = charge_model(state, calls=result.model_calls, tokens=result.tokens, cost=result.cost_usd)
    state = state.model_copy(update={"decisions": {**state.decisions, cfg.save_as: result.decision}})
    value = result.decision.value.get(cfg.branch_on)
    calibrated = cfg.branch_on in model.calibrated_fields
    above = result.above_threshold.get(cfg.branch_on, not calibrated)
    key = value if above and isinstance(value, str) else LOW_CONFIDENCE
    return NodeResult(state, result_key=key, events=list(result.events))
```
`handlers/__init__.py`: agrega `"decide": handle_decide`.

- [ ] **Step 4: Correr y confirmar que pasa**

Run: `uv run pytest tests/m02 -q && uv run mypy && uv run ruff check . --fix`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add agent_core/interpreter tests/m02/test_decide.py
git commit -m "feat(m2): nodo decide con vista model, umbral de M5 y presupuesto de modelo

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 11: Escritura, `confirm` y `verify` (vía M3)

**Files:**
- Modify: `agent_core/interpreter/calls.py` (agrega `build_action_context`)
- Create: `agent_core/interpreter/handlers/write.py`, `tests/m02/test_write.py`
- Modify: `agent_core/interpreter/handlers/__init__.py`

**Interfaces:**
- Consumes: `ActionManager.propose/answer/execute_write/verify` (M3), `ActionContext` (M3), `make_resolver`, `render_message`, `ViewsAudit`, `evaluate`/`truthy`, `request_step_up`, `Events.access_denied`, `tool_call_context`.
- Produces:
  - `build_action_context(state, ctx) -> ActionContext(uow_factory=ctx.uow_factory, tools=ctx.tools, call=tool_call_context(...), render=..., predicate=lambda expr, data: truthy(evaluate(expr, data)), resolve=make_resolver(ctx), record=ctx.record, audit=ViewsAudit(...), bound_params=ctx.bound_params)`.
  - `handle_confirm`: sin `confirm_answer` → `resolve_args` + `ctx.actions.propose(...)` → `Stop.awaiting_confirmation` con `confirmation=ConfirmationPrompt`; con `confirm_answer` → `ctx.actions.answer(state, node, answer, resume.token, ...)` → rama `yes|no|unclear|max_attempts` (un valor que no sea `yes|no|unclear` cuenta como `unclear`). `unclear` vuelve a entrar al nodo por su `next` y M3 rota el token. Ruta ausente → `escalate(validation_failed)`.
  - `handle_write` (`WriteToolNode`): `ctx.actions.execute_write(...)`; **no** agrega los eventos que devuelve (ya persistidos). `ok`/`uncertain`/`denied` → rama; `denied` agrega `access_denied`; `step_up_required` → `request_step_up` con `tool_def.min_auth_level` de la acción y `step_up_max_attempts` del nodo.
  - `handle_verify`: `ctx.actions.verify(...)` → rama `verified|failed`; devuelve los eventos de M3 (M4 los persiste).

- [ ] **Step 1: Escribir las pruebas que fallan**

`tests/m02/test_write.py`:
```python
from agent_core.domain import ActionState, AuthLevel
from agent_core.interpreter import Resume, Stop
from agent_core.ports import ToolStatus
from testing.builders import NOW, principal
from testing.fakes.tools import Scripted
from tests.m02.harness import World, fact, flow, slot, template, tool_def

CONFIRM = {"id": "confirmar", "type": "confirm", "config": {
    "action": {"tool": "radicar@1", "args": {"transaction_id": "facts.tx.value.transaction_id",
                                              "descripcion": "slots.d"}},
    "summary_template": "t/resumen@1", "max_attempts": 2},
    "next": {"yes": "radicar", "no": "cancelado", "unclear": "confirmar", "max_attempts": "cancelado"}}
WRITE = {"id": "radicar", "type": "tool", "config": {"action_from": "confirmar", "save_as": "pqr"},
         "next": {"ok": "verificar", "uncertain": "verificar", "denied": "esc"}}
VERIFY = {"id": "verificar", "type": "verify", "config": {
    "readback": "obtener@1", "by": "idempotency_key",
    "predicate": {"==": [{"var": "readback.status"}, "Open"]}, "save_as": "pqr_ok"},
    "next": {"verified": "fin", "failed": "esc"}}
TAIL = [
    {"id": "fin", "type": "end", "config": {"outcome": "resolved"}},
    {"id": "cancelado", "type": "end", "config": {"outcome": "cancelled"}},
    {"id": "esc", "type": "escalate", "config": {"reason_code": "tool_failure"}},
]


def _world(*, write_script=(), level: str = "session") -> tuple[World, object]:  # type: ignore[no-untyped-def]
    w = World()
    w.add(template("t/resumen", "¿Radico la disputa?"))
    write = tool_def("radicar", "write_reversible", level=level)
    w.add_tool(write, script=write_script, handler=lambda a: {"status": "Open", "id": "pqr-1", **a})
    readback = tool_def("obtener")
    w.add(readback)
    w.tools.register_readback(readback, of=w.tools_ref("radicar"))
    f = flow(CONFIRM, WRITE, VERIFY, *TAIL)
    return w, f


def _state(w: World, f):  # type: ignore[no-untyped-def]
    return w.persist(w.state(f, slots={"d": slot("cargo")}, facts={"tx": fact({"transaction_id": "tx-1"})}))


def _types(events) -> list[str]:  # type: ignore[no-untyped-def]
    return [e.type for e in events]


def test_confirm_proposes_then_yes_by_button_executes_and_verifies() -> None:
    w, f = _world()
    state = _state(w, f).model_copy(update={"active_flow": {"flow": "f@1.0.0", "node_id": "confirmar"}})
    proposed = w.step(state)
    assert proposed.stop is Stop.awaiting_confirmation and proposed.confirmation is not None
    assert proposed.confirmation.summary.text == "¿Radico la disputa?"
    assert proposed.state.active_flow.node_id == "confirmar"  # type: ignore[union-attr]
    state = w.persist(proposed.state)
    done = w.step(state, Resume("confirm_answer", "yes", token=proposed.confirmation.token))
    assert done.stop is Stop.terminal and done.end_outcome is not None
    assert [a.state for a in done.state.actions] == [ActionState.verified]
    assert set(done.state.facts) == {"pqr", "pqr_ok"}
    # Los eventos del commit de M3 ya están en el almacén y NO se repiten en `done.events`.
    persisted = _types(w.store.events["run-0001"])
    assert "action_dispatched" in persisted and "action_dispatched" not in _types(done.events)
    assert "action_confirmed" in _types(done.events) and "action_verified" in _types(done.events)


def test_confirm_no_cancels_and_unclear_re_asks_with_a_rotated_token() -> None:
    w, f = _world()
    start = _state(w, f).model_copy(update={"active_flow": {"flow": "f@1.0.0", "node_id": "confirmar"}})
    first = w.step(start)
    assert first.confirmation is not None
    state = w.persist(first.state)
    unclear = w.step(state, Resume("confirm_answer", "unclear"))
    assert unclear.stop is Stop.awaiting_confirmation and unclear.confirmation is not None
    assert unclear.confirmation.token != first.confirmation.token  # M3 rota el token en la reentrada
    no = w.step(w.persist(unclear.state), Resume("confirm_answer", "no"))
    assert no.stop is Stop.terminal and no.state.active_flow.node_id == "cancelado"  # type: ignore[union-attr]
    assert [a.state for a in no.state.actions] == [ActionState.cancelled]


def test_confirm_max_attempts_and_missing_args() -> None:
    w, f = _world()
    start = _state(w, f).model_copy(update={"active_flow": {"flow": "f@1.0.0", "node_id": "confirmar"}})
    state = w.step(start).state
    for _ in range(2):
        out = w.step(w.persist(state), Resume("confirm_answer", "unclear"))
        state = out.state
    assert out.stop is Stop.terminal and state.active_flow.node_id == "cancelado"  # type: ignore[union-attr]
    empty = World()
    empty.add(template("t/resumen", "x"), tool_def("radicar", "write_reversible"))
    out = empty.step(empty.state(flow(CONFIRM, *TAIL)))
    assert out.escalation is not None and out.escalation.reason_code == "validation_failed"


def test_t_m2_09_write_uncertain_is_not_retried_and_goes_to_verify() -> None:
    w, f = _world(write_script=[Scripted(ToolStatus.uncertain, error="timeout")])
    state = _state(w, f).model_copy(update={"active_flow": {"flow": "f@1.0.0", "node_id": "confirmar"}})
    proposed = w.step(state)
    assert proposed.confirmation is not None
    out = w.step(w.persist(proposed.state), Resume("confirm_answer", "yes", token=proposed.confirmation.token))
    writes = [c for c in w.tools.calls if c.tool.id == "radicar"]
    assert len(writes) == 1  # nunca se reintenta una escritura
    assert out.stop is Stop.terminal and out.state.active_flow.node_id == "fin"  # verify encontró el recurso


def test_write_step_up_stops_on_same_node_and_retry_continues() -> None:
    w, f = _world(level="step_up")
    start = _state(w, f).model_copy(update={"active_flow": {"flow": "f@1.0.0", "node_id": "confirmar"}})
    proposed = w.step(start)
    assert proposed.confirmation is not None
    first = w.step(w.persist(proposed.state), Resume("confirm_answer", "yes", token=proposed.confirmation.token))
    assert first.stop is Stop.awaiting_step_up and first.state.active_flow.node_id == "radicar"  # type: ignore[union-attr]
    assert first.step_up is not None and first.step_up.required_level is AuthLevel.step_up
    assert [a.state for a in first.state.actions] == [ActionState.confirmed]  # M3 la devuelve a `confirmed`
    elevated = first.state.model_copy(update={"principal": principal(auth={"level": "step_up", "at": NOW})})
    second = w.step(w.persist(elevated), Resume("step_up_retry"))
    assert second.stop is Stop.terminal and [a.state for a in second.state.actions] == [ActionState.verified]


def test_write_denied_takes_denied_branch_with_access_denied() -> None:
    w, f = _world(write_script=[Scripted(ToolStatus.denied)])
    start = _state(w, f).model_copy(update={"active_flow": {"flow": "f@1.0.0", "node_id": "confirmar"}})
    proposed = w.step(start)
    assert proposed.confirmation is not None
    out = w.step(w.persist(proposed.state), Resume("confirm_answer", "yes", token=proposed.confirmation.token))
    assert out.escalation is not None and out.escalation.reason_code == "tool_failure"
    assert "access_denied" in _types(out.events)
```
Notas para el ejecutor:
- Agrega a `World` (en `tests/m02/harness.py`) `def tools_ref(self, id: str) -> EntityRef` que devuelva `EntityRef(id=id, version="1.0.0")`; `register_readback(readback_def, of=<ref de la tool de escritura>)`.
- En un `verify`, M3 exige que la acción esté en `executed`, `uncertain` o `executing`; en `uncertain` el `FakeToolExecutor` solo aplica el efecto si `Scripted.effect` es `True` (por defecto sí), así que el readback lo encuentra.
- Los `active_flow` de estos tests apuntan a `confirmar` porque `w.state` arranca en el primer nodo de `flow(...)`; si prefieres, ordena los nodos del flow con `confirmar` primero y quita los `model_copy`.

- [ ] **Step 2: Correr y confirmar que falla**

Run: `uv run pytest tests/m02/test_write.py -q`
Expected: FAIL (sin handlers `confirm`/`tool_write`/`verify`).

- [ ] **Step 3: Implementar**

`agent_core/interpreter/calls.py` (agrega):
```python
from agent_core.actions import ActionContext
from agent_core.domain import Message, RefSpec
from agent_core.interpreter.audit import ViewsAudit
from agent_core.interpreter.jsonlogic import evaluate, truthy
from agent_core.interpreter.refs import make_resolver
from agent_core.interpreter.templates import render_message


def build_action_context(state: RunState, ctx: StepContext) -> ActionContext:
    """Arma el `ActionContext` de M3 con las mismas piezas que usa el resto de M2 (M2 §3.3)."""

    def render(ref: RefSpec, current: RunState) -> Message:
        return render_message(current, ctx, ref)

    def predicate(expr: JsonValue, data: JsonValue) -> bool:
        return truthy(evaluate(expr, data))

    return ActionContext(
        uow_factory=ctx.uow_factory, tools=ctx.tools, call=tool_call_context(state, ctx), render=render,
        predicate=predicate, resolve=make_resolver(ctx), record=ctx.record,
        audit=ViewsAudit(ctx.views, ctx.vault), bound_params=ctx.bound_params,
    )
```
(Importa `JsonValue` desde `agent_core.domain`. Como `templates` importa `context`, no hay ciclo con `calls`.)

`agent_core/interpreter/handlers/write.py`:
```python
"""`confirm`, `tool` de escritura y `verify`: todo delega en `ctx.actions` (M3). M2 nunca escribe directo."""

from agent_core.domain import (
    ConfirmNode,
    EntityKind,
    EntityRef,
    IllegalTransition,
    RunState,
    VerifyNode,
    WriteToolNode,
)
from agent_core.interpreter.calls import build_action_context
from agent_core.interpreter.context import Resume, StepContext, Stop
from agent_core.interpreter.events import Events
from agent_core.interpreter.handlers.base import NodeResult, clear_attempts, escalate_now, request_step_up
from agent_core.interpreter.refs import exact_ref
from agent_core.interpreter.resolve import MissingPath, resolve_args

_ANSWERS = ("yes", "no", "unclear")


def handle_confirm(node: ConfirmNode, state: RunState, ctx: StepContext, resume: Resume) -> NodeResult:
    action_ctx = build_action_context(state, ctx)
    try:
        if resume.kind != "confirm_answer":
            args = resolve_args(state, node.config.action.args)
            tool = exact_ref(ctx, EntityKind.tool, node.config.action.tool)
            state, prompt, events = ctx.actions.propose(state, node, args, ctx.tools.definition(tool),
                                                        action_ctx)
            return NodeResult(state, stop=Stop.awaiting_confirmation, confirmation=prompt, events=events)
        answer = resume.value if resume.value in _ANSWERS else "unclear"
        state, result, events = ctx.actions.answer(state, node, answer, resume.token,  # type: ignore[arg-type]
                                                   action_ctx)
    except MissingPath:
        return escalate_now(state, ctx, "validation_failed")
    return NodeResult(state, result_key=result, events=events)


def _action_tool(state: RunState, node: WriteToolNode) -> EntityRef:
    for action in reversed(state.actions):
        if action.confirm_node_id == node.config.action_from:
            return action.tool
    raise IllegalTransition(f"no hay acción para el confirm {node.config.action_from}")


def handle_write(node: WriteToolNode, state: RunState, ctx: StepContext, resume: Resume) -> NodeResult:
    # Los eventos que devuelve `execute_write` ya los persistió el `EventRecorder` de M3: no se agregan (§6).
    state, result, _persisted = ctx.actions.execute_write(state, node, build_action_context(state, ctx))
    tool = _action_tool(state, node)
    if result == "step_up_required":
        level = ctx.tools.definition(tool).min_auth_level
        return request_step_up(state, ctx, node.id, level, node.config.step_up_max_attempts, [])
    events = [Events(ctx).access_denied(state, tool)] if result == "denied" else []
    if result != "denied":
        state = clear_attempts(state, node.id)
    return NodeResult(state, result_key=result, events=events)


def handle_verify(node: VerifyNode, state: RunState, ctx: StepContext, resume: Resume) -> NodeResult:
    state, result, events = ctx.actions.verify(state, node, build_action_context(state, ctx))
    return NodeResult(state, result_key=result, events=events)
```
`handlers/__init__.py`: agrega `"confirm": handle_confirm`, `"tool_write": handle_write`, `"verify": handle_verify`.

- [ ] **Step 4: Correr y confirmar que pasa**

Run: `uv run pytest tests/m02 tests/m03 -q && uv run mypy && uv run ruff check . --fix && uv run lint-imports`
Expected: PASS. Puntos que suelen fallar: (a) el harness debe persistir el run antes de `execute_write` (`w.persist`); (b) `active_flow` del estado persistido debe coincidir con el flow del registro; (c) si M3 rechaza `answer` con `Resume.value` no válido, ya se normaliza a `unclear`.

- [ ] **Step 5: Commit**

```bash
git add agent_core/interpreter tests/m02
git commit -m "feat(m2): confirm, escritura y verify delegados en M3 con step-up y sin doble registro de eventos

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 12: `respond(generate)` y modo degradado

**Files:**
- Modify: `agent_core/interpreter/handlers/respond.py`
- Create: `tests/m02/test_respond_generate.py`

**Interfaces:**
- Consumes: `ctx.responder.generate(GenerateRequest, state) -> GenerateResult` (Task 1), `derive_claims`, `release_view` (M1), `model_budget_exhausted`, `charge_model`, `render_message`.
- Produces: rama `generate` de `handle_respond`:
  - `ctx.degraded` → renderiza `fallback_template_ref` **sin llamar** al responder (T-M2-10).
  - Si no: `model_budget_exhausted` → `escalate(budget_exceeded)`; si no, `GenerateRequest(node_id, config, claims=derive_claims(flow, release_view(...)).get(node.id, frozenset()))`; carga `model_calls/tokens/cost`; si el resultado trae `escalation` → terminal con esa solicitud (y los `rejected` y `events`); si trae `message`, se entrega. Los `RejectedDraft` y eventos del resultado suben a `NodeResult`.
  - `await: true` funciona igual que con plantilla (D16).

- [ ] **Step 1: Escribir las pruebas que fallan**

`tests/m02/test_respond_generate.py`:
```python
from decimal import Decimal

from agent_core.domain import EscalationRequest, Message, Prompt, RejectedDraft
from agent_core.interpreter import GenerateResult, Stop
from tests.m02.harness import World, fact, flow, template

GEN = {"id": "g", "type": "respond", "next": {"next": "fin"}, "config": {"generate": {
    "prompt_ref": "p/resumen@1", "allowed_facts": ["facts.pqr.value.id"],
    "fallback_template_ref": "t/respaldo@1"}, "claims": []}}
FIN = {"id": "fin", "type": "end", "config": {"outcome": "resolved"}}


def _world() -> World:
    w = World()
    w.add(template("t/respaldo", "Tu disputa {{ facts.pqr.value.id }} quedó radicada."))
    w.add(Prompt.model_validate({"id": "p/resumen", "version": "1.0.0", "locales": {"es": "resume", "pt": "resume"},
                                 "model_profile": "perfil@1"}))
    return w


def _state(w: World):  # type: ignore[no-untyped-def]
    return w.state(flow(GEN, FIN), facts={"pqr": fact({"id": "pqr-1"})})


def test_generate_delivers_message_and_charges_budgets() -> None:
    w = _world()
    generated = Message(kind="generated", text="Radicamos tu disputa pqr-1.", locale="es")
    w.responder.push(GenerateResult(message=generated, model_calls=2, tokens=120, cost_usd=Decimal("0.004"),
                                    rejected=[RejectedDraft(text_model="borrador", reason="cifra")]))
    out = w.step(_state(w))
    assert out.messages == [generated] and out.stop is Stop.terminal
    assert [d.reason for d in out.rejected_drafts] == ["cifra"]
    used = out.state.budgets_used
    assert (used.turn_model_calls, used.run_tokens, used.run_cost) == (2, 120, Decimal("0.004"))
    (request,) = w.responder.calls
    assert request.node_id == "g" and request.config.fallback_template_ref.id == "t/respaldo"


def test_t_m2_10_degraded_mode_uses_fallback_without_calling_the_responder() -> None:
    w = _world()
    out = w.step(_state(w), degraded=True)
    assert [m.text for m in out.messages] == ["Tu disputa pqr-1 quedó radicada."]
    assert out.messages[0].kind == "template" and w.responder.calls == []
    assert out.state.budgets_used.turn_model_calls == 0


def test_responder_escalation_ends_the_run_with_its_request() -> None:
    w = _world()
    request = EscalationRequest(reason_code="validation_failed", target_queue="general", priority="normal")
    w.responder.push(GenerateResult(escalation=request, model_calls=1, tokens=10))
    out = w.step(_state(w))
    assert out.stop is Stop.terminal and out.escalation == request and out.messages == []
    assert out.state.budgets_used.run_tokens == 10


def test_generate_respects_the_model_call_budget() -> None:
    w = _world()
    from agent_core.interpreter import Resume, advance

    state = _state(w).model_copy(update={"budgets_used": {"turn_model_calls": 3, "turn_started_at": w.clock.now()}})
    out = advance(state, w.ctx(), Resume())
    assert out.escalation is not None and out.escalation.reason_code == "budget_exceeded"
    assert w.responder.calls == []


def test_generate_claims_come_from_derive_claims() -> None:
    w = _world()
    w.responder.push(GenerateResult(message=Message(kind="generated", text="ok", locale="es")))
    w.step(_state(w))
    assert w.responder.calls[0].claims == frozenset()  # sin escrituras en este flow, no hay reclamos
```

- [ ] **Step 2: Correr y confirmar que falla**

Run: `uv run pytest tests/m02/test_respond_generate.py -q`
Expected: FAIL (`NotImplementedError: respond(generate)`).

- [ ] **Step 3: Implementar**

`agent_core/interpreter/handlers/respond.py` (reemplaza el archivo):
```python
"""`respond` (M2 §3.3): `template_ref` o `generate` (M8), con modo degradado y `await`."""

from agent_core.domain import Flow, GenerateConfig, IllegalTransition, Message, NodeId, RespondNode, RunState
from agent_core.flows import derive_claims, release_view
from agent_core.interpreter.budgets import charge_model, model_budget_exhausted
from agent_core.interpreter.context import Resume, StepContext, Stop
from agent_core.interpreter.handlers.base import NodeResult, escalate_now
from agent_core.interpreter.ports import GenerateRequest
from agent_core.interpreter.resolve import MissingPath
from agent_core.interpreter.templates import render_message


def _claims(state: RunState, ctx: StepContext, node_id: NodeId) -> frozenset[str]:
    assert state.active_flow is not None
    flow = ctx.registry.get(state.active_flow.flow, Flow)
    return derive_claims(flow, release_view(ctx.registry, ctx.release)).get(node_id, frozenset())


def _generate(node: RespondNode, config: GenerateConfig, state: RunState,
              ctx: StepContext) -> tuple[RunState, Message | None, NodeResult | None]:
    """`(estado, mensaje, resultado terminal)`: exactamente uno de los dos últimos viene lleno."""
    if ctx.degraded:  # sin llamar al modelo (T-M2-10)
        return state, render_message(state, ctx, config.fallback_template_ref), None
    if model_budget_exhausted(state, ctx):
        return state, None, escalate_now(state, ctx, "budget_exceeded")
    request = GenerateRequest(node_id=node.id, config=config, claims=_claims(state, ctx, node.id))
    result = ctx.responder.generate(request, state)
    state = charge_model(state, calls=result.model_calls, tokens=result.tokens, cost=result.cost_usd)
    if result.escalation is not None:
        return state, None, NodeResult(state, stop=Stop.terminal, escalation=result.escalation,
                                       events=list(result.events), rejected=list(result.rejected))
    if result.message is None:
        raise IllegalTransition("el responder no devolvió mensaje ni escalamiento")
    return state, result.message, NodeResult(state, events=list(result.events), rejected=list(result.rejected))


def handle_respond(node: RespondNode, state: RunState, ctx: StepContext, resume: Resume) -> NodeResult:
    cfg = node.config
    carried = NodeResult(state)
    try:
        if cfg.template_ref is not None:
            message = render_message(state, ctx, cfg.template_ref)
        else:
            assert cfg.generate is not None
            state, generated, extra = _generate(node, cfg.generate, state, ctx)
            if extra is not None:
                if generated is None:
                    return extra
                carried = extra
            assert generated is not None
            message = generated
    except MissingPath:
        return escalate_now(state, ctx, "validation_failed")
    stop = Stop.awaiting_user if cfg.await_ else None
    return NodeResult(state, result_key="next", stop=stop, messages=[message], events=carried.events,
                      rejected=carried.rejected)
```
Aclaración del contrato de `_generate`: en el modo degradado devuelve `(state, mensaje, None)`; con presupuesto agotado o escalamiento del responder, `(state, None, NodeResult terminal)`; con mensaje, `(state, mensaje, NodeResult con eventos y borradores)`. `GenerateConfig` sale de `agent_core.domain` (M0).

- [ ] **Step 4: Correr y confirmar que pasa**

Run: `uv run pytest tests/m02 -q && uv run mypy && uv run ruff check . --fix`
Expected: PASS. En `test_generate_respects…`, `turn_started_at` se fija a `w.clock.now()` para que el tiempo de pared no dispare antes que el presupuesto de modelo.

- [ ] **Step 5: Commit**

```bash
git add agent_core/interpreter tests/m02/test_respond_generate.py
git commit -m "feat(m2): respond con generate, modo degradado, reclamos y presupuesto de modelo

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 13: `disputa-cargo` de punta a punta, determinismo, API pública y cierre

**Files:**
- Create: `tests/m02/test_disputa_cargo.py`, `tests/m02/test_public_api.py`
- Modify: `agent_core/interpreter/__init__.py` (exports finales), `docs/specs/motor/m02-interprete.md`, `docs/specs/motor/00-indice.md` (§5)

**Interfaces:**
- Consumes: todo lo anterior; `registry_from_directory` (M1), `tests/m01/fixtures/registry`.
- Produces: T-M2-01 y T-M2-11; API pública verificada; spec y DoD actualizados.

- [ ] **Step 1: Escribir las pruebas que fallan**

`tests/m02/test_disputa_cargo.py`:
```python
"""T-M2-01 (arnés de fase 1: M2 con `Resume` guionados, sin M4) y T-M2-11 (determinismo)."""

from decimal import Decimal
from pathlib import Path

from agent_core.actions import ActionManager
from agent_core.domain import (
    Agent,
    AgentSelector,
    DecisionModelDef,
    EntityKind,
    EntityRef,
    Flow,
    Outcome,
    ToolDef,
)
from agent_core.interpreter import Resume, StepContext, StepOutcome, Stop, advance, begin_turn, start_flow
from agent_core.views import FieldClassifier, TokenVault, ViewService
from testing.builders import principal, run_state
from testing.fakes.clock import FakeClock
from testing.fakes.decision import ScriptedDecision, make_decision
from testing.fakes.ids import FakeIds
from testing.fakes.keys import FakeKeyProvider
from testing.fakes.registry_dir import registry_from_directory
from testing.fakes.responder import ScriptedResponder
from testing.fakes.storage import InMemoryStore
from testing.fakes.tools import FakeToolExecutor
from tests.m02.harness import CATALOG, _Authz

FIXTURE = Path(__file__).parents[1] / "m01" / "fixtures" / "registry"
CANDIDATAS = [
    {"transaction_id": "tx-1", "amount": Decimal("120.50"), "currency": "USD"},
    {"transaction_id": "tx-2", "amount": Decimal("30.00"), "currency": "USD"},
]


class Scenario:
    """Un mundo completo sobre el registro `demo` de M1, con tools y decisión guionadas."""

    def __init__(self, *, amount: Decimal = Decimal("120.50")) -> None:
        self.clock, self.ids, self.store = FakeClock(), FakeIds(), InMemoryStore()
        self.registry = registry_from_directory(FIXTURE, "demo")
        self.release = self.registry.resolve_release(AgentSelector(id="atencion", alias="prod"), principal())
        pins = self.release.entities
        self.agent = self.registry.get(EntityRef(id="atencion", version=pins[EntityKind.agent]["atencion"]), Agent)
        self.flow = self.registry.get(
            EntityRef(id="disputa-cargo", version=pins[EntityKind.flow]["disputa-cargo"]), Flow)
        self.tools = FakeToolExecutor(self.ids)
        for tool_id, version in pins[EntityKind.tool].items():
            definition = self.registry.get(EntityRef(id=tool_id, version=version), ToolDef)
            if tool_id == "obtener_pqr":
                self.tools.register_readback(definition, of=EntityRef(id="radicar_pqr", version=version))
            elif tool_id == "buscar_transacciones":
                self.tools.register(definition, handler=lambda a: CANDIDATAS)
            elif tool_id == "seleccionar":
                self.tools.register(definition, handler=lambda a: next(
                    t for t in a["lista"] if t["transaction_id"] == a["id"]))  # type: ignore[union-attr]
            elif tool_id == "convertir_moneda":
                self.tools.register(definition, handler=lambda a, amount=amount: amount)
            else:
                self.tools.register(definition, handler=lambda a: {"status": "Open", "id": "pqr-1", **a})
        self.decisions = ScriptedDecision([make_decision(
            {"match": "unica", "transaction": "tx-1"}, {"match": True}, tokens=30, cost="0.001")])
        keys = FakeKeyProvider.default()
        self.views = ViewService(keys, _Authz(), self.clock, FieldClassifier(CATALOG))  # type: ignore[arg-type]
        self.vault = TokenVault("run-0001", keys, self.ids)
        self.manager = ActionManager(self.ids, self.clock)
        self.turn = 0

    def ctx(self) -> StepContext:
        self.turn += 1
        return StepContext(
            release=self.release, agent=self.agent, locale="es", clock=self.clock, degraded=False,
            registry=self.registry, tools=self.tools, decisions=self.decisions, actions=self.manager,
            responder=ScriptedResponder(), views=self.views, vault=self.vault, ids=self.ids,
            uow_factory=self.store.uow, turn_id=f"turn-{self.turn:04d}")

    def drive(self, state, resume: Resume) -> StepOutcome:  # type: ignore[no-untyped-def]
        """Un turno como lo hará M4: `begin_turn`, `advance` y persistir el estado al detenerse."""
        out = advance(begin_turn(state, self.clock), self.ctx(), resume)
        return StepOutcome(**{**out.__dict__, "state": self._save(out.state)})

    def _save(self, state):  # type: ignore[no-untyped-def]
        with self.store.uow() as uow:
            saved = uow.save_run(state, state.state_version)
            uow.commit()
        return saved

    def initial_state(self):  # type: ignore[no-untyped-def]
        base = run_state(release=self.release.id, agent=f"atencion@{self.agent.version}")
        return self._save(start_flow(base, self.flow))


def run_happy_path(scenario: Scenario) -> tuple[list[StepOutcome], list[dict[str, object]]]:
    outs: list[StepOutcome] = []
    outs.append(scenario.drive(scenario.initial_state(), Resume()))
    outs.append(scenario.drive(outs[-1].state, Resume("slot_answer", "no reconozco un cargo")))
    assert outs[-1].confirmation is not None
    outs.append(scenario.drive(outs[-1].state, Resume("confirm_answer", "yes", token=outs[-1].confirmation.token)))
    stored = [e.model_dump(mode="json") for e in scenario.store.events["run-0001"]]
    return outs, stored


def test_t_m2_01_disputa_cargo_happy_path_ends_resolved() -> None:
    scenario = Scenario()
    outs, stored = run_happy_path(scenario)
    ask, confirm, done = outs
    assert ask.stop is Stop.awaiting_slot and len(ask.messages) == 1 and ask.messages[0].kind == "template"
    assert confirm.stop is Stop.awaiting_confirmation and confirm.confirmation is not None
    assert done.stop is Stop.terminal and done.end_outcome is Outcome.resolved and done.escalation is None
    assert done.state.active_flow.node_id == "fin"  # type: ignore[union-attr]
    assert set(done.state.facts) >= {"candidatas", "transaccion_elegida", "monto_usd", "pqr", "pqr_verificada"}
    assert done.state.facts["monto_usd"].source.kind == "compute"
    assert done.state.facts["monto_usd"].source.inputs == [done.state.facts["transaccion_elegida"].fact_id]
    assert done.state.slots["descripcion_cargo"].status == "validated"
    assert [a.state.value for a in done.state.actions] == ["verified"]
    assert "pqr-1" in done.messages[-1].text
    assert "action_dispatched" in {e["type"] for e in stored}  # los eventos de M3 quedaron en el almacén
    assert done.state.budgets_used.turn_model_calls == 1 and done.state.budgets_used.run_tokens == 30


def test_high_amount_escalates_by_policy_before_any_write() -> None:
    scenario = Scenario(amount=Decimal("620.00"))
    state = scenario.initial_state()
    ask = scenario.drive(state, Resume())
    out = scenario.drive(ask.state, Resume("slot_answer", "cargo alto"))
    assert out.stop is Stop.terminal and out.escalation is not None
    assert out.escalation.reason_code == "policy:escalamiento-disputa-monto"
    assert out.escalation.target_queue == "disputas" and out.state.actions == []
    assert not [c for c in scenario.tools.calls if c.tool.id == "radicar_pqr"]  # nada se escribió


def test_t_m2_11_two_runs_with_the_same_doubles_produce_identical_events() -> None:
    first_outs, first_stored = run_happy_path(Scenario())
    second_outs, second_stored = run_happy_path(Scenario())
    dump = lambda outs: [[e.model_dump(mode="json") for e in o.events] for o in outs]  # noqa: E731
    assert dump(first_outs) == dump(second_outs)
    assert first_stored == second_stored
    assert [o.state.model_dump(mode="json") for o in first_outs] == [o.state.model_dump(mode="json") for o in second_outs]
```
Nota: si quieres afirmar el texto exacto del primer mensaje, léelo de `tests/m01/fixtures/registry/templates/t/pedir_cargo@1.0.0.yaml`. Verifica también en el fixture el `unclear`/`max_attempts` de `confirmar` y la plantilla `t/pqr_radicado`, cuyo `{{ facts.pqr_verificada.value.id }}` se proyecta como `public` gracias al `CATALOG` del arnés.

`tests/m02/test_public_api.py`:
```python
import subprocess
import sys

import agent_core.interpreter as interpreter


def test_public_surface() -> None:
    assert set(interpreter.__all__) == {
        "NO_RESUME", "CircuitBreaker", "DecisionPort", "DecisionResult", "GenerateRequest", "GenerateResult",
        "ResponderPort", "Resume", "StepContext", "StepOutcome", "Stop", "advance", "begin_turn", "evaluate",
        "start_flow", "truthy",
    }
    for name in interpreter.__all__:
        assert getattr(interpreter, name) is not None


def test_importing_the_interpreter_does_not_import_forbidden_modules() -> None:
    code = ("import sys, agent_core.interpreter; "
            "bad = [m for m in sys.modules if m.split('.')[:2] in "
            "(['agent_core','guards'], ['agent_core','handoff'], ['agent_core','audit'], ['agent_core','turn'], "
            "['agent_core','api'], ['agent_core','registry'], ['agent_core','adapters'])]; "
            "assert not bad, bad")
    subprocess.run([sys.executable, "-c", code], check=True)
```

- [ ] **Step 2: Correr y confirmar que falla**

Run: `uv run pytest tests/m02/test_disputa_cargo.py tests/m02/test_public_api.py -q`
Expected: FAIL solo por lo que falte (exports o ajustes del arnés). Es la primera prueba de integración: cualquier fallo revela un desajuste real entre M2, M3 y el fixture; **corrige M2** (no M1, M3 ni M7) o, si el problema está en su interfaz, detente y pregunta.

- [ ] **Step 3: Completar `__init__.py`**

`agent_core/interpreter/__init__.py` final:
```python
"""M2 — intérprete de nodos (docs/specs/motor/m02-interprete.md). Interfaz pública."""

from agent_core.interpreter.breaker import CircuitBreaker
from agent_core.interpreter.budgets import begin_turn
from agent_core.interpreter.context import NO_RESUME, Resume, StepContext, StepOutcome, Stop
from agent_core.interpreter.jsonlogic import evaluate, truthy
from agent_core.interpreter.loop import advance, start_flow
from agent_core.interpreter.ports import (
    DecisionPort,
    DecisionResult,
    GenerateRequest,
    GenerateResult,
    ResponderPort,
)

__all__ = [
    "NO_RESUME", "CircuitBreaker", "DecisionPort", "DecisionResult", "GenerateRequest", "GenerateResult",
    "ResponderPort", "Resume", "StepContext", "StepOutcome", "Stop", "advance", "begin_turn", "evaluate",
    "start_flow", "truthy",
]
```

- [ ] **Step 4: Correr todo**

Run:
```bash
uv run pytest -q
uv run ruff check .
uv run mypy
uv run lint-imports
uv run agentcore contracts --check
```
Expected: todo en verde (los contratos no cambian: M2 no toca M0).

- [ ] **Step 5: Registrar LOC**

Run:
```bash
find agent_core/interpreter -name "*.py" | xargs wc -l | tail -1
find tests/m02 testing/fakes/decision.py testing/fakes/responder.py -name "*.py" | xargs wc -l | tail -1
```
Anota ambas cifras en el spec (estimación total 1.500–2.000 con pruebas).

- [ ] **Step 6: Actualizar el spec y el índice**

En `docs/specs/motor/m02-interprete.md` (pasa a **rev. 2**):
- §2: reflejar `StepContext` con `ids`, `vault`, `uow_factory`, `record`, `turn_id`, `breaker` (D2); `Resume.token`; `StepOutcome.output` y `rejected_drafts` (D3); `DecisionPort`/`ResponderPort` locales con su nota "M5/M8 los adaptan" (D1); `begin_turn` (D4); `start_flow(state, flow: Flow)` (D5).
- §3: incorporar D6 (plantillas en M2, sin `response_emitted` en fase 1), D7, D8, D9 (`>` en step-up), D10 (parámetros del breaker), D11 (`reads` del evaluador), D12, D13, D14 (validadores; `decide` pendiente), D16.
- §7: agregar pruebas extra (breaker, denied, excepción de tool, `respond(generate)`, alto monto de `disputa-cargo`) con IDs `T-M2-13…`.
- §10: marcar punto por punto la Definición de terminado y las LOC.
- §11 (Abiertos): dejar solo (a) validador `decide` de `collect`; (b) `open_questions`; (c) quién emite `response_emitted` de plantillas; (d) parámetros del breaker por `tool_def`; (e) reinicio de `node_attempts` al terminar un flow.

En `docs/specs/motor/00-indice.md` §5: mover `repair_turns_used` a "M2 (suma en `collect`) / M4 (lee)" (D15).

- [ ] **Step 7: Marcar la Definición de terminado (spec §10)**

- [ ] Handlers MVP completos (`decide`, `rule`, `collect`, `tool` lectura/compute, `tool` escritura, `confirm`, `verify`, `respond`, `escalate`, `end`); `disputa-cargo` corre en el arnés con dobles (T-M2-01).
- [ ] T-M2-01…12 en verde (mapeo: 01 → `test_disputa_cargo`; 02/03/12 → `test_rule`; 04/09 → `test_tool` y `test_write`; 05 → `test_collect`; 06/07 → `test_tool` y `test_write`; 08 → `test_loop` y `test_budgets`/`test_decide`/`test_respond_generate`; 10 → `test_respond_generate`; 11 → `test_disputa_cargo`).
- [ ] Interfaz pública exportada y con tipos; `import-linter`, `mypy` y `ruff` en verde; eventos validados contra el esquema de M0 (`Events` construye los modelos de M0).
- [ ] LOC del paquete registradas; sin TODO sin issue.

- [ ] **Step 8: Commit**

```bash
git add agent_core/interpreter tests/m02 docs/specs/motor/m02-interprete.md docs/specs/motor/00-indice.md
git commit -m "test(m2): disputa-cargo de punta a punta, determinismo y API publica; spec rev. 2

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

## Auto-revisión del plan contra el spec

**Cobertura de M2 §:**
- §2 interfaz → Tasks 1, 4, 6 (`StepContext`, `Resume`, `Stop`, `StepOutcome`, `advance`, `start_flow`, `HANDLERS`).
- §3.1 bucle → Task 6. §3.2 variables → Tasks 3 y 5. §3.3 handlers → `decide` T10, `rule` T7, `collect` T8, `tool` T9, escritura/`confirm`/`verify` T11, `respond` T6+T12, `escalate`/`end` T6.
- §3.4 step-up → `request_step_up` (T6) usado en T9 y T11. §3.5 presupuestos → T4 y T6/T10/T12. §3.6 JSON Logic → T2.
- §4 invariantes: determinismo → T13; sin I/O fuera de `ctx` → `StepContext` y el evaluador puro; `rule` sin `decisions` ni `claimed` → T7; escritura solo por `ctx.actions` → T11 (y la Global Constraint); hechos `full` y salida por `ctx.views` → T5.
- §5 fallas, §6 eventos → T9 (`tool_called`, `access_denied`, `step_up_requested`), T7 (`rule_evaluated`), T6 (`node_entered`); `decision_made` y los de M3 se agregan sin duplicar.
- §7 pruebas: T-M2-01 → T13; 02/03/12 → T7 (y T2); 04 → T9; 05 → T8; 06/07 → T9 y T11; 08 → T6 (nodos, tokens, costo, pared), T10 (modelo por decide) y T12 (modelo por generate); 09 → T9 (lecturas) y T11 (escrituras); 10 → T12; 11 → T13.
- §8 evaluación → los eventos ya llevan `node_entered`/`tool_called` con `latency_ms`, `attempt` y el corte del breaker (`circuit_open`).
- §11 abiertos → `max_attempts` del step-up: ya está en `ToolConfig`/`WriteToolConfig` (`step_up_max_attempts`, 2 por defecto; confirmado en el código de M0); `ids` en `StepContext`: T1; `open_questions`: queda abierto; reclamos en runtime: T12 (`derive_claims`, calculado por llamada; optimizar a "una vez por `flow@v`" queda como mejora si se mide costo).

**Escaneo de placeholders:** el único código no implementado a propósito es el validador `decide` de `collect`, que levanta `NotImplementedError` y se registra como Abierto (D14) con prueba explícita. En T6, la nota de `rule_data` pide dejar una sola función; no queda TODO.

**Consistencia de tipos:** `NodeResult(state, result_key, stop, messages, events, end_outcome, escalation, confirmation, step_up, output, rejected)` se usa igual en T6–T12; `StepOutcome.rejected_drafts` ← `NodeResult.rejected` (T6, `advance`); `request_step_up(state, ctx, node_id, level, max_attempts, events)` es idéntica en T9 y T11; `Projector.model_value(path, *, wrap_slots)` en T5, T10; `exact_ref(ctx, kind, ref)` en T5, T7, T9, T10, T11; `render_message(state, ctx, ref)` en T5, T6, T8, T11, T12; `charge_model(state, *, calls, tokens, cost)` y `model_budget_exhausted(state, ctx)` en T4, T10, T12.

**Riesgos que el ejecutor debe vigilar:**
1. Las pruebas de vista `audit` (`***`, ausencia de valores) dependen del catálogo de `FieldClassifier`; si `mask()` cambiara, ajusta las aserciones a "el valor real no aparece".
2. El arnés de T11/T13 asume que `InMemoryStore.uow()` y `save_run(state, state.state_version)` funcionan con el `state_version` que devuelve cada commit de M3; si el `RunState` que devuelve `execute_write` trae otra versión, usa siempre el estado devuelto y persiste con `w.persist`.
3. Si el fixture `disputa-cargo` o el registro `demo` cambian, T13 es la prueba que lo detecta primero.
