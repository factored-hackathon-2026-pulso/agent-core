"""`GatewayJevTransport`: JEV through the llm-gateway service (`POST /v1/jev`), not called directly."""

import json
import logging
from decimal import Decimal
from typing import Any

import httpx
import pytest
from respx import MockRouter

from agent_core.decision import DecisionConfigError, JevTransport, JevTransportError
from agent_core.decision.providers.jev_gateway import GatewayJevTransport, UnconfiguredJevTransport
from agent_core.domain import dumps, loads
from agent_telemetry import bind

BASE = "https://llm-gateway.test"
URL = f"{BASE}/v1/jev"
TOKEN = "gw-token-SECRETO-0002"
REQUEST: dict[str, Any] = {"state": {"locale": "es",
                                     "input": {"mensaje": "CONTENIDO-SENSIBLE", "monto": Decimal("500.00")}},
                           "model": "jev-1.13.0", "questions": []}
JEV_ANSWER = ('{"model":"jev-1.13.0","answers":{"command":{"p":0.9712}},'
              '"usage":{"input_tokens":10,"output_tokens":2}}')


def ok(retried: str = "[]") -> str:
    return '{"response":' + JEV_ANSWER + ',"retried":' + retried + "}"


def error(kind: str, upstream: int | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {"kind": kind, "message": f"jev error: {kind}"}
    if upstream is not None:
        body["upstream_status"] = upstream
    return {"error": body}


def transport() -> GatewayJevTransport:
    return GatewayJevTransport(BASE, TOKEN)


def test_it_is_a_jev_transport() -> None:
    t: JevTransport = transport()
    assert callable(t.send)


def test_the_envelope_carries_the_request_the_budget_and_the_bearer_token(respx_mock: MockRouter) -> None:
    route = respx_mock.post(URL).respond(200, text=ok())
    response = transport().send(REQUEST, 2500)
    sent = route.calls.last.request
    assert sent.headers["Authorization"] == f"Bearer {TOKEN}"
    assert sent.headers["Content-Type"].startswith("application/json")
    body = json.loads(sent.content)
    assert set(body) == {"request", "timeout_ms", "labels"} or set(body) == {"request", "timeout_ms"}
    assert body["timeout_ms"] == 2500 and body["request"]["model"] == "jev-1.13.0"
    assert '"monto":500.00' in sent.content.decode().replace(" ", "")
    assert response["model"] == "jev-1.13.0"


def test_the_answer_keeps_exact_decimals(respx_mock: MockRouter) -> None:
    respx_mock.post(URL).respond(200, text=ok())
    response = transport().send(REQUEST, 1000)
    answers = response["answers"]
    assert isinstance(answers, dict) and answers["command"] == loads('{"p": 0.9712}')
    assert dumps(response["usage"]) == '{"input_tokens":10,"output_tokens":2}'


def test_the_http_client_waits_a_little_longer_than_the_budget(respx_mock: MockRouter) -> None:
    captured: dict[str, Any] = {}

    def record(request: httpx.Request) -> httpx.Response:
        captured.update(request.extensions["timeout"])
        return httpx.Response(200, text=ok())

    respx_mock.post(URL).mock(side_effect=record)
    transport().send(REQUEST, 4000)
    assert captured["read"] == 6.0  # the service enforces the 4 s budget; the client allows 2 s of margin


def test_the_bound_turn_correlation_becomes_labels(respx_mock: MockRouter) -> None:
    route = respx_mock.post(URL).respond(200, text=ok())
    with bind(run_id="r1", turn_id="t1", release="rel-1", agent="ag@1"):
        transport().send(REQUEST, 1000)
    assert json.loads(route.calls.last.request.content)["labels"] == {
        "run_id": "r1", "turn_id": "t1", "release": "rel-1", "agent": "ag@1"}


def test_the_retryable_statuses_the_service_saw_are_counted(respx_mock: MockRouter) -> None:
    respx_mock.post(URL).respond(200, text=ok("[429,529,429]"))
    t = transport()
    t.send(REQUEST, 1000)
    t.send(REQUEST, 1000)
    assert t.retryable_responses == {429: 4, 529: 2}


def test_a_timeout_from_the_service_is_a_timeout(respx_mock: MockRouter) -> None:
    respx_mock.post(URL).respond(504, json=error("timeout"))
    with pytest.raises(TimeoutError):
        transport().send(REQUEST, 1000)


@pytest.mark.parametrize(("status", "kind", "upstream"), [
    (429, "rate_limited", 429), (502, "unavailable", 422), (502, "unavailable", 401),
    (502, "unavailable", 529),
    (502, "unavailable", 500)])
def test_the_upstream_status_comes_back_as_the_transport_error_status(
        respx_mock: MockRouter, status: int, kind: str, upstream: int) -> None:
    respx_mock.post(URL).respond(status, json=error(kind, upstream))
    with pytest.raises(JevTransportError) as caught:
        transport().send(REQUEST, 1000)
    assert caught.value.status == upstream


def test_a_failure_without_an_upstream_answer_has_no_status(respx_mock: MockRouter) -> None:
    respx_mock.post(URL).respond(502, json=error("unavailable"))
    with pytest.raises(JevTransportError) as caught:
        transport().send(REQUEST, 1000)
    assert caught.value.status is None


def test_the_service_rejecting_our_token_is_a_config_error(respx_mock: MockRouter) -> None:
    respx_mock.post(URL).respond(401, json=error("unauthorized"))
    with pytest.raises(DecisionConfigError):
        transport().send(REQUEST, 1000)


@pytest.mark.parametrize("status", [400, 404, 413, 415, 500])
def test_other_rejections_are_transport_errors(respx_mock: MockRouter, status: int) -> None:
    respx_mock.post(URL).respond(status, json=error("bad_request"))
    with pytest.raises(JevTransportError):
        transport().send(REQUEST, 1000)


@pytest.mark.parametrize("content", [b"", b"not json", b"[]", b'{"response": []}', b'{"retried": []}'])
def test_garbage_is_a_transport_error(respx_mock: MockRouter, content: bytes) -> None:
    respx_mock.post(URL).respond(200, content=content)
    with pytest.raises(JevTransportError):
        transport().send(REQUEST, 1000)


def test_client_side_failures(respx_mock: MockRouter) -> None:
    respx_mock.post(URL).mock(side_effect=httpx.ReadTimeout("lento"))
    with pytest.raises(TimeoutError):
        transport().send(REQUEST, 1000)
    respx_mock.post(URL).mock(side_effect=httpx.ConnectError("caído"))
    with pytest.raises(JevTransportError) as caught:
        transport().send(REQUEST, 1000)
    assert caught.value.status is None


def test_there_is_exactly_one_request_no_retries_on_this_side(respx_mock: MockRouter) -> None:
    route = respx_mock.post(URL).respond(429, json=error("rate_limited", 429))
    with pytest.raises(JevTransportError):
        transport().send(REQUEST, 1000)
    assert route.call_count == 1


def test_neither_the_token_nor_content_reach_logs_errors_or_repr(
        respx_mock: MockRouter, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    respx_mock.post(URL).respond(502, json=error("unavailable", 500))
    t = transport()
    with pytest.raises(JevTransportError) as caught:
        t.send(REQUEST, 1000)
    respx_mock.post(URL).mock(side_effect=httpx.ConnectError("conexion a https://llm-gateway.test"))
    with pytest.raises(JevTransportError) as caught2:
        t.send(REQUEST, 1000)
    for text in (caplog.text, str(caught.value), str(caught2.value), repr(t)):
        for secret in (TOKEN, "CONTENIDO-SENSIBLE", "500.00"):
            assert secret not in text


def test_an_unconfigured_transport_is_a_config_error_and_sends_nothing(respx_mock: MockRouter) -> None:
    route = respx_mock.post(URL).respond(200, text=ok())
    with pytest.raises(DecisionConfigError, match="AGENTCORE_LLM_GATEWAY_URL"):
        UnconfiguredJevTransport().send(REQUEST, 1000)
    assert route.call_count == 0
