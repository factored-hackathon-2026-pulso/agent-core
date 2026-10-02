"""Escritura `draft` (ADR 0019): la acción se congela directo en `confirmed`, sin token, y sigue
execute → verify con los mismos dos commits y la misma recuperación."""

import pytest

from agent_core.domain import (
    ActionState,
    EntityRef,
    InvalidationReason,
    RunState,
    ToolDef,
    VerifyNode,
    WriteToolNode,
    canonical_bytes,
    sha256_hex,
)
from testing.fakes.tools import RecordedCall
from tests.m03.harness import (
    ARGS,
    RESOURCE,
    WRITE,
    WRITE_NODE,
    CrashAfterCall,
    ProcessDied,
    World,
    base_state,
    event_types,
    persist,
    reload,
)

DRAFT = ToolDef.model_validate({
    "id": "guardar_borrador", "version": "1.0.0", "risk_class": "write_draft", "min_auth_level": "session",
    "idempotent": True, "readback_by": "idempotency_key", "source": "borradores"})
DRAFT_REF = EntityRef(id=DRAFT.id, version=DRAFT.version)
READBACK = ToolDef.model_validate({
    "id": "leer_borrador", "version": "1.0.0", "risk_class": "read", "min_auth_level": "session",
    "idempotent": True, "source": "borradores"})
DRAFT_NODE = WriteToolNode.model_validate({
    "id": "guardar", "type": "tool",
    "config": {"draft": True, "tool": "guardar_borrador@1.0.0", "args": {"titulo": "t"},
               "save_as": "borrador"},
    "next": {"ok": "verificar", "uncertain": "verificar", "denied": "esc"}})
VERIFY = VerifyNode.model_validate({
    "id": "verificar", "type": "verify",
    "config": {"readback": "leer_borrador@1.0.0", "by": "idempotency_key",
               "predicate": {"==": [{"var": "readback.status"}, "Open"]}, "save_as": "borrador_ok"},
    "next": {"verified": "fin", "failed": "esc"}})


def _world() -> World:
    w = World()
    w.tools.register(DRAFT, handler=lambda args: {**RESOURCE, **args})
    w.tools.register_readback(READBACK, of=DRAFT_REF)
    return w


def _frozen(w: World) -> RunState:
    state = persist(w, base_state())
    return w.manager.freeze_draft_write(state, DRAFT_NODE, ARGS, DRAFT, w.ctx())


def _draft_calls(w: World) -> list[RecordedCall]:
    return [c for c in w.tools.calls if c.tool == DRAFT_REF]


def test_freeze_creates_a_confirmed_action_without_token() -> None:  # Review Focus 5
    state = _frozen(_world())
    [action] = state.actions
    assert action.state is ActionState.confirmed
    assert (action.write_node_id, action.confirm_node_id, action.confirmation_token_hash,
            action.token_exp) == ("guardar", None, None, None)
    assert action.idempotency_key == action.action_id
    assert action.args == ARGS and action.args_hash == sha256_hex(canonical_bytes(ARGS))
    assert action.tool == DRAFT_REF


def test_freeze_on_reentry_keeps_the_frozen_action() -> None:
    w = _world()
    state = _frozen(w)
    again = w.manager.freeze_draft_write(state, DRAFT_NODE, {"otra": "cosa"}, DRAFT, w.ctx())
    assert [a.action_id for a in again.actions] == [state.actions[0].action_id]
    assert again.actions[0].args == ARGS  # congelada: los args nuevos no la cambian


def test_freeze_rejects_a_tool_that_is_not_the_declared_write_draft() -> None:  # Review Focus 5
    w = _world()
    state = persist(w, base_state())
    with pytest.raises(ValueError):
        w.manager.freeze_draft_write(state, DRAFT_NODE, ARGS, WRITE, w.ctx())  # write_reversible


def test_freeze_rejects_a_node_that_is_not_a_draft_write() -> None:
    w = _world()
    state = persist(w, base_state())
    with pytest.raises(ValueError):
        w.manager.freeze_draft_write(state, WRITE_NODE, ARGS, DRAFT, w.ctx())  # forma con confirm


def test_execute_runs_a_draft_with_two_commits_and_the_action_id_as_key() -> None:
    w = _world()
    state = _frozen(w)
    before = state.state_version
    state, result, events = w.manager.execute_write(state, DRAFT_NODE, w.ctx())
    [action] = state.actions
    assert (result, action.state) == ("ok", ActionState.executed)
    assert state.state_version == before + 2
    assert event_types(events) == ["action_dispatched", "tool_called"]
    assert state.facts["borrador"].value == {**RESOURCE, **ARGS}
    assert [(c.idempotency_key, c.args) for c in _draft_calls(w)] == [(action.action_id, ARGS)]
    assert reload(w).actions[0].state is ActionState.executed


def test_verify_after_a_draft_write_is_verified() -> None:
    w = _world()
    state, _, _ = w.manager.execute_write(_frozen(w), DRAFT_NODE, w.ctx())
    state, result, _ = w.manager.verify(state, VERIFY, w.ctx())
    assert result == "verified" and state.actions[0].state is ActionState.verified


def test_a_crash_after_the_call_recovers_through_verify_without_rewriting() -> None:
    w = _world()
    with pytest.raises(ProcessDied):
        w.manager.execute_write(_frozen(w), DRAFT_NODE, w.ctx(tools=CrashAfterCall(w.tools)))
    loaded = reload(w)
    [action] = loaded.actions
    assert (action.state, action.write_node_id) == (ActionState.executing, "guardar")
    assert w.manager.pending_recovery(loaded) == [action.action_id]
    calls = len(_draft_calls(w))
    recovered, result, _ = w.manager.verify(loaded, VERIFY, w.ctx())
    assert result == "verified" and recovered.actions[0].state is ActionState.verified
    assert len(_draft_calls(w)) == calls  # nunca se re-ejecuta (ADR 0007 §4)


def test_invalidate_cancels_a_frozen_draft_that_has_not_run() -> None:
    w = _world()
    state, _ = w.manager.invalidate(_frozen(w), InvalidationReason.abandoned)
    assert state.actions[0].state is ActionState.cancelled


def test_expire_tokens_ignores_draft_actions() -> None:
    w = _world()
    state, events = w.manager.expire_tokens(_frozen(w))
    assert events == [] and state.actions[0].state is ActionState.confirmed
