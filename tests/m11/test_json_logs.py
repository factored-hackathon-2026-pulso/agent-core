"""ADR 0003 #4 and I3: JSON logs with timestamp, exception type only, and run/trace correlation (T-M11-14)."""

import json
import logging

from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

import agent_telemetry as tel

pytest_plugins = ["tests.support.otel"]


def _record(exc: BaseException | None = None) -> logging.LogRecord:
    exc_info = (type(exc), exc, exc.__traceback__) if exc is not None else None
    record = logging.LogRecord("agentcore.api", logging.ERROR, __file__, 1, "error no controlado", None,
                               exc_info)
    record.created = 1_790_000_000.123  # fixed instant: `created` is set by `logging`, not by our Clock
    return record


def test_a_line_carries_timestamp_level_logger_and_message() -> None:
    line = json.loads(tel.JsonLogFormatter().format(_record()))
    assert line["timestamp"] == "2026-09-21T14:13:20.123Z"
    assert (line["level"], line["logger"]) == ("ERROR", "agentcore.api")
    assert line["message"] == "error no controlado"
    assert "exc_type" not in line and "trace_id" not in line


def test_an_exception_contributes_its_type_never_its_message_nor_stack() -> None:
    try:
        raise RuntimeError("SECRETO-123")
    except RuntimeError as exc:
        record = _record(exc)
        record.exc_text = "Traceback ... SECRETO-123"  # a previous formatter may have cached it
        text = tel.JsonLogFormatter().format(record)
    assert json.loads(text)["exc_type"] == "RuntimeError"
    assert "SECRETO-123" not in text and "Traceback" not in text


def test_extra_fields_are_not_emitted() -> None:
    record = _record()
    record.customer_text = "SECRETO"  # e.g. logger.info(..., extra={...})
    record.stack_info = "Stack (most recent call last): SECRETO"
    assert "SECRETO" not in tel.JsonLogFormatter().format(record)


def test_bound_context_and_trace_id_are_present(otel: InMemorySpanExporter) -> None:
    with tel.bind(run_id="run-0001", release="rel-1", agent="atencion@1.0.0"), tel.span(tel.CHAT):
        line = json.loads(tel.JsonLogFormatter().format(_record()))
    assert line["run_id"] == "run-0001" and line["agentcore.agent"] == "atencion@1.0.0"
    assert len(line["trace_id"]) == 32


def test_credentials_in_a_url_are_redacted() -> None:  # M3: SDK retry warnings may name the endpoint
    record = logging.LogRecord("opentelemetry.exporter", logging.WARNING, __file__, 1,
                               "retrying %s", ("https://usuario:SECRETO9@collector:4318/v1/traces",), None)
    line = json.loads(tel.JsonLogFormatter().format(record))
    assert "SECRETO9" not in line["message"] and "usuario" not in line["message"]
    assert "https://***@collector:4318/v1/traces" in line["message"]


def test_without_a_trace_the_line_carries_the_request_fallback() -> None:  # U3: same id as problem+json
    with tel.bind_trace_id("evt-0007"):
        line = json.loads(tel.JsonLogFormatter().format(_record()))
    assert line["trace_id"] == "evt-0007"
