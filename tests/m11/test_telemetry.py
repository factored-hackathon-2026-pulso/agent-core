"""T-M11-09 (mitad telemetría): todo span lleva run_id y agentcore.release; trace_id disponible;
contenido apagado por defecto; backend caído no rompe. Más el endurecimiento de `span()` (I4, I5, F3, F7)."""

import json
import logging
from collections.abc import Sequence

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.semconv.schemas import Schemas

import agent_telemetry as tel
from agent_telemetry import spans as tel_spans

pytest_plugins = ["tests.support.otel"]


def test_every_span_carries_run_and_release_and_nesting_works(otel: InMemorySpanExporter) -> None:
    with tel.bind(run_id="run-0001", turn_id="turn-0001", session_id="session-0001", release="rel-1"):
        with tel.span(tel.INVOKE_AGENT, agentcore_agent="atencion@1.0.0"):
            with tel.span(tel.DECIDE, agentcore_node="pedir_cargo", gen_ai_operation_name="chat"):
                pass
            with tel.span(tel.EXECUTE_TOOL):
                pass
    spans = otel.get_finished_spans()
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


def test_bind_agent_is_a_correlation_attribute(otel: InMemorySpanExporter) -> None:
    with tel.bind(run_id="run-0001", release="rel-1", agent="atencion@1.0.0"), tel.span(tel.CHAT):
        assert tel.correlation()["agentcore.agent"] == "atencion@1.0.0"
    assert dict(otel.get_finished_spans()[0].attributes or {})["agentcore.agent"] == "atencion@1.0.0"
    assert tel.correlation() == {}


def test_span_without_context_is_a_no_op_outside_strict_mode(
        caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch) -> None:  # I4
    monkeypatch.setattr(tel_spans, "_warned", set())  # the once-per-name memory is process-wide
    tel.configure(strict=False)
    with caplog.at_level(logging.WARNING, logger="agent_telemetry"):
        with tel.span(tel.CHAT) as s, tel.span(tel.CHAT):
            assert s is trace.INVALID_SPAN
    assert caplog.text.count("sin run_id") == 1  # one warning per span name


def test_span_without_context_never_fails_the_caller_outside_strict_mode(
        monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tel_spans, "_warned", set())
    tel.configure(strict=False)
    with pytest.raises(KeyError):  # the caller's own error still propagates untouched
        with tel.span(tel.INVOKE_AGENT, attributes={"user.text": "x"}):
            raise KeyError("x")


def test_span_without_context_is_rejected_in_strict_mode(otel: InMemorySpanExporter) -> None:
    with pytest.raises(tel.MissingTelemetryContext):
        with tel.span(tel.CHAT):
            pass


def test_an_exception_inside_a_span_leaves_only_its_type(otel: InMemorySpanExporter) -> None:
    with pytest.raises(RuntimeError):
        with tel.bind(run_id="run-0001", release="rel-1"), tel.span(tel.INVOKE_AGENT):
            raise RuntimeError("SECRETO-123")
    (span,) = otel.get_finished_spans()
    assert dict(span.attributes or {})["error.type"] == "RuntimeError"
    assert span.status.status_code == trace.StatusCode.ERROR
    assert all(e.name != "exception" for e in span.events) and not span.status.description
    assert "SECRETO-123" not in str(dict(span.attributes or {}))


def test_attributes_outside_the_allow_list_are_dropped_or_rejected(otel: InMemorySpanExporter) -> None:
    with pytest.raises(ValueError):
        with tel.bind(run_id="run-0001", release="rel-1"), tel.span(tel.CHAT, attributes={"user.text": "x"}):
            pass
    with pytest.raises(ValueError):
        with tel.bind(run_id="run-0001", release="rel-1"), tel.span(tel.CHAT, agentcore_args="x"):
            pass
    tel.configure(strict=False)
    with tel.bind(run_id="run-0001", release="rel-1"), tel.span(tel.CHAT, attributes={"user.text": "x"}):
        pass
    assert "user.text" not in dict(otel.get_finished_spans()[-1].attributes or {})


def test_set_attributes_goes_through_the_allow_list(otel: InMemorySpanExporter) -> None:
    with tel.bind(run_id="run-0001", release="rel-1"), tel.span(tel.INVOKE_AGENT) as s:
        tel.set_attributes(s, {"agentcore.problem_code": "internal_error"})
        with pytest.raises(ValueError):
            tel.set_attributes(s, {"agentcore.args": "x"})
    attrs = dict(otel.get_finished_spans()[0].attributes or {})
    assert attrs["agentcore.problem_code"] == "internal_error" and "agentcore.args" not in attrs


def test_the_allow_list_never_names_a_payload_field() -> None:
    payload = {"args", "result", "error", "inputs", "value", "p_cal", "top_k", "text", "message"}
    forbidden = payload | {f"agentcore.{key}" for key in payload} | {f"gen_ai.{key}" for key in payload}
    assert not tel.ALLOWED_ATTRIBUTES & forbidden


