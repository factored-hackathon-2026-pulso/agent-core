"""`verify` (M3 §3.5) y camino feliz completo (T-M3-01)."""

import pytest

from agent_core.domain import ActionState, IllegalTransition, ToolStatus
from testing.fakes.tools import Scripted
from tests.m03.harness import (
    ARGS,
    CONFIRM,
    RESOURCE,
    VERIFY_NODE,
    WRITE_NODE,
    ObservingTools,
    RaisingTools,
    StatusTools,
    World,
    confirmed_state,
    event_types,
    persisted_proposal,
    readbacks,
    writes,
)
from tests.m03.scenarios import crash_then_recover


def _written(w: World):  # type: ignore[no-untyped-def]
    state, _, _ = w.manager.execute_write(confirmed_state(w), WRITE_NODE, w.ctx())
    return state


def test_t_m3_01_happy_path() -> None:
    w = World()
    state, prompt = persisted_proposal(w)
    seen = [state.actions[0].state]
    state, answered, ev1 = w.manager.answer(state, CONFIRM, "yes", prompt.token, w.ctx())
    seen.append(state.actions[0].state)
    observer = ObservingTools(w.tools, w.store)
    state, written, ev2 = w.manager.execute_write(state, WRITE_NODE, w.ctx(tools=observer))
    seen.append(ActionState(observer.seen[0][0][0]))  # lo commiteado justo antes de llamar la tool
    seen.append(state.actions[0].state)
    state, verified, ev3 = w.manager.verify(state, VERIFY_NODE, w.ctx())
    seen.append(state.actions[0].state)
    assert seen == [ActionState.proposed, ActionState.confirmed, ActionState.executing, ActionState.executed,
                    ActionState.verified]
    assert (answered, written, verified) == ("yes", "ok", "verified")
    assert event_types(ev1 + ev2 + ev3) == [
        "action_confirmed", "action_dispatched", "tool_called", "tool_called", "action_verified"]
    assert state.facts["pqr"].value == state.facts["pqr_verificada"].value == {**RESOURCE, **ARGS}
    assert state.facts["pqr_verificada"].source.ref == prompt.action_id
    assert [c.idempotency_key for c in writes(w)] == [prompt.action_id]
    [readback] = readbacks(w)
    assert (readback.args, readback.idempotency_key) == ({"idempotency_key": prompt.action_id}, None)
    called, done = ev3
    assert (called.payload.node_id, called.payload.action_id) == ("verificar", prompt.action_id)
    assert (done.payload.result, done.payload.readback_call_id) == ("verified", called.payload.call_id)


@pytest.mark.parametrize(("effect", "expected"), [(True, "verified"), (False, "failed")])
def test_t_m3_02_uncertain_goes_to_verify_by_key(effect: bool, expected: str) -> None:
    w = World()
    outcome = Scripted(ToolStatus.uncertain, result=RESOURCE, error="HTTP 503", effect=effect)
    w.write_behaviour(script=(outcome,))
    state, result, _ = w.manager.execute_write(confirmed_state(w), WRITE_NODE, w.ctx())
    assert result == "uncertain"
    state, verified, _ = w.manager.verify(state, VERIFY_NODE, w.ctx())
    assert verified == expected
    assert state.actions[0].state is ActionState(expected)
    assert ("pqr_verificada" in state.facts) is effect
    assert len(writes(w)) == 1


def test_predicate_false_fails_but_keeps_the_readback() -> None:
    w = World()
    w.write_behaviour(handler=lambda args: {**args, "status": "Closed"})
    state, verified, _ = w.manager.verify(_written(w), VERIFY_NODE, w.ctx())
    assert verified == "failed"
    assert state.facts["pqr_verificada"].value["status"] == "Closed"  # type: ignore[index, call-overload]


def test_predicate_sees_the_readback_under_its_name() -> None:
    w = World()
    seen: list[object] = []

    def predicate(expr, data):  # type: ignore[no-untyped-def]
        seen.append((expr, data))
        return True

    w.manager.verify(_written(w), VERIFY_NODE, w.ctx(predicate=predicate))
    assert seen == [(VERIFY_NODE.config.predicate, {"readback": {**RESOURCE, **ARGS}})]


def test_predicate_that_raises_fails() -> None:
    w = World()

    def boom(expr, data):  # type: ignore[no-untyped-def]
        raise ZeroDivisionError

    _, verified, _ = w.manager.verify(_written(w), VERIFY_NODE, w.ctx(predicate=boom))
    assert verified == "failed"


def test_readback_exception_is_unavailable_and_does_not_prove_absence() -> None:
    """Un readback que no responde no prueba que el efecto no exista: la acción no pasa a `failed`."""
    w = World()
    state = _written(w)
    tools = RaisingTools(w.tools, TimeoutError("pqr-demo-1"), on="read")
    state, verified, events = w.manager.verify(state, VERIFY_NODE, w.ctx(tools=tools))
    assert verified == "unavailable"
    assert state.actions[0].state is ActionState.executed  # la tool había dicho ok; no se degrada a failed
    assert "pqr_verificada" not in state.facts
    assert (events[0].payload.status, events[0].payload.error) == (ToolStatus.error, "TimeoutError")
    assert events[1].payload.result == "unavailable"


@pytest.mark.parametrize("status", [ToolStatus.error, ToolStatus.timeout, ToolStatus.uncertain])
def test_readback_error_status_is_unavailable(status: ToolStatus) -> None:
    w = World()
    written = _written(w)
    tools = StatusTools(w.tools, status)
    state, verified, _ = w.manager.verify(written, VERIFY_NODE, w.ctx(tools=tools))
    assert verified == "unavailable" and state.actions[0].state is ActionState.executed


def test_unavailable_readback_after_a_crash_leaves_the_action_uncertain() -> None:
    """Recuperación (`executing`) con el readback caído: no se sabe si el efecto existe → `uncertain`."""
    w = World()
    out = crash_then_recover(w, "on_commit_2", readback=RaisingTools(w.tools, TimeoutError("x"), on="read"))
    assert out.result == "unavailable"
    assert out.recovered.actions[0].state is ActionState.uncertain
    assert len(writes(w)) == 1


def test_readback_that_answers_without_the_resource_still_fails() -> None:
    w = World()
    outcome = Scripted(ToolStatus.uncertain, result=RESOURCE, error="HTTP 503", effect=False)
    w.write_behaviour(script=(outcome,))
    state, _, _ = w.manager.execute_write(confirmed_state(w), WRITE_NODE, w.ctx())
    state, verified, _ = w.manager.verify(state, VERIFY_NODE, w.ctx())
    assert verified == "failed" and state.actions[0].state is ActionState.failed


def test_t_m3_11_denied_never_goes_to_verify() -> None:
    w = World()
    w.write_behaviour(script=(Scripted(ToolStatus.denied),))
    state, _, _ = w.manager.execute_write(confirmed_state(w), WRITE_NODE, w.ctx())
    with pytest.raises(IllegalTransition):
        w.manager.verify(state, VERIFY_NODE, w.ctx())
    assert readbacks(w) == []


def test_verify_only_by_idempotency_key() -> None:
    w = World()
    config = VERIFY_NODE.config.model_copy(update={"by": "fact:pqr"})
    by_fact = VERIFY_NODE.model_copy(update={"config": config})
    with pytest.raises(ValueError, match="idempotency_key"):
        w.manager.verify(_written(w), by_fact, w.ctx())
