"""Paquete común de telemetría (ADR 0003 #4): spans OTel con atributos `agentcore.*`."""

from agent_telemetry.context import bind
from agent_telemetry.logging import JsonLogFormatter
from agent_telemetry.setup import setup_tracing
from agent_telemetry.spans import (
    CHAT,
    DECIDE,
    EXECUTE_TOOL,
    INVOKE_AGENT,
    RULE,
    SEMCONV_VERSION,
    MissingTelemetryContext,
    configure,
    current_trace_id,
    set_content,
    span,
)

__all__ = [
    "CHAT",
    "DECIDE",
    "EXECUTE_TOOL",
    "INVOKE_AGENT",
    "RULE",
    "SEMCONV_VERSION",
    "JsonLogFormatter",
    "MissingTelemetryContext",
    "bind",
    "configure",
    "current_trace_id",
    "set_content",
    "setup_tracing",
    "span",
]
