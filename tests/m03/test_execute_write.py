"""`execute_write` (M3 §3.4): commit 1 → tool → commit 2, args congelados y mapeo de resultados."""

from datetime import timedelta

import pytest

from agent_core.domain import ActionState, IllegalTransition, ToolStatus
from testing.fakes.tools import Scripted
from tests.m03.harness import (
    ARGS,
    RESOURCE,
    WRITE_NODE,
    ObservingTools,
    RaisingTools,
    SlowTools,
    World,
    confirmed_state,
    event_types,
    persisted_proposal,
    reload,
    writes,
)


def test_ok_executes_saves_fact_and_commits_twice() -> None:
    w = World()
    state = confirmed_state(w)
    before = state.state_version
    state, result, events = w.manager.execute_write(state, WRITE_NODE, w.ctx())
    [action] = state.actions
    assert (result, action.state) == ("ok", ActionState.executed)
    assert state.state_version == before + 2
    fact = state.facts["pqr"]
    assert fact.value == {**RESOURCE, **ARGS}
    assert (fact.source.kind, fact.source.ref) == ("tool", action.action_id)
    assert event_types(events) == ["action_dispatched", "tool_called"]
    dispatched, called = events
    assert (dispatched.payload.action_id, dispatched.payload.args_hash) == (
        action.action_id, action.args_hash)
    assert (called.payload.status, called.payload.action_id, called.payload.node_id) == (
        ToolStatus.ok, action.action_id, "radicar")
    committed = reload(w)
    assert committed.actions[0].state is ActionState.executed
    assert committed.facts["pqr"] == fact
    assert event_types(w.store.events[committed.run_id]) == ["action_dispatched", "tool_called"]
    assert [(c.idempotency_key, c.args) for c in writes(w)] == [(action.action_id, ARGS)]


def test_write_is_never_called_before_commit_1() -> None:
    w = World()
    state = confirmed_state(w)
    observer = ObservingTools(w.tools, w.store)
    w.manager.execute_write(state, WRITE_NODE, w.ctx(tools=observer))
    assert observer.seen == [(["executing"], ["action_dispatched"])]


def test_t_m3_09_executed_args_are_the_frozen_ones() -> None:
    w = World()
    state = confirmed_state(w)
    state = state.model_copy(update={"slots": {"descripcion_cargo": {
        "value": "otro texto", "status": "validated", "source_turn": 2}}})
    w.manager.execute_write(state, WRITE_NODE, w.ctx())
    assert [c.args for c in writes(w)] == [ARGS]


@pytest.mark.parametrize(("scripted", "error"), [
    (Scripted(ToolStatus.uncertain, result=RESOURCE, error="HTTP 503", effect=True), "HTTP 503"),
    (Scripted(ToolStatus.uncertain, error="timeout", effect=False), "timeout"),
])
def test_t_m3_02_backend_failure_is_uncertain(scripted: Scripted, error: str) -> None:
    w = World()
    w.write_behaviour(script=(scripted,))
    state, result, events = w.manager.execute_write(confirmed_state(w), WRITE_NODE, w.ctx())
    assert (result, state.actions[0].state) == ("uncertain", ActionState.uncertain)
    assert "pqr" not in state.facts
    assert (events[1].payload.status, events[1].payload.error) == (ToolStatus.uncertain, error)


def test_t_m3_02_exception_is_uncertain_without_its_message() -> None:
    w = World()
    tools = RaisingTools(w.tools, ConnectionResetError("tx-demo-1 cargo desconocido"))
    state, result, events = w.manager.execute_write(confirmed_state(w), WRITE_NODE, w.ctx(tools=tools))
    assert (result, state.actions[0].state) == ("uncertain", ActionState.uncertain)
    payload = events[1].payload
    assert (payload.status, payload.error) == (ToolStatus.uncertain, "ConnectionResetError")
    assert reload(w).actions[0].state is ActionState.uncertain


def test_t_m3_11_denied_is_terminal_and_never_retried() -> None:
    w = World()
    w.write_behaviour(script=(Scripted(ToolStatus.denied),))
    state, result, _ = w.manager.execute_write(confirmed_state(w), WRITE_NODE, w.ctx())
    assert (result, state.actions[0].state) == ("denied", ActionState.denied)
    assert "pqr" not in state.facts
    with pytest.raises(IllegalTransition):
        w.manager.execute_write(state, WRITE_NODE, w.ctx())
    assert len(writes(w)) == 1


def test_step_up_returns_action_to_confirmed_and_retry_reuses_key() -> None:
    w = World()
    w.write_behaviour(script=(Scripted(ToolStatus.step_up_required),))
    state, result, events = w.manager.execute_write(confirmed_state(w), WRITE_NODE, w.ctx())
    assert (result, state.actions[0].state) == ("step_up_required", ActionState.confirmed)
    assert "pqr" not in state.facts
    assert events[1].payload.status is ToolStatus.step_up_required
    assert reload(w).actions[0].state is ActionState.confirmed
    state, result, _ = w.manager.execute_write(state, WRITE_NODE, w.ctx())
    assert result == "ok"
    key = state.actions[0].action_id
    assert [c.idempotency_key for c in writes(w)] == [key, key]


def test_execute_without_confirmed_action_is_a_bug() -> None:
    w = World()
    state, _ = persisted_proposal(w)
    with pytest.raises(IllegalTransition):
        w.manager.execute_write(state, WRITE_NODE, w.ctx())
    assert writes(w) == []


def test_commits_go_through_the_event_recorder() -> None:
    w = World()
    seen: list[tuple[int, list[str]]] = []

    def record(uow, state, events):  # type: ignore[no-untyped-def]
        seen.append((state.state_version, event_types(events)))
        uow.append_events(state.run_id, events)

    state = confirmed_state(w)
    v = state.state_version
    w.manager.execute_write(state, WRITE_NODE, w.ctx(record=record))
    assert seen == [(v + 1, ["action_dispatched"]), (v + 2, ["tool_called"])]


def test_latency_is_measured_with_the_monotonic_clock() -> None:
    w = World()
    tools = SlowTools(w.tools, w.clock, timedelta(milliseconds=250))
    _, _, events = w.manager.execute_write(confirmed_state(w), WRITE_NODE, w.ctx(tools=tools))
    assert events[1].payload.latency_ms == 250


def test_default_audit_projection_redacts_args_and_result() -> None:
    w = World()
    _, _, events = w.manager.execute_write(confirmed_state(w), WRITE_NODE, w.ctx())
    payload = events[1].payload
    assert payload.args == {"descripcion": "***", "transaction_id": "***"}
    assert (payload.result, payload.result_fp) == (None, None)
    assert "tx-demo-1" not in events[1].model_dump_json()
