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
    state = w.state(flow(END), budgets_used={
        "run_tokens": 5, "turn_nodes": 9, "turn_model_calls": 2, "turn_understand_calls": 2})
    fresh = begin_turn(state, w.clock)
    used = fresh.budgets_used
    assert (used.turn_nodes, used.turn_model_calls, used.turn_understand_calls, used.run_tokens) == (
        0, 0, 0, 5)
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
