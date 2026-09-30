from datetime import timedelta
from decimal import Decimal

import pytest

from agent_core.domain import Budgets, IllegalTransition, Outcome
from agent_core.interpreter import Resume, Stop, advance, begin_turn, start_flow
from tests.m02.harness import AGENT, World, fact, flow, template

FIN = {"id": "fin", "type": "end", "config": {"outcome": "resolved"}}


def _respond(id: str, ref: str, nxt: str, **cfg: object) -> dict[str, object]:
    return {"id": id, "type": "respond", "config": {"template_ref": ref, **cfg}, "next": {"next": nxt}}


def test_respond_template_then_end() -> None:
    w = World()
    w.add(template("t/hola", "Total {{ facts.saldo.value.amount }}"))
    f = flow(_respond("r", "t/hola@1.0.0", "fin"), FIN)
    out = w.step(w.state(f, facts={"saldo": fact({"amount": Decimal("10.50")})}))
    assert (out.stop, out.end_outcome, out.escalation) == (Stop.terminal, Outcome.resolved, None)
    assert [m.text for m in out.messages] == ["Total 10.50"]
    assert [e.type for e in out.events] == ["node_entered", "response_emitted", "node_entered"]  # type: ignore[attr-defined]
    assert out.state.active_flow is not None and out.state.active_flow.node_id == "fin"


def test_respond_await_moves_pointer_before_stopping() -> None:
    w = World()
    w.add(template("t/aclarar", "¿Cuál cargo?"))
    f = flow(_respond("a", "t/aclarar@1.0.0", "fin", **{"await": True}), FIN)
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


def test_priority_expr_non_string_result_falls_back_to_normal() -> None:
    w = World()
    esc = {"id": "e", "type": "escalate", "config": {
        "reason_code": "policy:monto", "priority_expr": {"if": [True, 5, "low"]}}}
    out = w.step(w.state(flow(esc)))
    assert out.escalation is not None and out.escalation.priority == "normal"


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
    cw = World()
    conv_end = {**end, "config": {"outcome": "resolved", "output_map": {"pqr": "facts.p.value.id"}}}
    conversational = cw.step(cw.state(flow(conv_end)))
    assert conversational.output is None


def test_missing_template_variable_escalates_validation_failed() -> None:
    w = World()
    w.add(template("t/x", "{{ facts.nope.value.id }}"))
    out = w.step(w.state(flow(_respond("r", "t/x@1.0.0", "fin"), FIN)))
    assert out.escalation is not None and out.escalation.reason_code == "validation_failed"
    assert out.stop is Stop.terminal


def test_start_flow_points_to_first_node_and_resets_attempts() -> None:
    w = World()
    f = flow(_respond("r", "t/hola@1.0.0", "fin"), FIN)
    state = w.state(f).model_copy(update={"active_flow": None, "node_attempts": {"x": 2}})
    started = start_flow(state, f)
    assert started.active_flow is not None
    assert (str(started.active_flow.flow), started.active_flow.node_id) == ("f@1.0.0", "r")
    assert started.node_attempts == {}


def test_transition_without_next_is_a_bug() -> None:
    w = World()
    w.add(template("t/hola", "hola"))
    f = flow({"id": "r", "type": "respond", "config": {"template_ref": "t/hola@1.0.0"}, "next": {}})
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
    return flow(_respond("a", "t/hola@1.0.0", "b"), _respond("b", "t/hola@1.0.0", "fin"), FIN)


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
    state = begin_turn(w.state(_chain()), w.clock)  # type: ignore[arg-type]
    w.clock.advance(timedelta(seconds=2))
    out = advance(state, w.ctx(), Resume())
    assert out.escalation is not None and out.escalation.reason_code == "budget_exceeded"
    assert out.messages == [] and [e.type for e in out.events] == []  # type: ignore[attr-defined]
