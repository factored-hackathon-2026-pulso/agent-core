from decimal import Decimal

from agent_core.domain import Budgets, DecisionModelDef
from agent_core.interpreter import Resume, Stop, advance
from testing.fakes.decision import make_decision
from tests.m02.harness import AGENT, World, fact, flow, slot

NEXT = {"unica": "si", "ninguna": "no", "low_confidence": "poco"}
TAIL = [
    {"id": "si", "type": "end", "config": {"outcome": "resolved"}},
    {"id": "no", "type": "end", "config": {"outcome": "cancelled"}},
    {"id": "poco", "type": "escalate", "config": {"reason_code": "low_confidence"}},
]
MODEL = DecisionModelDef.model_validate({
    "id": "match", "version": "2.0.0", "output_schema": {"type": "object"}, "calibrated_fields": ["match"],
    "input_view": ["slots.q"], "providers": [{"provider": "classifier"}], "calibration": {"method": "none"}})


def _decide(**config: object) -> dict[str, object]:
    return {"id": "d", "type": "decide", "next": NEXT,
            "config": {"model": "match@2.0.0", "branch_on": "match", "save_as": "coincide", **config}}


def _world() -> World:
    w = World()
    w.add(MODEL)
    return w


def _node(out) -> str:  # type: ignore[no-untyped-def]
    return out.state.active_flow.node_id


def test_above_threshold_branches_on_value_and_stores_decision() -> None:
    w = _world()
    w.decisions.push(make_decision({"match": "unica", "transaction": "tx-1"}, {"match": True}, tokens=40,
                                   cost="0.002"))
    state = w.state(flow(_decide(), *TAIL), slots={"q": slot("cargo raro")})
    out = w.step(state)
    assert _node(out) == "si"
    assert out.state.decisions["coincide"].value == {"match": "unica", "transaction": "tx-1"}
    used = out.state.budgets_used
    assert (used.turn_model_calls, used.run_tokens, used.run_cost) == (1, 40, Decimal("0.002"))
    (model_ref, inputs, locale) = w.decisions.calls[0]
    assert str(model_ref) == "match@2.0.0" and locale == "es"
    assert list(inputs) == ["slots.q"]


def test_inputs_are_model_view_with_wrapped_slots() -> None:
    w = _world()
    w.decisions.push(make_decision({"match": "unica"}, {"match": True}))
    w.step(w.state(flow(_decide(), *TAIL), slots={"q": slot("cargo raro")}))
    text = w.decisions.calls[0][1]["slots.q"]
    assert isinstance(text, str) and text.startswith("<datos_no_confiables") and "cargo raro" in text


def test_node_input_view_overrides_the_model_default() -> None:
    w = _world()
    w.decisions.push(make_decision({"match": "unica"}, {"match": True}))
    node = _decide(input_view=["facts.cand.value.amount"])
    out = w.step(w.state(flow(node, *TAIL), facts={"cand": fact({"amount": Decimal("3")})}))
    assert _node(out) == "si" and w.decisions.calls[0][1] == {"facts.cand.value.amount": Decimal("3")}


def test_below_threshold_or_missing_input_is_low_confidence() -> None:
    w = _world()
    w.decisions.push(make_decision({"match": "unica"}, {"match": False}))
    assert _node(w.step(w.state(flow(_decide(), *TAIL), slots={"q": slot("x")}))) == "poco"
    missing = _world()
    out = missing.step(missing.state(flow(_decide(), *TAIL), slots={"q": slot("x", "claimed")}))
    assert _node(out) == "poco" and missing.decisions.calls == []  # claimed = ausente (D7), no se llama


def test_uncalibrated_branch_field_branches_on_its_value() -> None:
    w = World()
    w.add(MODEL.model_copy(update={"calibrated_fields": []}))
    w.decisions.push(make_decision({"match": "ninguna"}, {}))
    out = w.step(w.state(flow(_decide(), *TAIL), slots={"q": slot("x")}))
    assert _node(out) == "no"


def test_model_call_budget_escalates_before_calling() -> None:
    w = World(agent=AGENT.model_copy(update={"budgets": Budgets.model_validate(
        {**AGENT.budgets.model_dump(), "max_model_calls_per_turn": 1})}))
    w.add(MODEL)
    state = w.state(flow(_decide(), *TAIL), slots={"q": slot("x")},
                    budgets_used={"turn_model_calls": 1, "turn_started_at": w.clock.now()})
    out = advance(state, w.ctx(), Resume())  # sin `w.step`: `begin_turn` reiniciaría turn_model_calls
    assert out.stop is Stop.terminal and out.escalation is not None
    assert out.escalation.reason_code == "budget_exceeded" and w.decisions.calls == []
