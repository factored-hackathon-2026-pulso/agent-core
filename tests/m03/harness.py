"""Arnés de M3 (M3 §7): tools, nodos y contexto sintéticos, y caídas inyectables por punto.

Solo datos sintéticos. Puntos de caída del spec:
- `after_commit_1`, `on_commit_2` (y `on_commit_1` para T-M3-04): `CrashingFactory`, que envuelve cualquier
  `UnitOfWorkFactory` (también la de Postgres cuando exista, M4);
- `after_call`: `CrashAfterCall`, la tool aplica el efecto y el proceso muere antes del commit 2.
"""

from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, Literal

from agent_core.actions import ActionContext, ActionManager
from agent_core.domain import (
    ConfirmationPrompt,
    ConfirmNode,
    EngineEvent,
    EntityRef,
    JsonValue,
    Message,
    RefSpec,
    RunState,
    ToolDef,
    VerifyNode,
    WriteToolNode,
)
from agent_core.ports import ToolCallContext, ToolExecutor, ToolResult, UnitOfWork, UnitOfWorkFactory
from testing.builders import principal, run_state
from testing.fakes.clock import FakeClock
from testing.fakes.ids import FakeIds
from testing.fakes.storage import InMemoryStore, SimulatedCrash
from testing.fakes.tools import FakeToolExecutor, Handler, RecordedCall, Scripted

RUN_ID = "run-0001"
RELEASE = "rel-2026-09-28"
TURN_ID = "turn-0001"
FLOW_REF = EntityRef.parse("disputa-cargo@1.0.0")
WRITE = ToolDef.model_validate({
    "id": "radicar_pqr", "version": "1.0.0", "risk_class": "write_reversible", "min_auth_level": "session",
    "idempotent": True, "readback_by": "idempotency_key", "source": "pqr",
})
READBACK = ToolDef.model_validate({
    "id": "obtener_pqr", "version": "1.0.0", "risk_class": "read", "min_auth_level": "session",
    "idempotent": True, "source": "pqr",
})
WRITE_REF = EntityRef(id=WRITE.id, version=WRITE.version)
READBACK_REF = EntityRef(id=READBACK.id, version=READBACK.version)
ARGS: dict[str, JsonValue] = {"transaction_id": "tx-demo-1", "descripcion": "cargo desconocido"}
RESOURCE: dict[str, JsonValue] = {"pqr_id": "pqr-demo-1", "status": "Open"}

_CONFIRM: dict[str, Any] = {
    "id": "confirmar",
    "type": "confirm",
    "config": {
        "action": {"tool": "radicar_pqr@1.0.0",
                   "args": {"transaction_id": "facts.transaccion_elegida.value.transaction_id"}},
        "summary_template": "t/resumen_pqr@1.0.0",
        "reprompt_template": "t/reprompt_pqr@1.0.0",
    },
    "next": {"yes": "radicar", "no": "fin_cancelado", "unclear": "confirmar",
             "max_attempts": "no_confirmado"},
}
CONFIRM = ConfirmNode.model_validate(_CONFIRM)
CONFIRM_SIN_REPROMPT = ConfirmNode.model_validate(
    {**_CONFIRM, "config": {k: v for k, v in _CONFIRM["config"].items() if k != "reprompt_template"}}
)
WRITE_NODE = WriteToolNode.model_validate({
    "id": "radicar", "type": "tool", "config": {"action_from": "confirmar", "save_as": "pqr"},
    "next": {"ok": "verificar", "uncertain": "verificar", "denied": "esc_tool"},
})
VERIFY_NODE = VerifyNode.model_validate({
    "id": "verificar", "type": "verify",
    "config": {"readback": "obtener_pqr@1.0.0", "by": "idempotency_key",
               "predicate": {"==": [{"var": "readback.status"}, "Open"]}, "save_as": "pqr_verificada"},
    "next": {"verified": "fin", "failed": "esc_verif"},
})


def eq_var(expr: JsonValue, data: JsonValue) -> bool:
    """JSON Logic mínimo de prueba: solo `{"==": [{"var": "a.b"}, literal]}`. El evaluador real es de M2."""
    assert isinstance(expr, dict)
    left, right = expr["=="]  # type: ignore[misc]
    value: Any = data
    for part in left["var"].split("."):  # type: ignore[index]
        value = value.get(part) if isinstance(value, dict) else None
    return bool(value == right)


