"""Traducción de errores del gateway (spec §3.2, §5; T-U5-04, T-U5-05, T-U5-06, T-U5-08)."""

from decimal import Decimal

import httpx
import pytest
from respx import MockRouter

from agent_core.adapters.llm.config import EndpointConfig, default_client
from agent_core.domain import GatewayError, GatewayErrorKind
from tests.u05.helpers import CHAT, DRAFT, ENV, GOOD_JSON, INPUTS, PROMPT, completion, make_world


def _fail(w, schema=None) -> GatewayError:  # type: ignore[no-untyped-def]
    with pytest.raises(GatewayError) as caught:
        w.gateway.generate(PROMPT, INPUTS, "es", schema)
    return caught.value


@pytest.mark.parametrize(("status", "kind"), [
    (503, GatewayErrorKind.unavailable), (500, GatewayErrorKind.unavailable),
    (400, GatewayErrorKind.unavailable), (401, GatewayErrorKind.unavailable),
    (429, GatewayErrorKind.rate_limited)])
def test_http_status_maps_to_a_kind_with_exactly_one_request(  # T-U5-05 y T-U5-06
        respx_mock: MockRouter, status: int, kind: GatewayErrorKind) -> None:
    route = respx_mock.post(CHAT).respond(status, json={"error": {"message": "x"}})
    error = _fail(make_world())
    assert error.kind is kind and route.call_count == 1  # max_retries = 0
    assert error.tokens_in is None and error.cost_usd is None


def test_client_timeout_maps_to_timeout(respx_mock: MockRouter) -> None:
    respx_mock.post(CHAT).mock(side_effect=httpx.ConnectTimeout("lento"))
    assert _fail(make_world()).kind is GatewayErrorKind.timeout


@pytest.mark.parametrize("side_effect", [httpx.ConnectError("caído"), RuntimeError("no previsto")])
def test_connection_errors_and_unforeseen_exceptions_are_unavailable(
        respx_mock: MockRouter, side_effect: Exception) -> None:
    respx_mock.post(CHAT).mock(side_effect=side_effect)
    assert _fail(make_world()).kind is GatewayErrorKind.unavailable


def test_content_filter_is_refused_with_the_reported_usage(respx_mock: MockRouter) -> None:
    respx_mock.post(CHAT).respond(200, json=completion("", finish="content_filter", usage=(120, 30)))
    error = _fail(make_world())
    assert error.kind is GatewayErrorKind.refused
    assert (error.tokens_in, error.tokens_out, error.cost_usd) == (120, 30, Decimal("0.000810"))
    assert error.model == "vendor/modelo-x-2026"


def test_a_refusal_field_is_refused(respx_mock: MockRouter) -> None:
    respx_mock.post(CHAT).respond(200, json=completion(None, refusal="no puedo ayudar", usage=(10, 2)))
    assert _fail(make_world()).kind is GatewayErrorKind.refused


def test_output_that_breaks_the_schema_is_invalid_output_with_usage(  # T-U5-04
        respx_mock: MockRouter) -> None:
    respx_mock.post(CHAT).respond(200, json=completion('{"text": 5}', usage=(120, 30)))
    error = _fail(make_world(), DRAFT)
    assert error.kind is GatewayErrorKind.invalid_output
    assert (error.tokens_in, error.tokens_out, error.cost_usd) == (120, 30, Decimal("0.000810"))


def test_not_json_is_invalid_output(respx_mock: MockRouter) -> None:
    respx_mock.post(CHAT).respond(200, json=completion("no es json"))
    assert _fail(make_world(), DRAFT).kind is GatewayErrorKind.invalid_output


def test_truncated_output_with_a_schema_is_invalid_output(respx_mock: MockRouter) -> None:
    respx_mock.post(CHAT).respond(200, json=completion(GOOD_JSON[:10], finish="length"))
    assert _fail(make_world(), DRAFT).kind is GatewayErrorKind.invalid_output


