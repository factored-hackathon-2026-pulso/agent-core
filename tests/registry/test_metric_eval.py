"""In-memory evaluator of the metric DSL (ADR 0020; semantics of decision D4)."""

from decimal import Decimal
from typing import Any

from agent_core.domain import (
    ActionDispatched,
    EngineEvent,
    Escalated,
    JudgeExpr,
    MetricExpr,
    ResponseEmitted,
    ToolCalled,
    TurnCompleted,
)
from agent_core.registry.evaluation.metric_eval import assertion_holds, catalog_record, evaluate_metric
from agent_core.registry.suite import Assertion
from testing.builders import NOW

ENV: dict[str, Any] = {"run_id": "run-1", "release": "rel-1", "ts": NOW}


def turn(ms: int, degraded: bool = False) -> EngineEvent:
    return TurnCompleted.model_validate({**ENV, "event_id": f"t-{ms}", "payload": {
        "entry": "turn", "duration_ms": ms, "stages": {}, "degraded": degraded, "awaiting": "none"}})


def escalated(reason: str = "tool_failure") -> EngineEvent:
    return Escalated.model_validate({**ENV, "event_id": "x", "payload": {
        "reason_code": reason, "target_queue": "q", "priority": "high", "handoff_ref": "h"}})


def tool(status: str = "ok") -> EngineEvent:
    return ToolCalled.model_validate({**ENV, "event_id": "c", "payload": {
        "node_id": "n1", "tool": "radicar_pqr@1.0.0", "call_id": "c1", "status": status, "args": {},
        "latency_ms": 10}})


def emitted(llm: bool = True) -> EngineEvent:
    payload: dict[str, Any] = {"kind": "generated", "validator": {"ok": True}, "fallback_used": False}
    if llm:
        payload["llm"] = {"calls": 1, "latency_ms": 5, "tokens_in": 3, "tokens_out": 4, "cost_usd": "0.0025",
                          "cost_known": True}
    return ResponseEmitted.model_validate({**ENV, "event_id": "r", "payload": payload})


def expr(**over: Any) -> MetricExpr:
    base: dict[str, Any] = {"event": "engine.turn_completed", "aggregation": "count", "window": "scenario"}
    return MetricExpr.model_validate(base | over)


TURNS = [turn(100), turn(200, degraded=True), turn(300), turn(400)]


def test_record_projects_envelope_and_nested_fields() -> None:
    found = catalog_record(emitted())
    assert found == ("engine.response_emitted", {
        "release": "rel-1", "run_id": "run-1", "validator.ok": True, "validator.regenerations": 0,
        "llm.calls": 1, "llm.latency_ms": 5, "llm.tokens_in": 3, "llm.tokens_out": 4,
        "llm.cost_usd": Decimal("0.0025"), "kind": "generated", "fallback_used": False})
    without_llm = catalog_record(emitted(llm=False))
    assert without_llm is not None and "llm.calls" not in without_llm[1]


def test_enums_become_their_text_and_unknown_events_are_not_measurable() -> None:
    found = catalog_record(tool("timeout"))
    assert found is not None and type(found[1]["status"]) is str and found[1]["status"] == "timeout"
    dispatched = ActionDispatched.model_validate(
        {**ENV, "event_id": "d", "payload": {"action_id": "a1", "tool": "radicar_pqr@1.0.0"}})
    assert catalog_record(dispatched) is None


def test_count_sum_avg_and_discrete_percentile() -> None:
    assert evaluate_metric(expr(), TURNS) == Decimal(4)
    assert evaluate_metric(expr(aggregation="sum", field="duration_ms"), TURNS) == Decimal(1000)
    assert str(evaluate_metric(expr(aggregation="avg", field="duration_ms"), TURNS)) == "250.0000"
    for pct, value in ((1, 100), (50, 200), (95, 400)):
        p = expr(aggregation="percentile", field="duration_ms", percentile=pct)
        assert evaluate_metric(p, TURNS) == Decimal(value)


def test_empty_inputs() -> None:
    assert evaluate_metric(expr(), []) == Decimal(0)
    assert evaluate_metric(expr(aggregation="sum", field="duration_ms"), []) == Decimal(0)
    assert evaluate_metric(expr(aggregation="avg", field="duration_ms"), []) is None
    assert evaluate_metric(expr(aggregation="percentile", field="duration_ms", percentile=50), []) is None


def test_rate_is_a_rounded_ratio_and_zero_denominator_is_unmeasured() -> None:
    denominator = {"event": "engine.turn_completed", "aggregation": "count", "window": "scenario"}
    rate = expr(aggregation="rate", where=[{"field": "degraded", "op": "eq", "value": True}],
                denominator=denominator)
    assert str(evaluate_metric(rate, [turn(1, True), turn(2), turn(3)])) == "0.3333"
    assert evaluate_metric(rate, []) is None


def test_missing_fields_are_null() -> None:
    events = [emitted(llm=False), emitted(llm=True)]
    calls = expr(event="engine.response_emitted", aggregation="sum", field="llm.calls")
    assert evaluate_metric(calls, events) == Decimal(1)
    not_five = expr(event="engine.response_emitted", where=[{"field": "llm.calls", "op": "ne", "value": 5}])
    assert evaluate_metric(not_five, events) == Decimal(1)  # the one without `llm` does not count


def test_filters_are_type_aware() -> None:
    assert evaluate_metric(expr(where=[{"field": "duration_ms", "op": "ge", "value": 200}]), TURNS) == 3
    assert evaluate_metric(expr(where=[{"field": "degraded", "op": "eq", "value": True}]), TURNS) == 1
    assert evaluate_metric(expr(where=[{"field": "degraded", "op": "eq", "value": 1}]), TURNS) == 0
    among = expr(event="engine.escalated",
                 where=[{"field": "reason_code", "op": "in", "value": ["tool_failure", "low_confidence"]}])
    assert evaluate_metric(among, [escalated(), escalated("customer_request")]) == 1


def test_unobservable_metrics_are_unmeasured() -> None:  # decision D5
    judge = JudgeExpr.model_validate({"judge_profile": "perfil-juez@1.0.0", "rubric": "r",
                                      "target_event": "engine.response_emitted"})
    assert evaluate_metric(judge, TURNS) is None
    assert evaluate_metric(expr(event="registry.proposal_created"), TURNS) is None  # never 0
    assert evaluate_metric(expr(group_by=["release"]), TURNS) is None  # spec 13.10


def test_assertions() -> None:
    must = Assertion(event="engine.escalated")
    never = Assertion(event="engine.escalated", expect="none")
    assert assertion_holds(must, [escalated()]) and not assertion_holds(must, [])
    assert assertion_holds(never, []) and not assertion_holds(never, [escalated()])
    filtered = Assertion.model_validate({
        "event": "engine.escalated",
        "where": [{"field": "reason_code", "op": "eq", "value": "low_confidence"}]})
    assert not assertion_holds(filtered, [escalated()])
    blind = Assertion(event="registry.proposal_created", expect="none")
    assert not assertion_holds(blind, [])  # not observable: fails closed even if it expects "none"
