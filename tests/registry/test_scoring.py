from agent_core.domain import (
    ActionDispatched,
    ActionDispatchedPayload,
    ActionVerified,
    ActionVerifiedPayload,
    EngineEvent,
    EntityRef,
    Escalated,
    EscalatedPayload,
    Outcome,
    RunClosed,
    RunClosedPayload,
)
from agent_core.registry.evaluation.scoring import aggregate, score_run
from agent_core.registry.suite import Expect
from testing.builders import NOW

_BASE = {"run_id": "run-1", "release": "rel", "ts": NOW}


def _dispatched(aid: str, tool: str = "radicar_pqr") -> EngineEvent:
    payload = ActionDispatchedPayload(action_id=aid, tool=EntityRef(id=tool, version="1.0.0"))
    return ActionDispatched(event_id="e-d", **_BASE, payload=payload)


def _verified(aid: str, result: str = "verified") -> EngineEvent:
    payload = ActionVerifiedPayload(action_id=aid, result=result, readback_call_id="c")  # type: ignore[arg-type]
    return ActionVerified(event_id=f"e-v-{aid}", **_BASE, payload=payload)


def _closed(outcome: str) -> EngineEvent:
    payload = RunClosedPayload(outcome=Outcome(outcome), closed_by="flow")
    return RunClosed(event_id="e-c", **_BASE, payload=payload)


def test_resolved_with_verified_action_passes() -> None:  # T-REG-25
    events = [_dispatched("a1"), _verified("a1"), _closed("resolved")]
    expect = Expect(outcome=Outcome.resolved, actions_verified=["radicar_pqr"], escalated=False)
    s = score_run(events, expect, [])
    zero = {"unverified_writes": 0, "unsupported_success": 0, "sensitive_leaks": 0}
    assert s.passed and s.guardrails == zero


def test_unverified_write_and_unsupported_success_are_counted() -> None:
    events = [_dispatched("a1"), _verified("a1", "failed"), _closed("resolved")]
    s = score_run(events, Expect(outcome=Outcome.resolved), [])
    assert s.guardrails["unverified_writes"] == 1 and s.guardrails["unsupported_success"] == 1


def test_outcome_mismatch_and_missing_action_fail_with_reasons() -> None:
    expect = Expect(outcome=Outcome.resolved, actions_verified=["radicar_pqr"])
    s = score_run([_closed("cancelled")], expect, [])
    assert not s.passed and len(s.failures) == 2


def test_escalation_expectation() -> None:
    payload = EscalatedPayload(
        reason_code="policy:test", target_queue="q", priority="normal", handoff_ref="h")
    esc = Escalated(event_id="e-x", **_BASE, payload=payload)
    assert not score_run([esc], Expect(escalated=False), []).passed
    assert score_run([esc], Expect(escalated=True), []).escalated


def test_sensitive_value_in_any_event_is_a_leak() -> None:
    events = [_dispatched("4111-1111")]
    assert score_run(events, Expect(), ["4111-1111"]).guardrails["sensitive_leaks"] == 1


def test_aggregate_primary_is_decimal_fraction() -> None:
    ok = score_run([_closed("resolved")], Expect(outcome=Outcome.resolved), [])
    bad = score_run([_closed("failed")], Expect(outcome=Outcome.resolved), [])
    m = aggregate([ok, ok, bad])
    assert str(m.primary) == "0.6667" and m.runs == 3
