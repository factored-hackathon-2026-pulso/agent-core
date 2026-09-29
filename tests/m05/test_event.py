"""T-M5-08: `decision_made` trae todos los campos."""

from decimal import Decimal

from agent_core.decision.types import DecisionOutput, RawPrediction
from agent_core.domain import (
    EVENT_EMITTERS,
    MEASURED_FIELDS,
    DecisionMade,
    DecisionModelDef,
    EntityRef,
    LabelScore,
    dumps,
)
from testing.capture import RequestCapture
from testing.fakes.provider import Reply, Timeout
from tests.m05.helpers import Rig, artifact, identity_map, make_service, model_def, ref, scope

DOC = "1234567890"


def _rig_with_fallback() -> tuple[DecisionModelDef, Rig]:
    definition = model_def(providers=("jev", "classifier"))
    art = artifact(thresholds={("command", "affirm", "classifier", "es"): 0.5},
                   calibrators={("command", "classifier", "es"): identity_map()})
    rig = make_service(definition, artifacts=[art])
    rig.providers["jev"].push(Timeout(after_ms=100))
    token = rig.vault.tokenize(DOC, "document_number", "doc")
    rig.providers["classifier"].push(Reply(RawPrediction(
        value={"command": "affirm", "slots": {"documento": token}}, p_raw={"command": 0.8},
        top_k={"command": [("affirm", 0.8), ("deny", 0.1)]}, tokens=7, cost_usd=Decimal("0.003"),
        model_version="clf-1"), latency_ms=25))
    return definition, rig


def _decide(rig: Rig, definition: DecisionModelDef) -> tuple[DecisionOutput, DecisionMade]:
    return rig.service.decide(ref(definition), {"text": "sí"}, "es", rig.vault, scope=scope())


def test_t_m5_08_event_has_all_fields() -> None:
    definition, rig = _rig_with_fallback()
    output, event = _decide(rig, definition)
    assert isinstance(event, DecisionMade) and event.type == "decision_made"
    payload = event.payload
    dumped = payload.model_dump()
    assert set(dumped) == {
        "decision_id", "model", "provider_used", "model_version", "fallback_depth", "value", "p_cal", "p_raw",
        "top_k", "above_threshold", "latency_ms", "tokens", "cost_usd", "locale"}
    assert [k for k, v in dumped.items() if v is None] == []
    assert payload.decision_id == output.decision_id == "decision-0001"
    assert payload.model == EntityRef(id=definition.id, version=definition.version)
    assert payload.provider_used == "classifier" and payload.model_version == "clf-1"
    assert payload.fallback_depth == 1
    assert payload.p_raw["command"] == 0.8 and payload.p_cal["command"] == 0.8
    assert payload.above_threshold == {"command": True}
    assert payload.top_k == {"command": [LabelScore(label="affirm", p=0.8),
                                         LabelScore(label="deny", p=0.1)]}
    assert payload.latency_ms == 125 and payload.tokens == 7 and payload.locale == "es"
    assert payload.cost_usd == Decimal("0.003") and isinstance(payload.cost_usd, Decimal)


def test_envelope_comes_from_scope_ids_and_clock() -> None:
    definition, rig = _rig_with_fallback()
    _, event = _decide(rig, definition)
    assert (event.run_id, event.release, event.turn_id, event.session_id) == (
        "run-1", "release-1", "turn-0001", None)
    assert event.event_id == "event-0001" and event.ts == rig.clock.now()
    assert event.seq is None and event.hash is None  # los asigna M11


def test_round_trip_and_emitter() -> None:
    definition, rig = _rig_with_fallback()
    _, event = _decide(rig, definition)
    assert DecisionMade.model_validate(event.model_dump()) == event
    assert EVENT_EMITTERS["decision_made"] == {"M5"}
    assert MEASURED_FIELDS["decision_made"] == {"latency_ms"}


def test_event_never_carries_clear_values() -> None:
    definition, rig = _rig_with_fallback()
    _, event = _decide(rig, definition)
    capture = RequestCapture()
    capture.record(dumps(event))
    assert capture.leaks([DOC]) == [] and "⟦doc:1⟧" in capture.requests[0]


def test_exhausted_chain_event_is_complete() -> None:
    rig = make_service()
    rig.providers["classifier"].push(Timeout())
    _, event = rig.service.decide(ref(), {"text": "sí"}, "es", rig.vault, scope=scope())
    payload = event.payload
    assert payload.provider_used == "none" and payload.value == {}
    assert payload.p_cal == {"command": None} and payload.above_threshold == {"command": False}
