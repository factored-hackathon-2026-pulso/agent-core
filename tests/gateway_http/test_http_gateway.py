"""`HttpLLMGateway`: the client of the standalone llm-gateway service (llm-gateway/api/openapi.yaml)."""

import json
import logging
from decimal import Decimal
from typing import Any

import httpx
import pytest
from opentelemetry.sdk.trace import TracerProvider
from respx import MockRouter

from agent_core.adapters.llm.http_gateway import HttpLLMGateway
from agent_core.domain import GatewayError, GatewayErrorKind, SchemaError, StructuredMode, loads
from agent_telemetry import bind
from tests.gateway_http.helpers import DRAFT, GENERATE, INPUTS, PROMPT, TOKEN, failure, make_gateway, success


def _gen(gateway: HttpLLMGateway, schema: dict[str, Any] | None = None, locale: str = "es") -> Any:
    return gateway.generate(PROMPT, INPUTS, locale, schema)  # type: ignore[arg-type]


def _fail(gateway: HttpLLMGateway, schema: dict[str, Any] | None = None) -> GatewayError:
    with pytest.raises(GatewayError) as caught:
        _gen(gateway, schema)
    return caught.value


def test_the_request_carries_the_prompt_inputs_schema_and_the_profile(respx_mock: MockRouter) -> None:
    route = respx_mock.post(GENERATE).respond(200, text=success())
    _gen(make_gateway(), DRAFT)
    request = route.calls.last.request
    assert request.headers["Authorization"] == f"Bearer {TOKEN}"
    assert request.headers["Content-Type"].startswith("application/json")
    body = json.loads(request.content)
    assert set(body) == {"prompt", "inputs", "schema", "profile", "labels"}
    assert body["prompt"] == "Resume la disputa."
    assert body["inputs"] == {"cliente": {"nombre": "⟦name:1⟧"}, "asunto": "CONTENIDO-SENSIBLE"}
    assert body["schema"] == DRAFT
    assert body["profile"] == {
        "endpoint_alias": "openrouter", "model": "vendor/modelo-x", "temperature": 0.2, "max_tokens": 300,
        "timeout_s": 8, "structured": "prompted",
        "price": {"input_per_mtok": "3.00", "output_per_mtok": "15.00"}}
    assert body["labels"] == {"prompt": "resumen@1.0.0", "model_profile": "perfil@1.0.0"}


def test_without_a_schema_the_field_is_omitted_and_the_locale_picks_the_text(respx_mock: MockRouter) -> None:
    route = respx_mock.post(GENERATE).respond(200, text=success())
    _gen(make_gateway(structured=StructuredMode.native), None, "pt")
    body = json.loads(route.calls.last.request.content)
    assert "schema" not in body and body["prompt"] == "Resuma a disputa."
    assert body["profile"]["structured"] == "native"


def test_decimals_travel_exactly_in_both_directions(respx_mock: MockRouter) -> None:
    route = respx_mock.post(GENERATE).respond(200, text=success({"total": Decimal("500.00")}))
    result = make_gateway().generate(PROMPT, {"monto": Decimal("1234.50"), "tasa": Decimal("0.0725")}, "es")
    sent = route.calls.last.request.content.decode().replace(" ", "")
    assert '"monto":1234.50' in sent and '"tasa":0.0725' in sent
    assert result.output == loads('{"total": 500.00}')
    assert isinstance(result.output, dict) and str(result.output["total"]) == "500.00"


def test_the_bound_turn_correlation_becomes_labels(respx_mock: MockRouter) -> None:
    route = respx_mock.post(GENERATE).respond(200, text=success())
    with bind(run_id="r1", turn_id="t1", session_id="s1", release="rel-1", agent="ag@1"):
        _gen(make_gateway())
    labels = json.loads(route.calls.last.request.content)["labels"]
    assert labels == {"prompt": "resumen@1.0.0", "model_profile": "perfil@1.0.0", "run_id": "r1",
                      "turn_id": "t1", "session_id": "s1", "release": "rel-1", "agent": "ag@1"}


def test_a_success_becomes_a_generation_result(respx_mock: MockRouter) -> None:
    respx_mock.post(GENERATE).respond(200, text=success({"text": "hola", "citations": []}, tokens=(120, 30)))
    result = _gen(make_gateway(), DRAFT)
    assert result.output == {"text": "hola", "citations": []}
    assert (result.tokens_in, result.tokens_out) == (120, 30)
    assert result.cost_usd == Decimal("0.000810") and isinstance(result.cost_usd, Decimal)
    assert result.model == "vendor/modelo-x-2026" and result.usage_known is True


