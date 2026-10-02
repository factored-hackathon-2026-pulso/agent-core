from decimal import Decimal
from typing import Any

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
    ResponseEmitted,
    RunClosed,
    RunClosedPayload,
)
from agent_core.registry.evaluation.scoring import PLATFORM_GUARDRAILS, measure, score_run
from agent_core.registry.suite import Assertion, Expect
from testing.builders import NOW
from tests.registry.eval_support import metric, scenario, suite

_BASE: dict[str, Any] = {"run_id": "run-1", "release": "rel", "ts": NOW}
ZERO = {name: 0 for name in PLATFORM_GUARDRAILS}


def _dispatched(aid: str, tool: str = "radicar_pqr") -> EngineEvent:
    payload = ActionDispatchedPayload(action_id=aid, tool=EntityRef(id=tool, version="1.0.0"))
    return ActionDispatched(event_id="e-d", **_BASE, payload=payload)


def _verified(aid: str, result: str = "verified") -> EngineEvent:
    payload = ActionVerifiedPayload(action_id=aid, result=result, readback_call_id="c")  # type: ignore[arg-type]
    return ActionVerified(event_id=f"e-v-{aid}", **_BASE, payload=payload)


def _closed(outcome: str) -> EngineEvent:
    payload = RunClosedPayload(outcome=Outcome(outcome), closed_by="flow")
    return RunClosed(event_id="e-c", **_BASE, payload=payload)


def _escalated() -> EngineEvent:
    payload = EscalatedPayload(
        reason_code="policy:test", target_queue="q", priority="normal", handoff_ref="h")
    return Escalated(event_id="e-x", **_BASE, payload=payload)


def _emitted(kind: str, ok: bool, failures: list[str]) -> EngineEvent:
    return ResponseEmitted.model_validate({**_BASE, "event_id": "e-r", "payload": {
        "kind": kind, "validator": {"ok": ok, "failures": failures}, "fallback_used": kind == "template"}})


def test_resolved_with_verified_action_passes() -> None:  # T-REG-25
    events = [_dispatched("a1"), _verified("a1"), _closed("resolved")]
    expect = Expect(outcome=Outcome.resolved, actions_verified=["radicar_pqr"], escalated=False)
    s = score_run(events, expect, [])
    assert s.passed and s.guardrails == ZERO


def test_unverified_write_and_unsupported_success_are_counted() -> None:
    events = [_dispatched("a1"), _verified("a1", "failed"), _closed("resolved")]
    s = score_run(events, Expect(outcome=Outcome.resolved), [])
    assert s.guardrails["platform_unverified_write"] == 1
    assert s.guardrails["platform_unverified_success_claim"] == 1


def test_outcome_mismatch_and_missing_action_fail_with_reasons() -> None:
    expect = Expect(outcome=Outcome.resolved, actions_verified=["radicar_pqr"])
    s = score_run([_closed("cancelled")], expect, [])
    assert not s.passed and len(s.failures) == 2


def test_escalation_expectation() -> None:
    assert not score_run([_escalated()], Expect(escalated=False), []).passed
    assert score_run([_escalated()], Expect(escalated=True), []).escalated


def test_sensitive_value_in_any_event_is_a_leak() -> None:
    leaked = score_run([_dispatched("4111-1111")], Expect(), ["4111-1111"])
    assert leaked.guardrails["platform_pii_leak"] == 1


def test_unapproved_page_citation_is_counted_only_on_generated_answers() -> None:  # decision D6
    events = [_emitted("generated", False, ["page_audience"]), _emitted("template", True, ["page_citations"]),
              _emitted("generated", True, [])]
    assert score_run(events, Expect(), []).guardrails["platform_unapproved_knowledge_citation"] == 1


def test_a_failed_assertion_fails_the_run() -> None:
    must = Assertion(event="engine.escalated")
    never = Assertion(event="engine.escalated", expect="none")
    assert score_run([_escalated()], Expect(), [], [must]).passed
    failing = score_run([_escalated()], Expect(), [], [never])
    assert not failing.passed and "aserción 1" in failing.failures[0]


def test_platform_guardrails_are_reserved_names() -> None:
    assert len(PLATFORM_GUARDRAILS) == len(set(PLATFORM_GUARDRAILS)) == 4
    assert all(name.startswith("platform_") for name in PLATFORM_GUARDRAILS)


RESOLVED = metric("resueltos", event="engine.run_closed",
                  where=[{"field": "outcome", "op": "eq", "value": "resolved"}])


def test_measure_pools_events_and_needs_every_repetition() -> None:  # decision D4
    ok = score_run([_closed("resolved")], Expect(outcome=Outcome.resolved), [])
    bad = score_run([_closed("failed")], Expect(outcome=Outcome.resolved), [])
    write = score_run([_dispatched("a1")], Expect(), [])
    s = suite([scenario("s1", repetitions=2), scenario("s2")])
    m = measure(s, [RESOLVED], [("s1", ok, [_closed("resolved")]), ("s1", bad, [_closed("failed")]),
                                ("s2", write, [_dispatched("a1")])])
    assert m.scenarios == {"s1": False, "s2": True}
    assert m.metrics["resueltos"] == Decimal(1)
    assert m.metrics["platform_unverified_write"] == Decimal(1)
    assert {k for k in m.metrics if k.startswith("platform_")} == set(PLATFORM_GUARDRAILS)


def test_measure_without_runs_measures_nothing() -> None:
    assert measure(suite([scenario("s1")]), [RESOLVED], []).model_dump() == {
        "status": "ok", "metrics": {}, "scenarios": {}}


def test_unmeasurable_metrics_are_left_out() -> None:  # decision D5
    funnel = metric("embudo", role="monitor", event="registry.proposal_created")
    ok = score_run([_closed("resolved")], Expect(), [])
    m = measure(suite([scenario("s1")]), [funnel], [("s1", ok, [_closed("resolved")])])
    assert "embudo" not in m.metrics