def test_truncated_output_without_a_schema_is_returned_as_text(respx_mock: MockRouter) -> None:
    respx_mock.post(CHAT).respond(200, json=completion("Tu disputa quedó", finish="length"))
    assert make_world().gateway.generate(PROMPT, INPUTS, "es").output == "Tu disputa quedó"


def test_empty_choices_is_invalid_output(respx_mock: MockRouter) -> None:
    body = completion("x")
    body["choices"] = []
    respx_mock.post(CHAT).respond(200, json=body)
    assert _fail(make_world()).kind is GatewayErrorKind.invalid_output


def test_unknown_alias_is_unavailable_without_a_request(respx_mock: MockRouter) -> None:  # T-U5-08
    error = _fail(make_world(endpoints={}))
    assert error.kind is GatewayErrorKind.unavailable and respx_mock.calls.call_count == 0


@pytest.mark.parametrize("env", [{}, {"OPENROUTER_API_KEY": ""}, {"OPENROUTER_API_KEY": "   "}])
def test_missing_empty_or_blank_key_is_unavailable_without_a_request(
        respx_mock: MockRouter, env: dict[str, str]) -> None:  # T-U5-08
    error = _fail(make_world(env=env))
    assert error.kind is GatewayErrorKind.unavailable and respx_mock.calls.call_count == 0


def test_default_client_is_built_without_retries() -> None:  # T-U5-06
    endpoint = EndpointConfig("openrouter", "https://openrouter.test/api/v1", "K")
    client = default_client(endpoint, ENV["OPENROUTER_API_KEY"], 8)
    assert client.max_retries == 0


def test_usage_without_token_fields_is_treated_as_not_reported(respx_mock: MockRouter) -> None:
    body = completion('{"text": 5}')
    body["usage"] = {}
    respx_mock.post(CHAT).respond(200, json=body)
    error = _fail(make_world(), DRAFT)
    assert error.kind is GatewayErrorKind.invalid_output
    assert error.tokens_in is None and error.cost_usd is None


def test_success_with_usage_without_token_fields_reports_zero(respx_mock: MockRouter) -> None:
    body = completion("hola")
    body["usage"] = {}
    respx_mock.post(CHAT).respond(200, json=body)
    result = make_world().gateway.generate(PROMPT, INPUTS, "es")
    assert (result.tokens_in, result.tokens_out, result.cost_usd) == (0, 0, Decimal("0"))


@pytest.mark.parametrize("body", [[1, 2], "texto", {"choices": "no-es-lista"}])
def test_a_200_that_is_not_a_chat_completion_never_leaks_a_raw_exception(
        respx_mock: MockRouter, body: object) -> None:
    respx_mock.post(CHAT).respond(200, json=body)
    assert _fail(make_world()).kind in (GatewayErrorKind.unavailable, GatewayErrorKind.invalid_output)


def test_a_failing_client_factory_is_unavailable(respx_mock: MockRouter) -> None:
    def boom(*_: object) -> None:
        raise RuntimeError("SECRETO")

    error = _fail(make_world(client_factory=boom))
    assert error.kind is GatewayErrorKind.unavailable and respx_mock.calls.call_count == 0


def test_a_long_model_chosen_key_is_clipped_in_the_log(respx_mock: MockRouter, caplog) -> None:  # type: ignore[no-untyped-def]
    key = "CLAVE-ELEGIDA-POR-EL-MODELO-" + "x" * 300
    respx_mock.post(CHAT).respond(200, json=completion(f'{{"text": "a", "citations": [], "{key}": 1}}'))
    with caplog.at_level("WARNING", logger="agent_core.adapters.llm"):
        error = _fail(make_world(), DRAFT)
    assert error.kind is GatewayErrorKind.invalid_output
    assert "CLAVE-ELEGIDA-POR-EL-MODELO-" in caplog.text and key not in caplog.text