def test_unknown_usage_is_kept_unknown(respx_mock: MockRouter) -> None:
    body = success("t", tokens=(0, 0), cost="0.000000", usage_known=False)
    respx_mock.post(GENERATE).respond(200, text=body)
    result = _gen(make_gateway())
    assert result.usage_known is False and result.cost_usd == Decimal("0")


@pytest.mark.parametrize(("status", "kind"), [
    (504, "timeout"), (502, "unavailable"), (429, "rate_limited"), (502, "invalid_output"), (502, "refused")])
def test_error_kinds_map_one_to_one_with_the_reported_usage(
        respx_mock: MockRouter, status: int, kind: str) -> None:
    route = respx_mock.post(GENERATE).respond(status, json=failure(kind, tokens=(120, 30), cost="0.000810"))
    error = _fail(make_gateway())
    assert error.kind is GatewayErrorKind(kind) and route.call_count == 1  # one request, no retries
    assert (error.tokens_in, error.tokens_out, error.cost_usd) == (120, 30, Decimal("0.000810"))
    assert error.model == "vendor/modelo-x"


def test_an_error_without_usage_carries_none(respx_mock: MockRouter) -> None:
    respx_mock.post(GENERATE).respond(504, json=failure("timeout"))
    error = _fail(make_gateway())
    assert (error.tokens_in, error.tokens_out, error.cost_usd) == (None, None, None)


@pytest.mark.parametrize(("status", "body"), [
    (400, failure("bad_request")), (401, failure("unauthorized")), (413, failure("payload_too_large")),
    (415, failure("unsupported_media_type")), (405, failure("method_not_allowed")),
    (404, failure("not_found")),
    (502, failure("something_new")), (500, {"unexpected": True}), (200, {"output": 1})])
def test_anything_else_is_unavailable(respx_mock: MockRouter, status: int, body: dict[str, Any]) -> None:
    respx_mock.post(GENERATE).respond(status, json=body)
    assert _fail(make_gateway()).kind is GatewayErrorKind.unavailable


@pytest.mark.parametrize("content", [b"", b"not json", b"[]", b'{"error": "x"}', b'{"error": {"kind": 5}}'])
@pytest.mark.parametrize("status", [200, 502])
def test_garbage_bodies_are_unavailable(respx_mock: MockRouter, content: bytes, status: int) -> None:
    respx_mock.post(GENERATE).respond(status, content=content)
    assert _fail(make_gateway()).kind is GatewayErrorKind.unavailable


def test_transport_failures(respx_mock: MockRouter) -> None:
    respx_mock.post(GENERATE).mock(side_effect=httpx.ReadTimeout("lento"))
    assert _fail(make_gateway()).kind is GatewayErrorKind.timeout
    respx_mock.post(GENERATE).mock(side_effect=httpx.ConnectError("caído"))
    assert _fail(make_gateway()).kind is GatewayErrorKind.unavailable
    respx_mock.post(GENERATE).mock(side_effect=RuntimeError("no previsto"))
    assert _fail(make_gateway()).kind is GatewayErrorKind.unavailable


def test_the_http_client_waits_a_little_longer_than_the_call_deadline(respx_mock: MockRouter) -> None:
    captured: dict[str, Any] = {}

    def record(request: httpx.Request) -> httpx.Response:
        captured.update(request.extensions["timeout"])
        return httpx.Response(200, text=success())

    respx_mock.post(GENERATE).mock(side_effect=record)
    _gen(make_gateway(timeout_s=20))
    assert captured["read"] == 25.0  # the service enforces the 20 s deadline; the client allows a margin


def test_a_missing_locale_is_a_programming_error_and_makes_no_request(respx_mock: MockRouter) -> None:
    route = respx_mock.post(GENERATE).respond(200, text=success())
    with pytest.raises(SchemaError):
        make_gateway().generate(PROMPT, INPUTS, "fr")  # type: ignore[arg-type]
    assert route.call_count == 0


def test_trace_context_is_propagated_to_the_service(respx_mock: MockRouter) -> None:
    route = respx_mock.post(GENERATE).respond(200, text=success())
    tracer = TracerProvider().get_tracer("prueba")
    with tracer.start_as_current_span("turno") as span:
        _gen(make_gateway())
        trace_id = format(span.get_span_context().trace_id, "032x")
    assert trace_id in route.calls.last.request.headers["traceparent"]


def test_neither_the_token_nor_content_reach_logs_or_error_messages(
        respx_mock: MockRouter, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    respx_mock.post(GENERATE).respond(502, json=failure("unavailable"))
    error = _fail(make_gateway())
    respx_mock.post(GENERATE).mock(side_effect=httpx.ConnectError("conexion a https://llm-gateway.test"))
    error2 = _fail(make_gateway())
    for text in (caplog.text, str(error), str(error2)):
        for secret in (TOKEN, "CONTENIDO-SENSIBLE", "⟦name:1⟧"):
            assert secret not in text
