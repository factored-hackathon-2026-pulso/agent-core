"""C1 and C2 of the observability audit (U1): FastAPI's native telemetry is off and the request span never
records an exception's message or stack (T-M9-15, T-M9-16)."""

from collections.abc import Iterator
from dataclasses import replace

import pytest
from fastapi import FastAPI
from fastapi.telemetry import _runtime
from fastapi.testclient import TestClient
from opentelemetry import _logs, metrics, trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode

from agent_core.api.app import FASTAPI_TELEMETRY_OFF, Authenticate, create_app
from agent_telemetry import setup as telemetry_setup
from tests.m09.conftest import api_deps

SECRET = "SECRETO-cliente-123"


def _providers() -> tuple[object, object, object]:
    return trace.get_tracer_provider(), metrics.get_meter_provider(), _logs.get_logger_provider()


@pytest.fixture
def exporter(monkeypatch: pytest.MonkeyPatch) -> Iterator[InMemorySpanExporter]:
    """A private provider for the request span (no global: T2 removes `set_tracer_provider`)."""
    exp = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exp))
    monkeypatch.setattr(telemetry_setup, "_PROVIDER", provider)
    yield exp
    provider.shutdown()


def _boom(app: FastAPI, authenticate: Authenticate) -> None:
    @app.get("/v1/boom")
    def boom() -> None:
        raise RuntimeError(SECRET)


def test_native_telemetry_is_off_even_with_an_otlp_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:  # T-M9-15
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://127.0.0.1:9")
    before, owned = _providers(), list(_runtime._owned)
    app = create_app(api_deps()[0])
    configured = {k: app._telemetry[k] for k in FASTAPI_TELEMETRY_OFF}  # type: ignore[literal-required]
    assert configured == FASTAPI_TELEMETRY_OFF
    assert all(value is False for value in FASTAPI_TELEMETRY_OFF.values())
    assert not app._native_telemetry.enabled()
    with TestClient(app, raise_server_exceptions=False) as client:  # runs the lifespan (auto-configure)
        client.get("/v1/no-existe")
    after = _providers()
    assert all(a is b for a, b in zip(before, after, strict=True))  # no global provider installed
    assert _runtime._owned == owned  # no OTLP exporter of FastAPI's logs or metrics


def test_an_unhandled_error_leaves_no_text_on_the_request_span(
    exporter: InMemorySpanExporter,
) -> None:  # T-M9-16
    deps, _ = api_deps()
    client = TestClient(create_app(replace(deps, extensions=(_boom,))), raise_server_exceptions=False)
    response = client.get("/v1/boom")
    assert response.status_code == 500 and SECRET not in response.text
    (span,) = [s for s in exporter.get_finished_spans() if s.name == "agentcore.api.request"]
    attrs = dict(span.attributes or {})
    assert attrs["error.type"] == "RuntimeError" and attrs["http.response.status_code"] == 500
    assert span.status.status_code == StatusCode.ERROR and not span.status.description
    assert all(event.name != "exception" for event in span.events)
    seen = [str(attrs), *(str(dict(e.attributes or {})) for e in span.events)]
    assert all(SECRET not in text for text in seen)


def test_a_handled_5xx_marks_the_span_as_error_without_description(
    exporter: InMemorySpanExporter,
) -> None:
    deps, _ = api_deps()

    def internal(app: FastAPI, authenticate: Authenticate) -> None:
        from agent_core.domain import EngineError, ProblemCode

        @app.get("/v1/internal")
        def fail() -> None:
            raise EngineError(ProblemCode.internal_error, "no-sale")

    client = TestClient(create_app(replace(deps, extensions=(internal,))), raise_server_exceptions=False)
    assert client.get("/v1/internal").status_code == 500
    (span,) = [s for s in exporter.get_finished_spans() if s.name == "agentcore.api.request"]
    assert dict(span.attributes or {})["http.response.status_code"] == 500
    assert span.status.status_code == StatusCode.ERROR and not span.status.description
