"""Contrato de `LLMGateway` (M0 §2.9) contra `ScriptedGateway`, `OpenAICompatGateway` y `HttpLLMGateway`.

Incluye además sanidad negativa y pruebas propias del doble.
"""

from decimal import Decimal

import pytest
from respx import MockRouter

from agent_core.domain import EntityRef, GatewayError, GatewayErrorKind
from agent_core.ports import GenerationResult, LLMGateway
from testing.capture import RequestCapture
from testing.fakes.gateway import ScriptedGateway, gen
from tests.gateway_http.helpers import GENERATE, failure, make_gateway, success
from tests.u05.helpers import CHAT, completion, make_world

PROMPT = EntityRef.parse("resumen@1.0.0")
INPUTS = {"cliente": {"nombre": "⟦name:1⟧"}, "monto": "1.234,56"}


def check_returns_generation_result(gateway: LLMGateway) -> None:
    out = gateway.generate(PROMPT, INPUTS, "es", {"type": "object"})
    assert isinstance(out, GenerationResult)
    assert out.tokens_in >= 0 and out.tokens_out >= 0
    assert isinstance(out.cost_usd, Decimal) and out.cost_usd >= 0
    assert out.model


def check_failure_is_gateway_error(gateway: LLMGateway) -> None:
    with pytest.raises(GatewayError) as caught:
        gateway.generate(PROMPT, INPUTS, "es")
    assert isinstance(caught.value.kind, GatewayErrorKind)


def check_does_not_mutate_inputs(gateway: LLMGateway) -> None:
    before = {"cliente": {"nombre": "⟦name:1⟧"}, "monto": "1.234,56"}
    gateway.generate(PROMPT, before, "es")
    assert before == {"cliente": {"nombre": "⟦name:1⟧"}, "monto": "1.234,56"}


@pytest.fixture(params=["scripted", "openai", "http"])
def ok_gateway(request: pytest.FixtureRequest, respx_mock: MockRouter) -> LLMGateway:
    if request.param == "scripted":
        return ScriptedGateway([gen("hola", []), gen("hola", []), gen("hola", [])])
    if request.param == "http":
        respx_mock.post(GENERATE).respond(200, text=success({"ok": True}))
        return make_gateway()
    respx_mock.post(CHAT).respond(200, json=completion('{"ok": true}'))
    return make_world().gateway


@pytest.fixture(params=["scripted", "openai", "http"])
def failing_gateway(request: pytest.FixtureRequest, respx_mock: MockRouter) -> LLMGateway:
    if request.param == "scripted":
        return ScriptedGateway([GatewayError(GatewayErrorKind.unavailable, model="scripted-1")])
    if request.param == "http":
        respx_mock.post(GENERATE).respond(502, json=failure("unavailable"))
        return make_gateway()
    respx_mock.post(CHAT).respond(503, json={"error": {"message": "x"}})
    return make_world().gateway


def test_returns_generation_result(ok_gateway: LLMGateway) -> None:
    check_returns_generation_result(ok_gateway)


def test_failure_is_gateway_error(failing_gateway: LLMGateway) -> None:
    check_failure_is_gateway_error(failing_gateway)


def test_does_not_mutate_inputs(ok_gateway: LLMGateway) -> None:
    check_does_not_mutate_inputs(ok_gateway)


class _FloatCostGateway:
    def generate(self, prompt: EntityRef, inputs_model_view: dict[str, object], locale: str,
                 schema: dict[str, object] | None = None) -> GenerationResult:
        return GenerationResult.model_construct(
            output=None, tokens_in=1, tokens_out=1, cost_usd=0.1, model="m")


def test_contract_detects_non_decimal_cost() -> None:
    with pytest.raises(AssertionError):
        check_returns_generation_result(_FloatCostGateway())  # type: ignore[arg-type]


def test_scripted_records_calls_as_copies_and_in_order() -> None:
    gateway = ScriptedGateway([gen("uno", []), gen("dos", [])])
    inputs: dict[str, object] = {"a": {"b": 1}}
    gateway.generate(PROMPT, inputs, "es")  # type: ignore[arg-type]
    inputs["a"] = "mutado"
    gateway.generate(PROMPT, {"z": 2}, "pt")
    assert [c.locale for c in gateway.calls] == ["es", "pt"]
    assert gateway.calls[0].inputs == {"a": {"b": 1}}


def test_scripted_without_script_fails_loudly() -> None:
    with pytest.raises(AssertionError, match="sin resultado guionado"):
        ScriptedGateway().generate(PROMPT, {}, "es")


def test_scripted_feeds_request_capture() -> None:
    capture = RequestCapture()
    gateway = ScriptedGateway([gen("ok", [])], capture=capture)
    gateway.generate(PROMPT, {"doc": "⟦doc:1⟧"}, "es")
    assert capture.leaks(["1023456789"]) == [] and len(capture.requests) == 1


def test_gen_output_is_the_draft_shape() -> None:
    assert gen("t", ["f1"]).output == {"text": "t", "citations": ["f1"]}
