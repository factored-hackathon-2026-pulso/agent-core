"""T-M3-03/04 sobre Postgres real: caída entre los dos commits de una escritura (M3 §5)."""

import pytest

from agent_core.adapters.postgres_uow import PostgresStore
from agent_core.audit import AuditLog
from agent_core.domain import ActionState
from testing.fakes.storage import SimulatedCrash
from tests.m03.harness import CONFIRM, WRITE_NODE, World, persisted_proposal, reload, writes
from tests.m03.scenarios import CrashAt, crash_then_recover
from tests.support.pg import postgres_store

pytestmark = pytest.mark.integration


def _world(pg: PostgresStore) -> World:
    """M3 sobre Postgres: los eventos de M3 se encadenan con el `AuditLog` real de M11."""
    return World(uow_factory=pg.uow, record=AuditLog(pg.audit()).recorder())


@pytest.mark.parametrize(("at", "calls", "expected"), [
    ("after_commit_1", 0, "failed"),
    ("after_call", 1, "verified"),
    ("on_commit_2", 1, "verified"),
])
def test_t_m3_03_caida_entre_commits_va_a_verify_sin_re_ejecutar(at: CrashAt, calls: int,
                                                                  expected: str) -> None:
    with postgres_store("m4_m3crash") as pg:
        w = _world(pg)
        out = crash_then_recover(w, at)
        assert out.loaded.actions[0].state is ActionState.executing
        assert out.writes_before_recovery == calls and len(writes(w)) == calls
        assert out.result == expected and out.recovered.actions[0].state is ActionState(expected)


def test_t_m3_04_reintento_del_turno_reusa_la_clave_de_idempotencia() -> None:
    with postgres_store("m4_m3crash") as pg:
        w = _world(pg)
        state, prompt = persisted_proposal(w)
        state, _, _ = w.manager.answer(state, CONFIRM, "yes", prompt.token, w.ctx())
        with pytest.raises(SimulatedCrash):
            w.manager.execute_write(state, WRITE_NODE, w.ctx(crash="on_commit_1"))
        retried = reload(w)  # el commit 1 no llegó: la acción persistida sigue proposed
        assert [(a.action_id, a.state) for a in retried.actions] == [(prompt.action_id, ActionState.proposed)]
        retried, answered, _ = w.manager.answer(retried, CONFIRM, "yes", prompt.token, w.ctx())
        retried, result, _ = w.manager.execute_write(retried, WRITE_NODE, w.ctx())
        assert (answered, result) == ("yes", "ok")
        assert [c.idempotency_key for c in writes(w)] == [prompt.action_id]
