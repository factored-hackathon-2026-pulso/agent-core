"""`OpenAICompatGateway.generate`: request, salida y costo (spec §3.1; T-U5-02, T-U5-03, T-U5-10)."""

import json
from decimal import Decimal

import pytest
from respx import MockRouter

from agent_core.adapters.llm.gateway import SCHEMA_INSTRUCTION
from agent_core.domain import (
    EntityRef,
    GatewayError,
    GatewayErrorKind,
    Prompt,
    SchemaError,
    StructuredMode,
    canonical_bytes,
)
from tests.u05.helpers import CHAT, DRAFT, GOOD_JSON, INPUTS, PROMPT, PROMPT_ES, completion, make_world


def _body(router: MockRouter) -> dict:  # type: ignore[type-arg]
    return json.loads(router.calls.last.request.content)


def test_request_carries_profile_params_system_and_canonical_user(respx_mock: MockRouter) -> None:  # T-U5-02
    route = respx_mock.post(CHAT).respond(200, json=completion("hola"))
    make_world().gateway.generate(PROMPT, INPUTS, "es")
    body = _body(route)
    assert body["model"] == "vendor/modelo-x" and body["max_tokens"] == 300 and body["temperature"] == 0.2
    system, user = body["messages"]
    assert system == {"role": "system", "content": PROMPT_ES}
    assert user == {"role": "user", "content": canonical_bytes(INPUTS).decode()}


def test_the_locale_picks_the_prompt_text(respx_mock: MockRouter) -> None:
    route = respx_mock.post(CHAT).respond(200, json=completion("olá"))
    make_world().gateway.generate(PROMPT, INPUTS, "pt")
    assert _body(route)["messages"][0]["content"] == "Resuma a disputa."


def test_native_sends_a_strict_json_schema_named_output(respx_mock: MockRouter) -> None:  # T-U5-03
    route = respx_mock.post(CHAT).respond(200, json=completion(GOOD_JSON))
    make_world(structured=StructuredMode.native).gateway.generate(PROMPT, INPUTS, "es", DRAFT)
    body = _body(route)
    assert body["response_format"] == {
        "type": "json_schema", "json_schema": {"name": "output", "schema": DRAFT, "strict": True}}
    assert body["messages"][0]["content"] == PROMPT_ES


def test_prompted_sends_no_response_format_and_appends_the_schema_instruction(  # T-U5-03
        respx_mock: MockRouter) -> None:
    route = respx_mock.post(CHAT).respond(200, json=completion(GOOD_JSON))
    make_world().gateway.generate(PROMPT, INPUTS, "es", DRAFT)
    body = _body(route)
    assert "response_format" not in body
    assert body["messages"][0]["content"] == PROMPT_ES + SCHEMA_INSTRUCTION + canonical_bytes(DRAFT).decode()


def test_success_returns_output_tokens_decimal_cost_and_the_reported_model(respx_mock: MockRouter) -> None:
    respx_mock.post(CHAT).respond(200, json=completion(GOOD_JSON, usage=(120, 30)))
    result = make_world().gateway.generate(PROMPT, INPUTS, "es", DRAFT)
    assert result.output == {"text": "hola", "citations": []}
    assert (result.tokens_in, result.tokens_out) == (120, 30)
    # (360 + 450) / 1e6
    assert result.cost_usd == Decimal("0.000810") and isinstance(result.cost_usd, Decimal)
    assert result.model == "vendor/modelo-x-2026"


def test_without_schema_the_output_is_the_text(respx_mock: MockRouter) -> None:
    respx_mock.post(CHAT).respond(200, json=completion("Tu disputa quedó radicada."))
    assert make_world().gateway.generate(PROMPT, INPUTS, "es").output == "Tu disputa quedó radicada."


def test_null_content_without_schema_is_the_empty_text(respx_mock: MockRouter) -> None:
    respx_mock.post(CHAT).respond(200, json=completion(None))
    assert make_world().gateway.generate(PROMPT, INPUTS, "es").output == ""


def test_decimals_in_structured_output_arrive_as_decimal(respx_mock: MockRouter) -> None:  # T-U5-10
    respx_mock.post(CHAT).respond(200, json=completion('{"monto": 12.50}'))
    schema = {"type": "object", "properties": {"monto": {"type": "number"}}}
    output = make_world().gateway.generate(PROMPT, INPUTS, "es", schema).output
    assert isinstance(output, dict) and output["monto"] == Decimal("12.50")
    assert isinstance(output["monto"], Decimal)


def test_a_single_code_block_around_the_json_is_accepted(respx_mock: MockRouter) -> None:
    respx_mock.post(CHAT).respond(200, json=completion("```json\n" + GOOD_JSON + "\n```"))
    output = make_world().gateway.generate(PROMPT, INPUTS, "es", DRAFT).output
    assert output == {"text": "hola", "citations": []}


def test_text_around_the_json_is_invalid_output_even_with_a_code_block(respx_mock: MockRouter) -> None:
    respx_mock.post(CHAT).respond(200, json=completion("Aquí va:\n```json\n" + GOOD_JSON + "\n```"))
    with pytest.raises(GatewayError) as caught:
        make_world().gateway.generate(PROMPT, INPUTS, "es", DRAFT)
    assert caught.value.kind is GatewayErrorKind.invalid_output


def test_a_success_without_usage_reports_zero_tokens_and_cost(respx_mock: MockRouter) -> None:
    respx_mock.post(CHAT).respond(200, json=completion("hola", usage=None))
    result = make_world().gateway.generate(PROMPT, INPUTS, "es")
    assert (result.tokens_in, result.tokens_out, result.cost_usd) == (0, 0, Decimal("0"))


def test_a_missing_locale_is_a_programming_error(respx_mock: MockRouter) -> None:
    w = make_world()
    w.registry.add(Prompt.model_validate(  # un prompt sin `pt` (M1 lo impide con G0-12; aquí se salta)
        {"id": "solo-es", "version": "1.0.0", "locales": {"es": "hola"}, "model_profile": "perfil@1.0.0"}))
    with pytest.raises(SchemaError):
        w.gateway.generate(EntityRef.parse("solo-es@1.0.0"), INPUTS, "pt")
    assert respx_mock.calls.call_count == 0
