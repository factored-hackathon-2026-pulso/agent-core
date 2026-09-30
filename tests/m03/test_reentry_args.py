"""Reentrada con token vigente (M3 §3.2, §11 (a) y (f)): si cambian los args o la versión de la tool, la
propuesta anterior se cancela (`args_changed`) y se congela una nueva; con los mismos, solo rota el token."""

from agent_core.domain import ActionState, InvalidationReason, ToolDef, canonical_bytes, sha256_hex
from tests.m03.harness import ARGS, CONFIRM, WRITE, World, persisted_proposal


def test_same_args_keep_the_action_and_rotate_the_token() -> None:
    w = World()
    state, prompt = persisted_proposal(w)
    state2, prompt2, events = w.manager.propose(state, CONFIRM, dict(ARGS), WRITE, w.ctx())
    assert prompt2.action_id == prompt.action_id and prompt2.token != prompt.token and events == []
    [action] = state2.actions
    assert action.args_hash == sha256_hex(canonical_bytes(ARGS))


def test_changed_args_cancel_the_old_action_and_freeze_a_new_one() -> None:
    w = World()
    state, prompt = persisted_proposal(w)
    changed = {**ARGS, "__cambiado__": "otro"}
    state2, prompt2, events = w.manager.propose(state, CONFIRM, changed, WRITE, w.ctx())
    assert prompt2.action_id != prompt.action_id
    old, new = state2.actions
    assert (old.action_id, old.state, old.cancel_reason) == (
        prompt.action_id, ActionState.cancelled, InvalidationReason.args_changed)
    assert (new.state, new.args, new.args_hash) == (ActionState.proposed, changed,
                                                    sha256_hex(canonical_bytes(changed)))
    assert new.idempotency_key == new.action_id != old.idempotency_key
    assert [e.type for e in events] == ["action_cancelled"]
    assert events[0].payload.reason is InvalidationReason.args_changed


def test_the_old_token_no_longer_confirms_after_args_changed() -> None:
    w = World()
    state, prompt = persisted_proposal(w)
    state, _, _ = w.manager.propose(state, CONFIRM, {**ARGS, "x": 1}, WRITE, w.ctx())
    state, answered, _ = w.manager.answer(state, CONFIRM, "yes", prompt.token, w.ctx())
    assert answered == "unclear" and all(a.state is not ActionState.confirmed for a in state.actions)


def test_a_new_tool_version_also_invalidates_the_proposal() -> None:
    w = World()
    state, prompt = persisted_proposal(w)
    newer = ToolDef.model_validate({**WRITE.model_dump(mode="json"), "version": "1.1.0"})
    _, prompt2, events = w.manager.propose(state, CONFIRM, dict(ARGS), newer, w.ctx())
    assert prompt2.action_id != prompt.action_id
    assert [e.payload.reason for e in events] == [InvalidationReason.args_changed]