def test_record_span_uses_the_given_times_and_parent(otel: InMemorySpanExporter) -> None:
    with tel.bind(run_id="run-0001", release="rel-1"), tel.span(tel.INVOKE_AGENT) as parent:
        tel.record_span(tel.RULE, parent=parent, start_ns=1_000, end_ns=5_000,
                        attributes={"agentcore.node": "n1", "agentcore.rule.result": True})
    rule = next(s for s in otel.get_finished_spans() if s.name == tel.RULE)
    assert (rule.start_time, rule.end_time) == (1_000, 5_000)
    assert rule.parent is not None and rule.parent.span_id == parent.get_span_context().span_id
    assert dict(rule.attributes or {})["run_id"] == "run-0001"


def test_record_span_on_a_no_op_parent_records_nothing(otel: InMemorySpanExporter) -> None:
    tel.record_span(tel.RULE, parent=trace.INVALID_SPAN, start_ns=1, end_ns=2, attributes={})
    assert otel.get_finished_spans() == ()


def test_spans_declare_the_pinned_schema(otel: InMemorySpanExporter) -> None:
    with tel.bind(run_id="run-0001", release="rel-1"), tel.span(tel.CHAT):
        pass
    scope = otel.get_finished_spans()[0].instrumentation_scope
    assert scope is not None and scope.schema_url == tel.SCHEMA_URL
    with tel.tracer("x").start_as_current_span("y") as raw:  # tracer() serves the same provider
        assert raw.get_span_context().is_valid


def test_trace_id_is_available_inside_a_span(otel: InMemorySpanExporter) -> None:
    assert tel.current_trace_id() is None
    with tel.bind(run_id="run-0001", release="rel-1"), tel.span(tel.INVOKE_AGENT):
        trace_id = tel.current_trace_id()
    assert trace_id is not None and len(trace_id) == 32
    finished = otel.get_finished_spans()[0]
    assert finished.context is not None
    assert format(finished.context.trace_id, "032x") == trace_id


def test_content_capture_is_off_by_default_and_uses_given_audit_view(otel: InMemorySpanExporter) -> None:
    with tel.bind(run_id="run-0001", release="rel-1"):
        with tel.span(tel.CHAT) as s:
            tel.set_content(s, "input", {"texto": "⟦name:1⟧"})
        tel.configure(capture_content=True)
        with tel.span(tel.CHAT) as s:
            tel.set_content(s, "input", {"texto": "⟦name:1⟧"})
    off, on = otel.get_finished_spans()
    assert "agentcore.content.input" not in (off.attributes or {})
    assert json.loads(str((on.attributes or {})["agentcore.content.input"])) == {"texto": "⟦name:1⟧"}


def test_failing_backend_never_breaks_the_caller() -> None:
    class Broken(SpanExporter):
        def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
            raise RuntimeError("phoenix caído")

    tel.setup_tracing(exporter=Broken())
    try:
        with tel.bind(run_id="run-0001", release="rel-1"), tel.span(tel.CHAT):
            pass  # no lanza
    finally:
        tel.shutdown_tracing()


def test_json_logs_carry_run_id_and_trace_id(otel: InMemorySpanExporter) -> None:
    record = logging.LogRecord("x", logging.INFO, __file__, 1, "hola", None, None)
    with tel.bind(run_id="run-0001", release="rel-1"), tel.span(tel.CHAT):
        line = json.loads(tel.JsonLogFormatter().format(record))
    assert line["run_id"] == "run-0001" and line["agentcore.release"] == "rel-1"
    assert len(line["trace_id"]) == 32


def test_semconv_version_is_pinned_to_an_installed_schema() -> None:
    assert f"https://opentelemetry.io/schemas/{tel.SEMCONV_VERSION}" in {s.value for s in Schemas}
    assert tel.SCHEMA_URL == f"https://opentelemetry.io/schemas/{tel.SEMCONV_VERSION}"


def test_setup_tracing_never_touches_the_global_provider() -> None:
    before = trace.get_tracer_provider()
    tel.setup_tracing(exporter=InMemorySpanExporter())
    try:
        assert trace.get_tracer_provider() is before
    finally:
        tel.shutdown_tracing()


def test_after_shutdown_spans_are_no_op() -> None:
    exporter = InMemorySpanExporter()
    tel.setup_tracing(exporter=exporter)
    tel.shutdown_tracing()
    with tel.bind(run_id="run-0001", release="rel-1"), tel.span(tel.CHAT) as s:
        assert not s.is_recording()
    assert exporter.get_finished_spans() == ()
    tel.shutdown_tracing()  # idempotent


def test_endpoint_uses_batch_processor_and_reconfiguring_shuts_down_the_previous_provider() -> None:
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    first = InMemorySpanExporter()
    tel.setup_tracing(exporter=first)
    try:
        provider = tel.setup_tracing(endpoint="http://127.0.0.1:9/v1/traces")
        assert first._stopped  # type: ignore[attr-defined]  # el provider anterior se cerró
        processors = provider._active_span_processor._span_processors  # type: ignore[attr-defined]
        assert any(isinstance(p, BatchSpanProcessor) for p in processors)
        tel.setup_tracing(exporter=InMemorySpanExporter())  # cierra el de lotes sin colgar
    finally:
        tel.shutdown_tracing()


def test_bound_context_wins_over_span_kwargs(otel: InMemorySpanExporter) -> None:
    with tel.bind(run_id="run-0001", release="rel-1"), tel.span(tel.CHAT, run_id="run-otro"):
        pass
    assert dict(otel.get_finished_spans()[0].attributes or {})["run_id"] == "run-0001"