# --- caídas ---------------------------------------------------------------------------------------------

CrashPoint = Literal["on_commit_1", "after_commit_1", "on_commit_2"]
_CRASH_PLAN: dict[str, tuple[int, str]] = {
    "on_commit_1": (1, "on_commit"),
    "after_commit_1": (1, "after_commit"),
    "on_commit_2": (2, "on_commit"),
}


class CrashingUoW:
    """Delega en la UoW real; en el punto pedido simula la muerte del proceso alrededor de `commit`."""

    def __init__(self, inner: UnitOfWork, point: str | None) -> None:
        self._inner = inner
        self._point = point

    def __enter__(self) -> "CrashingUoW":
        self._inner.__enter__()
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self._inner.__exit__(*exc_info)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def commit(self) -> None:
        if self._point == "on_commit":
            raise SimulatedCrash("caída antes de aplicar el commit")
        self._inner.commit()
        if self._point == "after_commit":
            raise SimulatedCrash("caída después de aplicar el commit")


class CrashingFactory:
    """Cuenta las UoW que abre M3 (commit 1 = primera, commit 2 = segunda) y hace caer la pedida."""

    def __init__(self, base: UnitOfWorkFactory, crash: CrashPoint | None = None) -> None:
        self._base = base
        self._crash = crash
        self.opened = 0

    def __call__(self) -> CrashingUoW:
        self.opened += 1
        at, point = _CRASH_PLAN[self._crash] if self._crash else (0, None)
        return CrashingUoW(self._base(), point if self.opened == at else None)


class ProcessDied(BaseException):
    """Muerte del proceso: no es `Exception`, así que M3 no puede tragársela como `uncertain`."""


class _Wrapper:
    def __init__(self, inner: ToolExecutor) -> None:
        self.inner = inner

    def definition(self, tool: EntityRef) -> ToolDef:
        return self.inner.definition(tool)

    def execute(self, tool: EntityRef, args: dict[str, JsonValue], bound_params: dict[str, str],
                ctx: ToolCallContext, idempotency_key: str | None = None) -> ToolResult:
        return self.inner.execute(tool, args, bound_params, ctx, idempotency_key)


class CrashAfterCall(_Wrapper):
    """Punto `after_call`: la escritura se aplica y el proceso muere antes del commit 2."""

    def execute(self, tool: EntityRef, args: dict[str, JsonValue], bound_params: dict[str, str],
                ctx: ToolCallContext, idempotency_key: str | None = None) -> ToolResult:
        result = super().execute(tool, args, bound_params, ctx, idempotency_key)
        if self.definition(tool).is_write:
            raise ProcessDied()
        return result


class RaisingTools(_Wrapper):
    """Lanza `exc` al llamar una escritura (`on="write"`) o una lectura (`on="read"`)."""

    def __init__(self, inner: ToolExecutor, exc: Exception, on: Literal["write", "read"] = "write") -> None:
        super().__init__(inner)
        self.exc = exc
        self.on = on

    def execute(self, tool: EntityRef, args: dict[str, JsonValue], bound_params: dict[str, str],
                ctx: ToolCallContext, idempotency_key: str | None = None) -> ToolResult:
        if self.definition(tool).is_write == (self.on == "write"):
            raise self.exc
        return super().execute(tool, args, bound_params, ctx, idempotency_key)


class SlowTools(_Wrapper):
    """Cada llamada avanza el reloj `delta` (para medir `latency_ms`)."""

    def __init__(self, inner: ToolExecutor, clock: FakeClock, delta: timedelta) -> None:
        super().__init__(inner)
        self.clock = clock
        self.delta = delta

    def execute(self, tool: EntityRef, args: dict[str, JsonValue], bound_params: dict[str, str],
                ctx: ToolCallContext, idempotency_key: str | None = None) -> ToolResult:
        self.clock.advance(self.delta)
        return super().execute(tool, args, bound_params, ctx, idempotency_key)


