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
from agent_core.interpreter import CircuitBreaker, Resume, StepContext, StepOutcome, advance, begin_turn
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


class AllowAllAuthz:
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
        self.views = ViewService(keys, AllowAllAuthz(), self.clock, FieldClassifier(CATALOG))  # type: ignore[arg-type]
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

    def tools_ref(self, id: str) -> EntityRef:
        return EntityRef(id=id, version="1.0.0")

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
        return advance(begin_turn(state, self.clock), self.ctx(**ctx_over), resume or Resume())

    def persist(self, state: RunState) -> RunState:
        """Lo que hace M4 al cerrar un turno: guarda el estado en su propia transacción."""
        with self.store.uow() as uow:
            saved = uow.save_run(state, state.state_version)
            uow.commit()
        return saved
