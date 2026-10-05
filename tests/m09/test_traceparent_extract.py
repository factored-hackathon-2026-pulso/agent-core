"""An inbound W3C `traceparent` makes the request span a child of the caller's trace (Langfuse plan, C1)."""

from fastapi import FastAPI
from fastapi.testclient import TestClient
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from agent_core.api.tracing import install_tracing
from testing.fakes.ids import FakeIds

pytest_plugins = ["tests.support.otel"]

TRACE_ID = "4bf92f3577b34da6a3ce929d0e0e4736"
PARENT_SPAN = "00f067aa0ba902b7"


def _client() -> TestClient:
    app = FastAPI()
    install_tracing(app, FakeIds())

    @app.get("/ok")
    def ok() -> dict[str, str]:
        return {"ok": "1"}

    return TestClient(app)


def test_the_request_span_joins_the_callers_trace(otel: InMemorySpanExporter) -> None:
    resp = _client().get("/ok", headers={"traceparent": f"00-{TRACE_ID}-{PARENT_SPAN}-01"})
    assert resp.status_code == 200
    (span,) = otel.get_finished_spans()
    assert span.context is not None and span.parent is not None
    assert format(span.context.trace_id, "032x") == TRACE_ID
    assert format(span.parent.span_id, "016x") == PARENT_SPAN


def test_without_traceparent_the_request_starts_its_own_trace(otel: InMemorySpanExporter) -> None:
    _client().get("/ok")
    (span,) = otel.get_finished_spans()
    assert span.parent is None


def test_a_malformed_traceparent_is_ignored(otel: InMemorySpanExporter) -> None:
    resp = _client().get("/ok", headers={"traceparent": "garbage"})
    assert resp.status_code == 200
    (span,) = otel.get_finished_spans()
    assert span.parent is None