class ObservingTools(_Wrapper):
    """En cada escritura anota qué había *commiteado* en el almacén justo antes de llamarla."""

    def __init__(self, inner: ToolExecutor, store: InMemoryStore) -> None:
        super().__init__(inner)
        self.store = store
        self.seen: list[tuple[list[str], list[str]]] = []

    def execute(self, tool: EntityRef, args: dict[str, JsonValue], bound_params: dict[str, str],
                ctx: ToolCallContext, idempotency_key: str | None = None) -> ToolResult:
        if self.definition(tool).is_write:
            with self.store.lock:
                committed = self.store.runs[ctx.run_id]
                events = self.store.events.get(ctx.run_id, [])
                self.seen.append(([a.state.value for a in committed.actions],
                                  [e.type for e in events]))  # type: ignore[attr-defined]
        return super().execute(tool, args, bound_params, ctx, idempotency_key)


# --- mundo ----------------------------------------------------------------------------------------------


def base_state(**over: Any) -> RunState:
    return run_state(active_flow={"flow": str(FLOW_REF), "node_id": "confirmar"}, **over)


@dataclass
class World:
    store: InMemoryStore = field(default_factory=InMemoryStore)
    uow_factory: UnitOfWorkFactory | None = None  # otro almacén (p. ej. Postgres); por defecto, `store.uow`
    record: Any = None  # `EventRecorder` por defecto de `ctx()` (Postgres exige eventos encadenados, M11)
    clock: FakeClock = field(default_factory=FakeClock)
    ids: FakeIds = field(default_factory=FakeIds)
    rendered: list[RefSpec] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.tools = FakeToolExecutor(self.ids)
        self.write_behaviour()
        self.tools.register_readback(READBACK, of=WRITE_REF)
        self.manager = ActionManager(self.ids, self.clock)

    @property
    def uow(self) -> UnitOfWorkFactory:
        return self.uow_factory or self.store.uow

    def write_behaviour(self, *, script: tuple[Scripted, ...] = (), handler: Handler | None = None) -> None:
        self.tools.register(WRITE, script=script, handler=handler or (lambda args: {**RESOURCE, **args}))

    def render(self, ref: RefSpec, state: RunState) -> Message:
        self.rendered.append(ref)
        return Message(kind="template", text=f"resumen:{ref.id}", locale=state.locale)

    def ctx(self, *, crash: CrashPoint | None = None, tools: ToolExecutor | None = None,
            predicate: Any = eq_var, run_id: str = RUN_ID, **hooks: Any) -> ActionContext:
        call = ToolCallContext(run_id=run_id, release=RELEASE, principal=principal(),
                               subject=base_state().subject, turn_id=TURN_ID)
        if self.record is not None:
            hooks.setdefault("record", self.record)
        return ActionContext(uow_factory=CrashingFactory(self.uow, crash), tools=tools or self.tools,
                             call=call, render=self.render, predicate=predicate, **hooks)


def persist(w: World, state: RunState) -> RunState:
    """Lo que hace M4 al cerrar un turno: guarda el estado en su propia transacción."""
    with w.uow() as uow:
        saved = uow.save_run(state, state.state_version)
        uow.commit()
    return saved


def reload(w: World) -> RunState:
    with w.uow() as uow:
        loaded = uow.load_run(RUN_ID)
    assert loaded is not None
    return loaded


def persisted_proposal(w: World, node: ConfirmNode = CONFIRM) -> tuple[RunState, ConfirmationPrompt]:
    """Turno 1 persistido: run creado y acción `proposed` esperando confirmación."""
    state = persist(w, base_state())
    state, prompt, _ = w.manager.propose(state, node, ARGS, WRITE, w.ctx())
    return persist(w, state), prompt


def confirmed_state(w: World) -> RunState:
    """Turno 2 en memoria: `yes` por botón sobre la propuesta persistida."""
    state, prompt = persisted_proposal(w)
    state, result, _ = w.manager.answer(state, CONFIRM, "yes", prompt.token, w.ctx())
    assert result == "yes"
    return state


def writes(w: World) -> list[RecordedCall]:
    return [c for c in w.tools.calls if c.tool == WRITE_REF]


def readbacks(w: World) -> list[RecordedCall]:
    return [c for c in w.tools.calls if c.tool == READBACK_REF]


def event_types(events: list[EngineEvent]) -> list[str]:
    return [e.type for e in events]  # type: ignore[attr-defined]
