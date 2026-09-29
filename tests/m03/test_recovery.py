"""Recuperación tras una caída (T-M3-03) y reintento del turno con la misma clave (T-M3-04)."""

import pytest

from agent_core.domain import ActionState, IllegalTransition
from testing.fakes.storage import SimulatedCrash
from tests.m03.harness import CONFIRM, WRITE_NODE, World, confirmed_state, persisted_proposal, reload, writes
from tests.m03.scenarios import CrashAt, crash_then_recover


@pytest.mark.parametrize(("at", "calls", "expected"), [
    ("after_commit_1", 0, "failed"),   # la tool no llegó a llamarse: el readback no encuentra nada
    ("after_call", 1, "verified"),     # la tool aplicó el efecto y el proceso murió antes del commit 2
    ("on_commit_2", 1, "verified"),    # el commit 2 falló: el efecto queda probado por el readback
])
def test_t_m3_03_crash_between_commits_goes_to_verify(at: CrashAt, calls: int, expected: str) -> None:
    w = World()
    out = crash_then_recover(w, at)
    assert out.loaded.actions[0].state is ActionState.executing
    assert out.writes_before_recovery == calls
    assert len(writes(w)) == calls  # verify nunca re-ejecuta
    assert out.result == expected
    assert out.recovered.actions[0].state is ActionState(expected)


def test_executing_action_is_never_re_executed() -> None:
    w = World()
    out = crash_then_recover(w, "on_commit_2")
    with pytest.raises(IllegalTransition):
        w.manager.execute_write(out.loaded, WRITE_NODE, w.ctx())
    assert len(writes(w)) == 1


def test_t_m3_04_turn_retry_reuses_idempotency_key() -> None:
    w = World()
    state, prompt = persisted_proposal(w)
    state, _, _ = w.manager.answer(state, CONFIRM, "yes", prompt.token, w.ctx())
    with pytest.raises(SimulatedCrash):
        w.manager.execute_write(state, WRITE_NODE, w.ctx(crash="on_commit_1"))
    retried = reload(w)  # el commit 1 no llegó: la acción persistida sigue proposed, con el mismo id
    assert [(a.action_id, a.state) for a in retried.actions] == [(prompt.action_id, ActionState.proposed)]
    retried, answered, _ = w.manager.answer(retried, CONFIRM, "yes", prompt.token, w.ctx())
    retried, result, _ = w.manager.execute_write(retried, WRITE_NODE, w.ctx())
    assert (answered, result) == ("yes", "ok")
    assert [c.idempotency_key for c in writes(w)] == [prompt.action_id]


def test_pending_recovery_ignores_other_states() -> None:
    w = World()
    assert w.manager.pending_recovery(confirmed_state(w)) == []
