"""Shared telemetry package (ADR 0003 #4): OTel spans with `agentcore.*` attributes and JSON logs."""

from agent_telemetry.context import bind, correlation
from agent_telemetry.logging import JsonLogFormatter
from agent_telemetry.semconv import SCHEMA_URL
from agent_telemetry.setup import setup_tracing, shutdown_tracing, tracer
from agent_telemetry.spans import (
    ALLOWED_ATTRIBUTES,
    CHAT,
    DECIDE,
    EXECUTE_TOOL,
    INVOKE_AGENT,
    RULE,
    SEMCONV_VERSION,
    MissingTelemetryContext,
    configure,
    current_trace_id,
    mark_error,
    record_span,
    set_attributes,
    set_content,
    span,
)

__all__ = [
    "ALLOWED_ATTRIBUTES",
    "CHAT",
    "DECIDE",
    "EXECUTE_TOOL",
    "INVOKE_AGENT",
    "RULE",
    "SCHEMA_URL",
    "SEMCONV_VERSION",
    "JsonLogFormatter",
    "MissingTelemetryContext",
    "bind",
    "configure",
    "correlation",
    "current_trace_id",
    "mark_error",
    "record_span",
    "set_attributes",
    "set_content",
    "setup_tracing",
    "shutdown_tracing",
    "span",
    "tracer",
]
