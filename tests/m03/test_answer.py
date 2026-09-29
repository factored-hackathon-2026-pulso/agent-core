"""`answer` y `expire_tokens` (M3 §3.3): token vigente, tope de `unclear` y vencimiento."""

from datetime import timedelta

import pytest

from agent_core.domain import ActionState, IllegalTransition, InvalidationReason
from tests.m03.harness import ARGS, CONFIRM, TURN_ID, WRITE, World, base_state, event_types


def _proposed(w: World):  # type: ignore[no-untyped-def]
    return w.manager.propose(base_state(), CONFIRM, ARGS, WRITE, w.ctx())


def test_yes_by_button_confirms() -> None:
    w = World()
    state, prompt, _ = _proposed(w)
    state, result, events = w.manager.answer(state, CONFIRM, "yes", prompt.token, w.ctx())
    assert result == "yes"
    assert state.actions[0].state is ActionState.confirmed
    [event] = events
    assert event_types(events) == ["action_confirmed"]
    assert event.payload.action_id == prompt.action_id
    assert (event.payload.source, event.turn_id) == ("button", TURN_ID)


def test_yes_by_text_uses_the_proposed_token() -> None:
    w = World()
    state, _, _ = _proposed(w)
    state, result, events = w.manager.answer(state, CONFIRM, "yes", None, w.ctx())
    assert result == "yes"
    assert events[0].payload.source == "understand"


def test_t_m3_06_old_token_after_rotation_does_not_confirm() -> None:
    w = World()
    state, first, _ = _proposed(w)
    state, second, _ = w.manager.propose(state, CONFIRM, ARGS, WRITE, w.ctx())
    state, result, events = w.manager.answer(state, CONFIRM, "yes", first.token, w.ctx())
    assert (result, events) == ("unclear", [])
    assert state.actions[0].state is ActionState.proposed
    assert "confirmar" not in state.node_attempts  # un token que no coincide no suma intento
    state, result, _ = w.manager.answer(state, CONFIRM, "yes", second.token, w.ctx())
    assert result == "yes"


def test_t_m3_07_reentry_with_expired_token_cancels_and_freezes_new_one() -> None:
    w = World()
    state, old, _ = _proposed(w)
    w.clock.advance(WRITE.confirmation_ttl)  # now == token_exp: ya venció
    state, new, events = w.manager.propose(state, CONFIRM, ARGS, WRITE, w.ctx())
    assert new.action_id != old.action_id
    by_id = {a.action_id: a for a in state.actions}
    assert by_id[old.action_id].state is ActionState.cancelled
    assert by_id[old.action_id].cancel_reason is InvalidationReason.token_expired
    assert by_id[new.action_id].state is ActionState.proposed
    assert event_types(events) == ["action_cancelled"]
    assert events[0].payload.reason is InvalidationReason.token_expired
    state, result, _ = w.manager.answer(state, CONFIRM, "yes", old.token, w.ctx())
    assert result == "unclear"
    assert {a.action_id: a.state for a in state.actions}[new.action_id] is ActionState.proposed


def test_yes_with_expired_token_cancels_and_is_unclear() -> None:
    w = World()
    state, prompt, _ = _proposed(w)
    w.clock.advance(WRITE.confirmation_ttl + timedelta(seconds=1))
    state, result, events = w.manager.answer(state, CONFIRM, "yes", prompt.token, w.ctx())
    assert result == "unclear"
    assert state.actions[0].state is ActionState.cancelled
    assert [e.payload.reason for e in events] == [InvalidationReason.token_expired]


def test_no_cancels_denied_by_user() -> None:
    w = World()
    state, _, _ = _proposed(w)
    state, result, events = w.manager.answer(state, CONFIRM, "no", None, w.ctx())
    assert result == "no"
    assert state.actions[0].cancel_reason is InvalidationReason.denied_by_user
    assert [e.payload.reason for e in events] == [InvalidationReason.denied_by_user]


def test_t_m3_05_two_unclear_reach_max_attempts() -> None:
    w = World()
    state, _, _ = _proposed(w)
    state, first, events = w.manager.answer(state, CONFIRM, "unclear", None, w.ctx())
    assert (first, events, state.node_attempts["confirmar"]) == ("unclear", [], 1)
    assert state.actions[0].state is ActionState.proposed
    state, second, events = w.manager.answer(state, CONFIRM, "unclear", None, w.ctx())
    assert second == "max_attempts"
    [action] = state.actions
    assert (action.state, action.cancel_reason) == (ActionState.cancelled, InvalidationReason.max_attempts)
    assert [e.payload.reason for e in events] == [InvalidationReason.max_attempts]


def test_answer_without_proposed_action_is_a_bug() -> None:
    w = World()
    with pytest.raises(IllegalTransition):
        w.manager.answer(base_state(), CONFIRM, "yes", None, w.ctx())


def test_expire_tokens_cancels_only_expired() -> None:
    w = World()
    state, _, _ = _proposed(w)
    state, events = w.manager.expire_tokens(state, turn_id=TURN_ID)
    assert (events, state.actions[0].state) == ([], ActionState.proposed)
    w.clock.advance(WRITE.confirmation_ttl)
    state, events = w.manager.expire_tokens(state, turn_id=TURN_ID)
    assert state.actions[0].cancel_reason is InvalidationReason.token_expired
    assert [(e.type, e.turn_id) for e in events] == [("action_cancelled", TURN_ID)]
