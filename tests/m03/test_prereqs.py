"""M3 depende solo de M0: estos símbolos deben existir tal como los usa el plan."""

from agent_core.domain import (
    Action,
    ActionCancelled,
    ActionConfirmed,
    ActionDispatched,
    ActionState,
    ActionVerified,
    ConfirmationPrompt,
    ConfirmNode,
    IllegalTransition,
    InvalidationReason,
    ToolCalled,
    VerifyNode,
    WriteToolNode,
    canonical_bytes,
    sha256_hex,
)
from agent_core.ports import IdKind, ToolCallContext, ToolResult, ToolStatus, UnitOfWorkFactory
from testing.fakes.storage import InMemoryStore, SimulatedCrash
from testing.fakes.tools import FakeToolExecutor, Scripted


def test_m0_symbols_exist() -> None:
    assert {s.value for s in ActionState} == {
        "proposed", "confirmed", "executing", "executed", "uncertain", "denied", "verified", "failed",
        "cancelled",
    }
    assert InvalidationReason.token_expired and InvalidationReason.denied_by_user
    assert IdKind.action and IdKind.event and IdKind.fact and IdKind.call
    assert ToolStatus.step_up_required and ToolStatus.uncertain
    fields = {"args_hash", "confirmation_token_hash", "token_exp", "idempotency_key"}
    assert set(Action.model_fields) >= fields
    for name in (ActionCancelled, ActionConfirmed, ActionDispatched, ActionVerified, ToolCalled,
                 ConfirmationPrompt, ConfirmNode, VerifyNode, WriteToolNode, IllegalTransition,
                 ToolCallContext, ToolResult, UnitOfWorkFactory, InMemoryStore, SimulatedCrash,
                 FakeToolExecutor, Scripted):
        assert name is not None
    assert len(sha256_hex(canonical_bytes({"a": 1}))) == 64
