"""Determinismo: dos mundos idénticos producen los mismos eventos (ids y ts incluidos) y el mismo estado."""

from typing import Any

from tests.m03.harness import CONFIRM, VERIFY_NODE, WRITE_NODE, World, persisted_proposal


def _run() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    w = World()
    state, prompt = persisted_proposal(w)
    state, answered, ev1 = w.manager.answer(state, CONFIRM, "yes", prompt.token, w.ctx())
    assert answered == "yes"
    state, written, ev2 = w.manager.execute_write(state, WRITE_NODE, w.ctx())
    assert written == "ok"
    state, verified, ev3 = w.manager.verify(state, VERIFY_NODE, w.ctx())
    assert verified == "verified"
    events = [e.model_dump(mode="json") for e in (*ev1, *ev2, *ev3)]
    return events, state.model_dump(mode="json")


def test_two_identical_worlds_produce_identical_events_and_state() -> None:
    events_a, state_a = _run()
    events_b, state_b = _run()
    assert events_a
    assert events_a == events_b
    assert state_a == state_b
