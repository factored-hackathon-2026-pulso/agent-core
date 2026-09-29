"""Los eventos de M3 validan contra el esquema de M0, M3 figura como su emisor y ningún evento ni el estado
llevan el token de confirmación (DoD común, índice §8)."""

from pydantic import TypeAdapter

from agent_core.domain import EVENT_EMITTERS, AnyEvent, InvalidationReason, ToolStatus
from testing.fakes.tools import Scripted
from tests.m03.harness import (
    ARGS,
    CONFIRM,
    VERIFY_NODE,
    WRITE,
    WRITE_NODE,
    World,
    base_state,
    persisted_proposal,
)

_ADAPTER: TypeAdapter[AnyEvent] = TypeAdapter(AnyEvent)


def _every_kind_of_event():  # type: ignore[no-untyped-def]
    w = World()
    w.write_behaviour(script=(Scripted(ToolStatus.step_up_required),))
    state, prompt = persisted_proposal(w)
    state, _, confirmed = w.manager.answer(state, CONFIRM, "yes", prompt.token, w.ctx())
    state, _, stepped_up = w.manager.execute_write(state, WRITE_NODE, w.ctx())
    state, _, written = w.manager.execute_write(state, WRITE_NODE, w.ctx())
    state, _, verified = w.manager.verify(state, VERIFY_NODE, w.ctx())
    other, other_prompt, _ = w.manager.propose(base_state(), CONFIRM, ARGS, WRITE, w.ctx())
    other, cancelled = w.manager.invalidate(other, InvalidationReason.cancel)
    events = confirmed + stepped_up + written + verified + cancelled
    return events, [prompt.token, other_prompt.token], [state, other]


def test_events_validate_against_m0_and_m3_is_their_emitter() -> None:
    events, _, _ = _every_kind_of_event()
    assert {e.type for e in events} == {
        "action_confirmed", "action_dispatched", "tool_called", "action_verified", "action_cancelled"}
    for event in events:
        assert "M3" in EVENT_EMITTERS[event.type]
        assert _ADAPTER.validate_json(event.model_dump_json()) == event
        assert (event.seq, event.prev_hash, event.hash) == (None, None, None)  # los asigna M11


def test_confirmation_token_never_leaves_the_prompt() -> None:
    events, tokens, states = _every_kind_of_event()
    dumps = [e.model_dump_json() for e in events] + [s.model_dump_json() for s in states]
    for token in tokens:
        assert not [d for d in dumps if token in d]
