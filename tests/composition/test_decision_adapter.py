"""Adaptador de M5 hacia el `DecisionPort` de M2 (m05 §2): `DecisionOutput` → `DecisionResult`."""

from decimal import Decimal

from agent_core.composition import DecisionAdapter
from agent_core.decision import EventScope, RawPrediction
from agent_core.interpreter import DecisionPort, DecisionResult
from tests.m05.helpers import RUN_ID, artifact, identity_map, make_service, model_def, ref


def _rig():  # type: ignore[no-untyped-def]
    art = artifact(thresholds={("command", "affirm", "classifier", "es"): 0.6},
                   calibrators={("command", "classifier", "es"): identity_map()})
    return make_service(model_def(calibrated=("command",)), artifacts=[art])


def _adapter(rig, turn_id: str | None = "turn-0001") -> DecisionPort:  # type: ignore[no-untyped-def]
    scope = EventScope(run_id=RUN_ID, release="release-1", turn_id=turn_id, session_id="session-0001")
    return DecisionAdapter(rig.service, rig.vault, scope)


def test_la_decision_guardable_y_el_uso_salen_de_la_salida_de_m5() -> None:
    rig = _rig()
    rig.providers["classifier"].push(RawPrediction(
        value={"command": "affirm"}, p_raw={"command": 0.9}, tokens=30, cost_usd=Decimal("0.001")))
    result = _adapter(rig).decide(ref(model_def(calibrated=("command",))), {"text": "sí"}, "es")
    assert isinstance(result, DecisionResult)
    assert result.decision.value == {"command": "affirm"}
    assert result.decision.decision_id == result.events[0].payload.decision_id
    assert result.decision.p_cal == {"command": 0.9} and result.above_threshold == {"command": True}
    assert (result.model_calls, result.tokens, result.cost_usd) == (1, 30, Decimal("0.001"))


def test_emite_decision_made_con_el_alcance_del_run() -> None:
    rig = _rig()
    rig.providers["classifier"].push(RawPrediction(value={"command": "affirm"}, p_raw={"command": 0.9}))
    result = _adapter(rig).decide(ref(model_def(calibrated=("command",))), {"text": "sí"}, "es")
    (event,) = result.events
    assert event.type == "decision_made" and event.run_id == RUN_ID
    assert event.release == "release-1" and event.session_id == "session-0001"
