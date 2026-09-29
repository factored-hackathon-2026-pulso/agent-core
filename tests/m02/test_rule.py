from decimal import Decimal

from agent_core.domain import Decision, EntityRef, Policy
from agent_core.interpreter import Stop
from tests.m02.harness import World, fact, flow, slot

ENDS = [
    {"id": "si", "type": "end", "config": {"outcome": "resolved"}},
    {"id": "no", "type": "end", "config": {"outcome": "cancelled"}},
]


def _rule(config: dict[str, object]) -> dict[str, object]:
    return {"id": "r", "type": "rule", "config": config, "next": {"true": "si", "false": "no"}}


def _branch(out) -> str:  # type: ignore[no-untyped-def]
    return out.state.active_flow.node_id


def test_t_m2_02_claimed_slot_is_null() -> None:
    w = World()
    f = flow(_rule({"expr": {"==": [{"var": "slots.monto"}, None]}}), *ENDS)
    claimed = w.step(w.state(f, slots={"monto": slot(Decimal("900"), "claimed")}))
    assert _branch(claimed) == "si"  # claimed → null → la igualdad con null es verdadera
    validated = w.step(w.state(f, slots={"monto": slot(Decimal("900"))}))
    assert _branch(validated) == "no"


def test_t_m2_03_policy_emits_rule_evaluated_with_audit_inputs() -> None:
    w = World()
    policy = Policy.model_validate({
        "id": "umbral", "version": "1.0.0", "owner": "riesgo", "rationale": "x",
        "expr": {">": [{"var": "facts.monto_usd.value"}, 500]}})
    w.add(policy)
    f = flow(_rule({"policy": "umbral@1.0.0"}), *ENDS)
    out = w.step(w.state(f, facts={"monto_usd": fact(Decimal("620.00"), kind="compute")}))
    assert _branch(out) == "si"
    rule_events = [e for e in out.events if e.type == "rule_evaluated"]  # type: ignore[attr-defined]
    assert len(rule_events) == 1
    payload = rule_events[0].payload  # type: ignore[attr-defined]
    assert (payload.node_id, payload.policy, payload.result) == ("r", EntityRef.parse("umbral@1.0.0"), True)
    assert list(payload.inputs) == ["facts.monto_usd.value"]
    assert "620.00" not in rule_events[0].model_dump_json()


def test_inline_expr_has_no_policy_ref() -> None:
    w = World()
    f = flow(_rule({"expr": {"in": [{"var": "slots.tipo"}, ["a", "b"]]}}), *ENDS)
    out = w.step(w.state(f, slots={"tipo": slot("a")}))
    event = next(e for e in out.events if e.type == "rule_evaluated")  # type: ignore[attr-defined]
    assert event.payload.policy is None and event.payload.result is True  # type: ignore[attr-defined]


def test_rule_never_reads_decisions() -> None:
    w = World()
    decision = Decision(decision_id="decision-0001", value={"match": "unica"}, p_cal={"match": 0.9},
                        provider_used="s", model_version="1")
    f = flow(_rule({"expr": {"==": [{"var": "decisions.d.match"}, "unica"]}}), *ENDS)
    out = w.step(w.state(f, decisions={"d": decision}))
    assert _branch(out) == "no" and out.stop is Stop.terminal


def test_t_m2_12_decimal_boundary_through_a_rule() -> None:
    w = World()
    f = flow(_rule({"expr": {">": [{"var": "facts.m.value"}, 500]}}), *ENDS)
    assert _branch(w.step(w.state(f, facts={"m": fact(Decimal("500.00"))}))) == "no"
    assert _branch(w.step(w.state(f, facts={"m": fact(Decimal("500.01"))}))) == "si"
