"""`decide` with `choices_from` (ADR 0021, P3)."""

from agent_core.domain import DecisionModelDef
from testing.fakes.decision import make_decision
from tests.m02.harness import World, fact, flow, slot

ROUTER = DecisionModelDef.model_validate({
    "id": "router", "version": "1.0.0", "output_schema": {"type": "object"}, "calibrated_fields": ["choice"],
    "input_view": [], "providers": [{"provider": "llm_structured"}], "calibration": {"method": "none"}})
TAIL = [{"id": "ok", "type": "end", "config": {"outcome": "resolved"}},
        {"id": "no", "type": "end", "config": {"outcome": "cancelled"}},
        {"id": "low", "type": "escalate", "config": {"reason_code": "policy:low_confidence"}}]
DECIDE = {"id": "route", "type": "decide",
          "config": {"model": "router@1.0.0", "branch_on": "choice", "save_as": "route",
                     "choices_from": "facts.directory.value.choices", "input_view": ["slots.problem"]},
          "next": {"chosen": "ok", "none": "no", "low_confidence": "low"}}


def _run(choices: list[str], *pushed: object) -> tuple[World, str]:
    w = World()
    w.add(ROUTER)
    w.decisions.push(*pushed)  # type: ignore[arg-type]
    state = w.state(flow(DECIDE, *TAIL), slots={"problem": slot("no reconozco un cargo")},
                    facts={"directory": fact({"choices": choices})})
    return w, w.step(state).state.active_flow.node_id  # type: ignore[union-attr]


def test_chosen_above_threshold() -> None:
    w, node = _run(["disputas", "saldos"], make_decision({"choice": "disputas"}, {"choice": True}))
    assert node == "ok"
    assert w.decisions.choice_calls[0][2] == ["disputas", "saldos"]


def test_none_and_low_confidence() -> None:
    assert _run(["disputas"], make_decision({"choice": "none"}, {"choice": True}))[1] == "no"
    assert _run(["disputas"], make_decision({"choice": "disputas"}, {"choice": False}))[1] == "low"


def test_empty_directory_is_none_without_calling_the_model() -> None:
    w, node = _run([])
    assert node == "no" and w.decisions.choice_calls == []


def test_a_choice_outside_the_list_is_low_confidence() -> None:
    assert _run(["disputas"], make_decision({"choice": "inventado"}, {"choice": True}))[1] == "low"


def test_duplicate_choices_are_deduplicated_preserving_order() -> None:
    w, node = _run(["saldos", "disputas", "saldos"], make_decision({"choice": "disputas"}, {"choice": True}))
    assert node == "ok"
    assert w.decisions.choice_calls[0][2] == ["saldos", "disputas"]


def test_a_real_choice_named_none_is_low_confidence_without_calling_the_model() -> None:
    w, node = _run(["disputas", "none"])
    assert node == "low" and w.decisions.choice_calls == []
