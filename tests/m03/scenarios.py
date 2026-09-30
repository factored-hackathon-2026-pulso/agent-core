"""Escenario de caída entre commits (M3 §5, §7). Es reutilizable: M4 lo correrá con un `World` sobre
Postgres (DoD de M3, diferido a M4)."""

from dataclasses import dataclass
from typing import Literal

import pytest

from agent_core.actions import VerifyResult
from agent_core.domain import RunState
from agent_core.ports import ToolExecutor
from testing.fakes.storage import SimulatedCrash
from tests.m03.harness import (
    CONFIRM,
    VERIFY_NODE,
    WRITE_NODE,
    CrashAfterCall,
    ProcessDied,
    World,
    persisted_proposal,
    reload,
    writes,
)

CrashAt = Literal["after_commit_1", "after_call", "on_commit_2"]


@dataclass(frozen=True)
class CrashOutcome:
    action_id: str
    writes_before_recovery: int
    loaded: RunState
    recovered: RunState
    result: VerifyResult


def crash_then_recover(w: World, at: CrashAt, *, readback: ToolExecutor | None = None) -> CrashOutcome:
    """Turno de confirmación que muere en `at`; al recargar, la acción va a `verify` sin re-ejecutarse."""
    state, prompt = persisted_proposal(w)
    state, answered, _ = w.manager.answer(state, CONFIRM, "yes", prompt.token, w.ctx())
    assert answered == "yes"
    if at == "after_call":
        with pytest.raises(ProcessDied):
            w.manager.execute_write(state, WRITE_NODE, w.ctx(tools=CrashAfterCall(w.tools)))
    else:
        with pytest.raises(SimulatedCrash):
            w.manager.execute_write(state, WRITE_NODE, w.ctx(crash=at))
    loaded = reload(w)
    assert w.manager.pending_recovery(loaded) == [prompt.action_id]
    before = len(writes(w))
    ctx = w.ctx(tools=readback) if readback else w.ctx()
    recovered, result, _ = w.manager.verify(loaded, VERIFY_NODE, ctx)
    return CrashOutcome(prompt.action_id, before, loaded, recovered, result)
