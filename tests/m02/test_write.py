from agent_core.domain import ActionState, AuthLevel, EngineEvent, Flow, RunState
from agent_core.interpreter import Resume, StepOutcome, Stop
from agent_core.ports import ToolStatus
from testing.builders import NOW, principal
from testing.fakes.tools import Scripted
from tests.m02.harness import World, fact, flow, slot, template, tool_def

CONFIRM = {"id": "confirmar", "type": "confirm", "config": {
    "action": {"tool": "radicar@1.0.0", "args": {"transaction_id": "facts.tx.value.transaction_id",
                                                  "descripcion": "slots.d"}},
    "summary_template": "t/resumen@1.0.0", "max_attempts": 2},
    "next": {"yes": "radicar", "no": "cancelado", "unclear": "confirmar", "max_attempts": "cancelado"}}
WRITE = {"id": "radicar", "type": "tool", "config": {"action_from": "confirmar", "save_as": "pqr"},
         "next": {"ok": "verificar", "uncertain": "verificar", "denied": "esc"}}
VERIFY = {"id": "verificar", "type": "verify", "config": {
    "readback": "obtener@1.0.0", "by": "idempotency_key",
    "predicate": {"==": [{"var": "readback.status"}, "Open"]}, "save_as": "pqr_ok"},
    "next": {"verified": "fin", "failed": "esc"}}
TAIL = [
    {"id": "fin", "type": "end", "config": {"outcome": "resolved"}},
    {"id": "cancelado", "type": "end", "config": {"outcome": "cancelled"}},
    {"id": "esc", "type": "escalate", "config": {"reason_code": "tool_failure"}},
]


def _world(*, write_script: tuple[Scripted, ...] = (), level: str = "session") -> tuple[World, Flow]:
    w = World()
    w.add(template("t/resumen", "¿Radico la disputa?"))
    write = tool_def("radicar", "write_reversible", level=level)
    w.add_tool(write, script=write_script, handler=lambda a: {"status": "Open", "id": "pqr-1", **a})
    readback = tool_def("obtener")
    w.add(readback)
    w.tools.register_readback(readback, of=w.tools_ref("radicar"))
    return w, flow(CONFIRM, WRITE, VERIFY, *TAIL)


def _start(w: World, f: Flow) -> RunState:
    """Estado persistido en `confirmar` (primer nodo del flow) con sus slots y hechos."""
    return w.persist(w.state(f, slots={"d": slot("cargo")}, facts={"tx": fact({"transaction_id": "tx-1"})}))


def _yes(w: World, proposed: StepOutcome) -> StepOutcome:
    assert proposed.confirmation is not None
    answer = Resume("confirm_answer", "yes", token=proposed.confirmation.token)
    return w.step(w.persist(proposed.state), answer)


def _types(events: list[EngineEvent]) -> list[str]:
    return [e.type for e in events]


def test_confirm_proposes_then_yes_by_button_executes_and_verifies() -> None:
    w, f = _world()
    proposed = w.step(_start(w, f))
    assert proposed.stop is Stop.awaiting_confirmation and proposed.confirmation is not None
    assert proposed.confirmation.summary.text == "¿Radico la disputa?"
    assert proposed.state.active_flow is not None and proposed.state.active_flow.node_id == "confirmar"
    done = _yes(w, proposed)
    assert done.stop is Stop.terminal and done.end_outcome is not None
    assert [a.state for a in done.state.actions] == [ActionState.verified]
    assert set(done.state.facts) == {"tx", "pqr", "pqr_ok"}
    # Los eventos del commit de M3 ya están en el almacén y NO se repiten en `done.events`.
    persisted = _types(w.store.events["run-0001"])
    assert "action_dispatched" in persisted and "action_dispatched" not in _types(done.events)
    assert "action_confirmed" in _types(done.events) and "action_verified" in _types(done.events)


def test_confirm_no_cancels_and_unclear_re_asks_with_a_rotated_token() -> None:
    w, f = _world()
    first = w.step(_start(w, f))
    assert first.confirmation is not None
    unclear = w.step(w.persist(first.state), Resume("confirm_answer", "unclear"))
    assert unclear.stop is Stop.awaiting_confirmation and unclear.confirmation is not None
    assert unclear.confirmation.token != first.confirmation.token  # M3 rota el token en la reentrada
    no = w.step(w.persist(unclear.state), Resume("confirm_answer", "no"))
    assert no.stop is Stop.terminal and no.state.active_flow is not None
    assert no.state.active_flow.node_id == "cancelado"
    assert [a.state for a in no.state.actions] == [ActionState.cancelled]


def test_confirm_max_attempts_goes_to_its_branch() -> None:
    w, f = _world()
    state = w.step(_start(w, f)).state
    out = None
    for _ in range(2):
        out = w.step(w.persist(state), Resume("confirm_answer", "unclear"))
        state = out.state
    assert out is not None and out.stop is Stop.terminal
    assert state.active_flow is not None and state.active_flow.node_id == "cancelado"


def test_confirm_with_missing_args_escalates_validation_failed() -> None:
    empty = World()
    empty.add(template("t/resumen", "x"), tool_def("radicar", "write_reversible"))
    out = empty.step(empty.state(flow(CONFIRM, *TAIL)))
    assert out.escalation is not None and out.escalation.reason_code == "validation_failed"


def test_confirm_invalid_answer_counts_as_unclear() -> None:
    w, f = _world()
    first = w.step(_start(w, f))
    out = w.step(w.persist(first.state), Resume("confirm_answer", "quizas"))
    assert out.stop is Stop.awaiting_confirmation and out.confirmation is not None


def test_t_m2_09_write_uncertain_is_not_retried_and_goes_to_verify() -> None:
    lost = Scripted(ToolStatus.uncertain, result={"status": "Open", "id": "pqr-1"}, error="timeout")
    w, f = _world(write_script=(lost,))
    proposed = w.step(_start(w, f))
    assert proposed.confirmation is not None
    out = _yes(w, proposed)
    writes = [c for c in w.tools.calls if c.tool.id == "radicar"]
    assert len(writes) == 1  # nunca se reintenta una escritura
    assert out.stop is Stop.terminal and out.state.active_flow is not None
    assert out.state.active_flow.node_id == "fin"  # verify encontró el recurso


def test_write_step_up_stops_on_same_node_and_retry_continues() -> None:
    w, f = _world(level="step_up")
    proposed = w.step(_start(w, f))
    assert proposed.confirmation is not None
    first = _yes(w, proposed)
    assert first.stop is Stop.awaiting_step_up and first.state.active_flow is not None
    assert first.state.active_flow.node_id == "radicar"
    assert first.step_up is not None and first.step_up.required_level is AuthLevel.step_up
    assert [a.state for a in first.state.actions] == [ActionState.confirmed]  # M3 la devuelve a `confirmed`
    elevated = first.state.model_copy(update={"principal": principal(auth={"level": "step_up", "at": NOW})})
    second = w.step(w.persist(elevated), Resume("step_up_retry"))
    assert second.stop is Stop.terminal and [a.state for a in second.state.actions] == [ActionState.verified]


def test_write_denied_takes_denied_branch_with_access_denied() -> None:
    w, f = _world(write_script=(Scripted(ToolStatus.denied),))
    proposed = w.step(_start(w, f))
    assert proposed.confirmation is not None
    out = _yes(w, proposed)
    assert out.escalation is not None and out.escalation.reason_code == "tool_failure"
    assert "access_denied" in _types(out.events)
