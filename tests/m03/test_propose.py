"""`propose` (M3 §3.2): congelado, hash del token, TTL, reentrada con rotación (T-M3-06) y copia (T-M3-09)."""

from datetime import timedelta

import pytest

from agent_core.actions.confirmation import token_hash
from agent_core.domain import ActionState, IllegalTransition, canonical_bytes, sha256_hex
from testing.builders import run_state
from tests.m03.harness import (
    ARGS,
    CONFIRM,
    CONFIRM_SIN_REPROMPT,
    FLOW_REF,
    READBACK,
    WRITE,
    WRITE_REF,
    World,
    base_state,
)


def test_propose_freezes_action() -> None:
    w = World()
    state, prompt, events = w.manager.propose(base_state(), CONFIRM, ARGS, WRITE, w.ctx())
    [action] = state.actions
    assert action.state is ActionState.proposed
    assert action.action_id == prompt.action_id == action.idempotency_key == "action-0001"
    assert (action.tool, action.flow, action.confirm_node_id) == (WRITE_REF, FLOW_REF, "confirmar")
    assert action.args == ARGS
    assert action.args_hash == sha256_hex(canonical_bytes(ARGS))
    assert action.created_at == w.clock.now()
    assert action.token_exp == prompt.expires_at == w.clock.now() + WRITE.confirmation_ttl
    assert action.confirmation_token_hash == token_hash(prompt.token) == sha256_hex(prompt.token.encode())
    assert prompt.token not in state.model_dump_json()
    assert len(prompt.token) >= 22  # 128 bits en base64 url-safe sin relleno
    assert prompt.summary.text == "resumen:t/resumen_pqr"
    assert events == []


def test_t_m3_09_frozen_args_are_a_copy() -> None:
    w = World()
    resolved = {"transaction_id": "tx-demo-1", "detalle": {"notas": ["a"]}}
    state, _, _ = w.manager.propose(base_state(), CONFIRM, resolved, WRITE, w.ctx())
    resolved["transaction_id"] = "tx-otro"
    resolved["detalle"]["notas"].append("b")  # type: ignore[index, union-attr]
    assert state.actions[0].args == {"transaction_id": "tx-demo-1", "detalle": {"notas": ["a"]}}


def test_t_m3_06_reentry_with_live_token_reuses_action_and_rotates_token() -> None:
    w = World()
    state, first, _ = w.manager.propose(base_state(), CONFIRM, ARGS, WRITE, w.ctx())
    w.clock.advance(timedelta(minutes=4))
    state, second, events = w.manager.propose(state, CONFIRM, ARGS, WRITE, w.ctx())
    assert len(state.actions) == 1
    assert [a.state for a in state.actions] == [ActionState.proposed]
    assert second.action_id == first.action_id
    assert second.expires_at == first.expires_at
    assert second.token != first.token  # rotado: solo se guarda el hash (M3 rev. 2 §3.2)
    assert state.actions[0].confirmation_token_hash == token_hash(second.token)
    assert second.summary.text == "resumen:t/reprompt_pqr"
    assert events == []


def test_reentry_without_reprompt_uses_summary() -> None:
    w = World()
    state, _, _ = w.manager.propose(base_state(), CONFIRM_SIN_REPROMPT, ARGS, WRITE, w.ctx())
    _, again, _ = w.manager.propose(state, CONFIRM_SIN_REPROMPT, ARGS, WRITE, w.ctx())
    assert again.summary.text == "resumen:t/resumen_pqr"
    assert [ref.id for ref in w.rendered] == ["t/resumen_pqr", "t/resumen_pqr"]


def test_propose_rejects_a_tool_def_that_is_not_the_nodes_write() -> None:
    w = World()
    with pytest.raises(ValueError, match="no es la escritura"):
        w.manager.propose(base_state(), CONFIRM, ARGS, READBACK, w.ctx())


def test_propose_requires_active_flow() -> None:
    w = World()
    with pytest.raises(IllegalTransition):
        w.manager.propose(run_state(), CONFIRM, ARGS, WRITE, w.ctx())


def test_context_of_another_run_is_rejected() -> None:
    w = World()
    with pytest.raises(ValueError, match="otro run"):
        w.manager.propose(base_state(), CONFIRM, ARGS, WRITE, w.ctx(run_id="run-9999"))
