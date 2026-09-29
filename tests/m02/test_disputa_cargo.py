"""T-M2-01 (arnés de fase 1: M2 con `Resume` guionados, sin M4) y T-M2-11 (determinismo)."""

from dataclasses import replace
from decimal import Decimal
from pathlib import Path

from agent_core.actions import ActionManager
from agent_core.domain import (
    Agent,
    AgentSelector,
    EntityKind,
    EntityRef,
    Flow,
    Outcome,
    ToolDef,
)
from agent_core.interpreter import (
    CircuitBreaker,
    Resume,
    StepContext,
    StepOutcome,
    Stop,
    advance,
    begin_turn,
    start_flow,
)
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
from tests.m02.harness import CATALOG, AllowAllAuthz

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
        selector = AgentSelector(id="atencion", alias="prod")
        self.release = self.registry.resolve_release(selector, principal())
        pins = self.release.entities
        agent_ref = EntityRef(id="atencion", version=pins[EntityKind.agent]["atencion"])
        self.agent = self.registry.get(agent_ref, Agent)
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
        self.views = ViewService(keys, AllowAllAuthz(), self.clock, FieldClassifier(CATALOG))  # type: ignore[arg-type]
        self.vault = TokenVault("run-0001", keys, self.ids)
        self.manager = ActionManager(self.ids, self.clock)
        self.breaker = CircuitBreaker()
        self.turn = 0

    def ctx(self) -> StepContext:
        self.turn += 1
        return StepContext(
            release=self.release, agent=self.agent, locale="es", clock=self.clock, degraded=False,
            registry=self.registry, tools=self.tools, decisions=self.decisions, actions=self.manager,
            responder=ScriptedResponder(), views=self.views, vault=self.vault, ids=self.ids,
            uow_factory=self.store.uow, turn_id=f"turn-{self.turn:04d}", breaker=self.breaker)

    def drive(self, state, resume: Resume) -> StepOutcome:  # type: ignore[no-untyped-def]
        """Un turno como lo hará M4: `begin_turn`, `advance` y persistir el estado al detenerse."""
        out = advance(begin_turn(state, self.clock), self.ctx(), resume)
        return replace(out, state=self._save(out.state))

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
    answer = Resume("slot_answer", "no reconozco un cargo")
    outs.append(scenario.drive(outs[-1].state, answer))
    assert outs[-1].confirmation is not None
    yes = Resume("confirm_answer", "yes", token=outs[-1].confirmation.token)
    outs.append(scenario.drive(outs[-1].state, yes))
    stored = [e.model_dump(mode="json") for e in scenario.store.events["run-0001"]]
    return outs, stored


def test_t_m2_01_disputa_cargo_happy_path_ends_resolved() -> None:
    scenario = Scenario()
    outs, stored = run_happy_path(scenario)
    ask, confirm, done = outs
    assert ask.stop is Stop.awaiting_slot and len(ask.messages) == 1 and ask.messages[0].kind == "template"
    assert confirm.stop is Stop.awaiting_confirmation and confirm.confirmation is not None
    assert done.stop is Stop.terminal and done.end_outcome is Outcome.resolved
    assert done.escalation is None
    assert done.state.active_flow.node_id == "fin"  # type: ignore[union-attr]
    assert set(done.state.facts) >= {
        "candidatas", "transaccion_elegida", "monto_usd", "pqr", "pqr_verificada"}
    assert done.state.facts["monto_usd"].source.kind == "compute"
    assert done.state.facts["monto_usd"].source.inputs == [done.state.facts["transaccion_elegida"].fact_id]
    assert done.state.slots["descripcion_cargo"].status == "validated"
    assert [a.state.value for a in done.state.actions] == ["verified"]
    assert "pqr-1" in done.messages[-1].text
    assert "action_dispatched" in {e["type"] for e in stored}  # los eventos de M3 quedaron en el almacén
    # el `decide` corre en el turno 2: el contador por turno se reinicia en `begin_turn`, el del run no
    assert confirm.state.budgets_used.turn_model_calls == 1
    assert done.state.budgets_used.turn_model_calls == 0 and done.state.budgets_used.run_tokens == 30


def test_high_amount_escalates_by_policy_before_any_write() -> None:
    scenario = Scenario(amount=Decimal("620.00"))
    state = scenario.initial_state()
    ask = scenario.drive(state, Resume())
    out = scenario.drive(ask.state, Resume("slot_answer", "cargo alto"))
    assert out.stop is Stop.terminal and out.escalation is not None
    assert out.escalation.reason_code == "policy:escalamiento-disputa-monto"
    assert out.escalation.target_queue == "disputas" and out.state.actions == []
    assert not [c for c in scenario.tools.calls if c.tool.id == "radicar_pqr"]  # nada se escribió


def _dump(outs: list[StepOutcome]) -> list[list[dict[str, object]]]:
    return [[e.model_dump(mode="json") for e in o.events] for o in outs]


def test_t_m2_11_two_runs_with_the_same_doubles_produce_identical_events() -> None:
    first_outs, first_stored = run_happy_path(Scenario())
    second_outs, second_stored = run_happy_path(Scenario())
    assert _dump(first_outs) == _dump(second_outs)
    assert first_stored == second_stored
    assert [o.state.model_dump(mode="json") for o in first_outs] == [
        o.state.model_dump(mode="json") for o in second_outs]
