"""Spans y logs del gateway (spec §3.5; T-U5-09): semconv GenAI y nada de contenido ni secretos."""

import logging

import httpx
import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode, Tracer
from respx import MockRouter

from agent_core.domain import GatewayError
from tests.u05.helpers import CHAT, DRAFT, GOOD_JSON, INPUTS, KEY, PROMPT, completion, make_world

SECRETS = (KEY, "CONTENIDO-SENSIBLE", "TEXTO-DEL-MODELO")


def _tracer() -> tuple[Tracer, InMemorySpanExporter]:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    return provider.get_tracer("prueba"), exporter


def test_a_call_emits_one_chat_span_with_genai_attributes(respx_mock: MockRouter) -> None:
    respx_mock.post(CHAT).respond(200, json=completion(GOOD_JSON, usage=(120, 30)))
    tracer, exporter = _tracer()
    make_world(tracer=tracer).gateway.generate(PROMPT, INPUTS, "es", DRAFT)
    (span,) = exporter.get_finished_spans()
    assert span.name == "chat"
    attrs = dict(span.attributes or {})
    assert attrs["gen_ai.operation.name"] == "chat" and attrs["gen_ai.provider.name"] == "openrouter"
    assert attrs["gen_ai.request.model"] == "vendor/modelo-x"
    assert attrs["gen_ai.response.model"] == "vendor/modelo-x-2026"
    assert attrs["gen_ai.usage.input_tokens"] == 120 and attrs["gen_ai.usage.output_tokens"] == 30
    assert attrs["agentcore.prompt"] == "resumen@1.0.0" and attrs["agentcore.model_profile"] == "perfil@1.0.0"
    assert "agentcore.gateway.error_kind" not in attrs


def test_an_error_records_its_kind_on_the_span(respx_mock: MockRouter) -> None:
    respx_mock.post(CHAT).respond(429, json={"error": {"message": "TEXTO-DEL-MODELO"}})
    tracer, exporter = _tracer()
    with pytest.raises(GatewayError):
        make_world(tracer=tracer).gateway.generate(PROMPT, INPUTS, "es")
    (span,) = exporter.get_finished_spans()
    assert dict(span.attributes or {})["agentcore.gateway.error_kind"] == "rate_limited"


def test_an_error_with_usage_keeps_the_usage_on_the_span(respx_mock: MockRouter) -> None:
    respx_mock.post(CHAT).respond(200, json=completion("no es json", usage=(7, 3)))
    tracer, exporter = _tracer()
    with pytest.raises(GatewayError):
        make_world(tracer=tracer).gateway.generate(PROMPT, INPUTS, "es", DRAFT)
    (span,) = exporter.get_finished_spans()
    attrs = dict(span.attributes or {})
    assert attrs["agentcore.gateway.error_kind"] == "invalid_output"
    assert attrs["gen_ai.usage.input_tokens"] == 7 and attrs["gen_ai.usage.output_tokens"] == 3


def test_an_error_span_has_no_exception_event_nor_status_description(respx_mock: MockRouter) -> None:
    respx_mock.post(CHAT).respond(503, json={"error": {"message": "TEXTO-DEL-MODELO"}})
    tracer, exporter = _tracer()
    with pytest.raises(GatewayError):
        make_world(tracer=tracer).gateway.generate(PROMPT, INPUTS, "es")
    (span,) = exporter.get_finished_spans()
    assert all(event.name != "exception" for event in span.events)
    assert span.status.status_code == StatusCode.ERROR and not span.status.description


@pytest.mark.parametrize("failure", ["http", "salida", "conexion"])
def test_neither_the_key_nor_the_content_reach_spans_logs_or_errors(
        respx_mock: MockRouter, caplog: pytest.LogCaptureFixture, failure: str) -> None:
    if failure == "http":
        respx_mock.post(CHAT).respond(503, json={"error": {"message": "TEXTO-DEL-MODELO CONTENIDO-SENSIBLE"}})
    elif failure == "salida":
        respx_mock.post(CHAT).respond(200, json=completion("TEXTO-DEL-MODELO", usage=(5, 5)))
    else:
        respx_mock.post(CHAT).mock(side_effect=httpx.ConnectError("TEXTO-DEL-MODELO " + KEY))
    tracer, exporter = _tracer()
    caplog.set_level(logging.INFO)  # el SDK `openai` registra el contenido de los requests solo en DEBUG
    with pytest.raises(GatewayError) as caught:
        make_world(tracer=tracer).gateway.generate(PROMPT, INPUTS, "es", DRAFT)
    assert exporter.get_finished_spans()
    seen = [caplog.text, str(caught.value), repr(caught.value)]
    for span in exporter.get_finished_spans():
        seen += [str(span.name), str(dict(span.attributes or {})),
                 *(str(dict(event.attributes or {})) for event in span.events)]
    for secret in SECRETS:
        assert all(secret not in text for text in seen), secret
