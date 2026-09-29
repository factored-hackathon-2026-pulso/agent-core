from decimal import Decimal

import pytest

from agent_core.decision.providers.jev import JevProvider, JevTransport
from agent_core.decision.types import DecisionConfigError, ProviderError, ProviderTimeout
from agent_core.domain import JsonValue, ProviderSpec
from testing.capture import RequestCapture

SPEC = ProviderSpec(provider="jev", config={"model": "jev-es-1", "timeout_ms": 800})
INPUT: dict[str, JsonValue] = {"text": "sí, adelante", "recent_turns": ["hola"]}
SCHEMA: dict[str, JsonValue] = {"type": "object", "additionalProperties": True}


class FakeTransport:
    def __init__(self, response: dict[str, JsonValue] | Exception) -> None:
        self.response = response
        self.sent: list[tuple[dict[str, JsonValue], int]] = []

    def send(self, request: dict[str, JsonValue], timeout_ms: int) -> dict[str, JsonValue]:
        self.sent.append((request, timeout_ms))
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


GOOD: dict[str, JsonValue] = {
    "value": {"command": "affirm"},
    "probabilities": {"command": Decimal("0.93")},
    "top_k": {"command": [["affirm", Decimal("0.93")], ["deny", Decimal("0.05")]]},
    "latency_ms": 42,
}


def test_maps_a_good_response() -> None:
    transport = FakeTransport(GOOD)
    raw = JevProvider(transport).predict(SPEC, INPUT, SCHEMA, "es")
    assert raw.value == {"command": "affirm"}
    assert raw.p_raw == {"command": pytest.approx(0.93)}
    assert raw.top_k == {"command": [("affirm", pytest.approx(0.93)), ("deny", pytest.approx(0.05))]}
    assert raw.latency_ms == 42 and raw.model_version == "jev:jev-es-1"
    assert raw.cost_usd == Decimal("0") and raw.tokens == 0
    assert all(isinstance(p, float) for p in raw.p_raw.values() if p is not None)


def test_request_shape_and_timeout_are_passed_to_the_transport() -> None:
    transport = FakeTransport(GOOD)
    JevProvider(transport).predict(SPEC, INPUT, SCHEMA, "pt")
    request, timeout_ms = transport.sent[0]
    assert request == {"model": "jev-es-1", "input": INPUT, "schema": SCHEMA, "locale": "pt"}
    assert timeout_ms == 800


def test_capture_records_the_request_before_sending() -> None:
    capture = RequestCapture()
    transport = FakeTransport(TimeoutError())
    with pytest.raises(ProviderTimeout):
        JevProvider(transport, capture).predict(SPEC, INPUT, SCHEMA, "es")
    assert len(capture.requests) == 1 and "sí, adelante" in capture.requests[0]


def test_transport_timeout_becomes_provider_timeout() -> None:
    with pytest.raises(ProviderTimeout):
        JevProvider(FakeTransport(TimeoutError("lento"))).predict(SPEC, INPUT, SCHEMA, "es")


def test_arbitrary_transport_error_becomes_provider_error_without_leaking_the_request() -> None:
    boom = RuntimeError("fallo con SECRETO-XYZ y Bearer sk-sintetica")
    with pytest.raises(ProviderError) as info:
        JevProvider(FakeTransport(boom)).predict(SPEC, INPUT, SCHEMA, "es")
    text = str(info.value)
    assert "SECRETO-XYZ" not in text and "sk-sintetica" not in text and "sí, adelante" not in text
    assert info.value.__cause__ is None and info.value.__suppress_context__


def test_missing_probabilities_means_p_raw_none_per_field() -> None:
    response: dict[str, JsonValue] = {"value": {"command": "affirm"}}
    raw = JevProvider(FakeTransport(response)).predict(SPEC, INPUT, SCHEMA, "es")
    assert raw.p_raw == {"command": None} and raw.top_k == {} and raw.latency_ms == 0


def test_partial_probabilities_only_null_the_missing_field() -> None:
    response: dict[str, JsonValue] = {"value": {"command": "affirm", "flow": "f"},
                                      "probabilities": {"command": 0.5}}
    raw = JevProvider(FakeTransport(response)).predict(SPEC, INPUT, SCHEMA, "es")
    assert raw.p_raw == {"command": pytest.approx(0.5), "flow": None}


@pytest.mark.parametrize("response", [
    {},                                                        # sin value
    {"value": "affirm"},                                       # value no es objeto
    {"value": {"command": "a"}, "probabilities": [1]},         # probabilities mal formado
    {"value": {"command": "a"}, "probabilities": {"command": 1.5}},
    {"value": {"command": "a"}, "probabilities": {"command": True}},
    {"value": {"command": "a"}, "probabilities": {"command": "0.5"}},
    {"value": {"command": "a"}, "top_k": {"command": [["a"]]}},
    {"value": {"command": "a"}, "latency_ms": -1},
])
def test_malformed_response_is_provider_error(response: dict[str, JsonValue]) -> None:
    with pytest.raises(ProviderError):
        JevProvider(FakeTransport(response)).predict(SPEC, INPUT, SCHEMA, "es")


@pytest.mark.parametrize("config", [
    {"model": "m"},                                  # sin timeout_ms
    {"model": "m", "timeout_ms": 0},
    {"model": "m", "timeout_ms": -5},
    {"model": "m", "timeout_ms": True},
    {"model": "m", "timeout_ms": "800"},
    {"timeout_ms": 800},                             # sin model
    {"model": 3, "timeout_ms": 800},
])
def test_bad_config_is_a_config_error(config: dict[str, JsonValue]) -> None:
    transport = FakeTransport(GOOD)
    with pytest.raises(DecisionConfigError):
        JevProvider(transport).predict(ProviderSpec(provider="jev", config=config), INPUT, SCHEMA, "es")
    assert transport.sent == []


def test_repr_hides_transport_internals() -> None:
    assert "SECRETO" not in repr(JevProvider(FakeTransport(GOOD)))


def test_name_and_protocol() -> None:
    assert JevProvider(FakeTransport(GOOD)).name == "jev"
    transport: JevTransport = FakeTransport(GOOD)
    assert transport is not None
