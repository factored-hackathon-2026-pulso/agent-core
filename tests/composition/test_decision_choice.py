"""`DecisionAdapter.decide_choice`: the effective schema carries the runtime options plus "none"."""

from agent_core.composition import DecisionAdapter
from agent_core.decision import WILDCARD_LABEL, EventScope, RawPrediction
from tests.m05.helpers import RUN_ID, artifact, identity_map, make_service, model_def, ref


def _rig():  # type: ignore[no-untyped-def]
    art = artifact(thresholds={("choice", WILDCARD_LABEL, "classifier", "es"): 0.6},
                   calibrators={("choice", "classifier", "es"): identity_map()})
    return make_service(model_def(calibrated=("choice",)), artifacts=[art])


def _adapter(rig) -> DecisionAdapter:  # type: ignore[no-untyped-def]
    scope = EventScope(run_id=RUN_ID, release="release-1", turn_id="turn-0001", session_id="session-0001")
    return DecisionAdapter(rig.service, rig.vault, scope)


def test_decide_choice_builds_the_schema_with_none_appended() -> None:
    rig = _rig()
    rig.providers["classifier"].push(RawPrediction(value={"choice": "disputas"}, p_raw={"choice": 0.9}))
    _adapter(rig).decide_choice(ref(model_def(calibrated=("choice",))), {"text": "x"},
                                ["disputas", "saldos"], "es")
    assert rig.providers["classifier"].schemas == [{
        "type": "object", "additionalProperties": False, "required": ["choice"],
        "properties": {"choice": {"type": "string", "enum": ["disputas", "saldos", "none"]}}}]


def test_decide_choice_returns_the_decision_with_the_wildcard_threshold() -> None:
    rig = _rig()
    rig.providers["classifier"].push(RawPrediction(value={"choice": "disputas"}, p_raw={"choice": 0.9},
                                                    tokens=10))
    result = _adapter(rig).decide_choice(ref(model_def(calibrated=("choice",))), {"text": "x"},
                                         ["disputas", "saldos"], "es")
    assert result.decision.value == {"choice": "disputas"}
    assert result.above_threshold == {"choice": True}
    assert result.tokens == 10 and result.model_calls == 1
    (event,) = result.events
    assert event.type == "decision_made" and event.payload.decision_id == result.decision.decision_id


def test_decide_choice_none_is_a_valid_output() -> None:
    rig = _rig()
    rig.providers["classifier"].push(RawPrediction(value={"choice": "none"}, p_raw={"choice": 0.9}))
    result = _adapter(rig).decide_choice(ref(model_def(calibrated=("choice",))), {"text": "x"},
                                         ["disputas"], "es")
    assert result.decision.value == {"choice": "none"}


def test_an_option_outside_the_list_is_rejected_as_out_of_schema() -> None:
    rig = _rig()
    bad = RawPrediction(value={"choice": "invented"}, p_raw={"choice": 0.99})
    rig.providers["classifier"].push(bad, bad)
    result = _adapter(rig).decide_choice(ref(model_def(calibrated=("choice",))), {"text": "x"},
                                         ["disputas"], "es")
    assert result.above_threshold == {"choice": False} and result.decision.value == {}
