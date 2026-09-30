"""T-M11-09 (mitad telemetría): todo span lleva run_id y agentcore.release; trace_id disponible;
contenido apagado por defecto; backend caído no rompe."""

import json
import logging
from collections.abc import Sequence

import pytest
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.semconv.schemas import Schemas

import agent_telemetry as tel
from agent_telemetry.setup import setup_tracing


@pytest.fixture
def exporter() -> InMemorySpanExporter:
    exp = InMemorySpanExporter()
    setup_tracing(exporter=exp)
    tel.configure(capture_content=False)
    return exp


def test_every_span_carries_run_and_release_and_nesting_works(exporter: InMemorySpanExporter) -> None:
    with tel.bind(run_id="run-0001", turn_id="turn-0001", session_id="session-0001", release="rel-1"):
        with tel.span(tel.INVOKE_AGENT, agentcore_agent="atencion@1.0.0"):
            with tel.span(tel.DECIDE, agentcore_node="pedir_cargo", gen_ai_operation_name="chat"):
                pass
            with tel.span(tel.EXECUTE_TOOL):
                pass
    spans = exporter.get_finished_spans()
    assert {s.name for s in spans} == {tel.INVOKE_AGENT, tel.DECIDE, tel.EXECUTE_TOOL}
    for s in spans:
        attrs = dict(s.attributes or {})
        assert attrs["run_id"] == "run-0001" and attrs["agentcore.release"] == "rel-1"
        assert attrs["turn_id"] == "turn-0001" and attrs["session_id"] == "session-0001"
    root = next(s for s in spans if s.name == tel.INVOKE_AGENT)
    assert dict(root.attributes or {})["agentcore.agent"] == "atencion@1.0.0"
    child = next(s for s in spans if s.name == tel.DECIDE)
    assert root.context is not None and child.context is not None
    assert child.parent is not None and child.parent.span_id == root.context.span_id
    assert dict(child.attributes or {})["gen_ai.operation.name"] == "chat"


def test_span_without_context_is_rejected() -> None:
    with pytest.raises(tel.MissingTelemetryContext):
        with tel.span(tel.CHAT):
            pass


def test_trace_id_is_available_inside_a_span(exporter: InMemorySpanExporter) -> None:
    assert tel.current_trace_id() is None
    with tel.bind(run_id="run-0001", release="rel-1"), tel.span(tel.INVOKE_AGENT):
        trace_id = tel.current_trace_id()
    assert trace_id is not None and len(trace_id) == 32
    finished = exporter.get_finished_spans()[0]
    assert finished.context is not None
    assert format(finished.context.trace_id, "032x") == trace_id


def test_content_capture_is_off_by_default_and_uses_given_audit_view(exporter: InMemorySpanExporter) -> None:
    with tel.bind(run_id="run-0001", release="rel-1"):
        with tel.span(tel.CHAT) as s:
            tel.set_content(s, "input", {"texto": "⟦name:1⟧"})
        tel.configure(capture_content=True)
        with tel.span(tel.CHAT) as s:
            tel.set_content(s, "input", {"texto": "⟦name:1⟧"})
    off, on = exporter.get_finished_spans()
    assert "agentcore.content.input" not in (off.attributes or {})
    assert json.loads(str((on.attributes or {})["agentcore.content.input"])) == {"texto": "⟦name:1⟧"}


def test_failing_backend_never_breaks_the_caller() -> None:
    class Broken(SpanExporter):
        def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
            raise RuntimeError("phoenix caído")

    setup_tracing(exporter=Broken())
    with tel.bind(run_id="run-0001", release="rel-1"), tel.span(tel.CHAT):
        pass  # no lanza


def test_json_logs_carry_run_id_and_trace_id(exporter: InMemorySpanExporter) -> None:
    record = logging.LogRecord("x", logging.INFO, __file__, 1, "hola", None, None)
    with tel.bind(run_id="run-0001", release="rel-1"), tel.span(tel.CHAT):
        line = json.loads(tel.JsonLogFormatter().format(record))
    assert line["run_id"] == "run-0001" and line["agentcore.release"] == "rel-1"
    assert len(line["trace_id"]) == 32


def test_semconv_version_is_pinned_to_an_installed_schema() -> None:
    assert f"https://opentelemetry.io/schemas/{tel.SEMCONV_VERSION}" in {s.value for s in Schemas}


def test_endpoint_uses_batch_processor_and_reconfiguring_shuts_down_the_previous_provider() -> None:
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    first = InMemorySpanExporter()
    setup_tracing(exporter=first)
    provider = setup_tracing(endpoint="http://127.0.0.1:9/v1/traces")
    assert first._stopped  # type: ignore[attr-defined]  # el provider anterior se cerró
    processors = provider._active_span_processor._span_processors  # type: ignore[attr-defined]
    assert any(isinstance(p, BatchSpanProcessor) for p in processors)
    setup_tracing(exporter=InMemorySpanExporter())  # cierra el de lotes sin colgar


def test_bound_context_wins_over_span_kwargs(exporter: InMemorySpanExporter) -> None:
    with tel.bind(run_id="run-0001", release="rel-1"), tel.span(tel.CHAT, run_id="run-otro"):
        pass
    assert dict(exporter.get_finished_spans()[0].attributes or {})["run_id"] == "run-0001"
