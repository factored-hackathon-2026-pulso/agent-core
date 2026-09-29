from decimal import Decimal

import pytest

from agent_core.decision.types import DecisionOutput, RawPrediction
from agent_core.domain import Decision


def _output(**over: object) -> DecisionOutput:
    base = dict(value={"command": "affirm"}, p_cal={"command": 0.9}, p_raw={"command": 0.8}, top_k={},
                above_threshold={"command": True}, provider_used="classifier", model_version="clf-1",
                fallback_depth=0, latency_ms=3, tokens=0, cost_usd=Decimal("0"), decision_id="decision-0001",
                model_calls=1)
    return DecisionOutput(**{**base, **over})  # type: ignore[arg-type]


def test_as_decision_drops_above_threshold_and_keeps_provenance() -> None:
    decision = _output().as_decision()
    assert isinstance(decision, Decision)
    assert decision.decision_id == "decision-0001" and decision.provider_used == "classifier"
    assert decision.model_version == "clf-1"
    assert decision.p_cal == {"command": pytest.approx(0.9)}


def test_raw_prediction_defaults_are_neutral() -> None:
    raw = RawPrediction(value={}, p_raw={})
    assert raw.tokens == 0 and raw.cost_usd == Decimal("0") and raw.top_k == {}
    assert raw.model_version == "unknown"


def test_cost_is_decimal_not_float() -> None:
    assert isinstance(_output().cost_usd, Decimal)


def test_repr_does_not_dump_the_value() -> None:
    text = repr(_output(value={"slots": {"nombre": "SECRETO-XYZ"}}))
    assert "SECRETO-XYZ" not in text and "decision-0001" in text


def test_output_is_frozen() -> None:
    with pytest.raises(AttributeError):
        _output().tokens = 5  # type: ignore[misc]
