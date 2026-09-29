"""T-M5-10 (nivel proveedor): la vista `model` que llega a jev y llm_structured no lleva pii_direct.

La variante de punta a punta a través de `DecisionService.decide` queda para cuando exista `decide`."""

from decimal import Decimal

import pytest

from agent_core.decision.providers.jev import JevProvider
from agent_core.decision.providers.llm_structured import LlmStructuredProvider
from agent_core.decision.types import DecisionProvider
from agent_core.domain import EntityRef, JsonValue, Locale, ProviderSpec
from agent_core.ports import GenerationResult
from testing.capture import RequestCapture
from testing.fakes.keys import FakeKeyProvider
from tests.m07.helpers import make_service, make_vault

NAME = "Anabela Sintetica"
DOC = "1098765432"
CLEAR = [NAME, DOC]
SCHEMA: dict[str, JsonValue] = {"type": "object", "additionalProperties": True}


class CapturingTransport:
    def send(self, request: dict[str, JsonValue], timeout_ms: int) -> dict[str, JsonValue]:
        return {"value": {"command": "affirm"}, "probabilities": {"command": 0.9}}


class CapturingGateway:
    def __init__(self, capture: RequestCapture) -> None:
        self.capture = capture

    def generate(self, prompt: EntityRef, inputs_model_view: dict[str, JsonValue], locale: Locale,
                 schema: dict[str, JsonValue] | None = None) -> GenerationResult:
        self.capture.record({"prompt": str(prompt), "inputs": inputs_model_view, "schema": schema})
        return GenerationResult(output={"command": "affirm"}, tokens_in=1, tokens_out=1,
                                cost_usd=Decimal("0"), model="llm-sint-1")


def _providers(capture: RequestCapture) -> dict[str, tuple[DecisionProvider, ProviderSpec]]:
    return {
        "jev": (JevProvider(CapturingTransport(), capture),
                ProviderSpec(provider="jev", config={"model": "jev-1", "timeout_ms": 500})),
        "llm_structured": (LlmStructuredProvider(CapturingGateway(capture)),
                           ProviderSpec(provider="llm_structured", config={"prompt": "p@1.0.0"})),
    }


def _views() -> tuple[JsonValue, JsonValue]:
    """`(model, full)` de un cliente sintético con `pii_direct` y un reclamo con el documento en el texto."""
    service, vault = make_service(keys=FakeKeyProvider.default()), make_vault(keys=FakeKeyProvider.default())
    row = {"first_name": NAME, "document_number": DOC, "amount": Decimal("10.50"), "currency": "COP"}
    projected = service.project([row], "transactions", [], vault)
    return projected.model, projected.full


@pytest.mark.parametrize("provider_name", ["jev", "llm_structured"])
def test_t_m5_10_model_view_reaches_provider_without_pii_direct(provider_name: str) -> None:
    capture = RequestCapture()
    provider, spec = _providers(capture)[provider_name]
    model_view, _ = _views()
    provider.predict(spec, {"text": "sí", "data": model_view}, SCHEMA, "es")
    assert len(capture.requests) == 1
    assert capture.leaks(CLEAR) == []
    assert "⟦" in capture.requests[0]  # los identificadores viajan como tokens


@pytest.mark.parametrize("provider_name", ["jev", "llm_structured"])
def test_counter_test_full_view_is_detected_as_a_leak(provider_name: str) -> None:
    capture = RequestCapture()
    provider, spec = _providers(capture)[provider_name]
    _, full_view = _views()
    provider.predict(spec, {"text": "sí", "data": full_view}, SCHEMA, "es")
    assert capture.leaks(CLEAR) != []
