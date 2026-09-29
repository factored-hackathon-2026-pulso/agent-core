"""Spans `invoke_agent` › `agentcore.decide`, `agentcore.rule`, `execute_tool`, `chat` (M11 §3.2)."""

import json
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from opentelemetry import trace
from opentelemetry.trace import Span

from agent_telemetry.context import current
from agent_telemetry.setup import get_provider

# ADR 0003: la versión de semconv GenAI queda fijada. Verificada (decisión 15 del plan) contra
# `opentelemetry-semantic-conventions` 0.66b0 (uv.lock): su `Schemas` llega hasta 1.44.0 y sus
# `gen_ai_attributes` traen `gen_ai.operation.name` con los valores `invoke_agent`, `execute_tool` y `chat`
# que usan estos spans. El "1.37.0" del plan existe en el paquete pero es más antiguo; se fija la última.
SEMCONV_VERSION = "1.44.0"
INVOKE_AGENT, DECIDE, RULE, EXECUTE_TOOL, CHAT = (
    "invoke_agent",
    "agentcore.decide",
    "agentcore.rule",
    "execute_tool",
    "chat",
)
_REQUIRED = ("run_id", "agentcore.release")
_capture_content = False


class MissingTelemetryContext(RuntimeError):
    """Un span sin `run_id` o `agentcore.release` rompería la correlación (ADR 0003 #4)."""


def configure(*, capture_content: bool = False) -> None:
    global _capture_content
    _capture_content = capture_content


def _key(name: str) -> str:
    """`agentcore_flow` → `agentcore.flow`; `gen_ai_operation_name` → `gen_ai.operation.name`."""
    for prefix in ("agentcore_", "gen_ai_"):
        if name.startswith(prefix):
            return prefix[:-1] + "." + name[len(prefix) :].replace("_", ".")
    return name


@contextmanager
def span(name: str, **attrs: Any) -> Iterator[Span]:
    # el contexto enlazado (`bind`) manda sobre los kwargs: un span no puede cambiar su run_id ni su release
    merged: dict[str, Any] = {**{_key(k): v for k, v in attrs.items() if v is not None}, **current()}
    missing = [k for k in _REQUIRED if k not in merged]
    if missing:
        raise MissingTelemetryContext(f"span {name!r} sin {', '.join(missing)}; usa bind(...)")
    tracer = get_provider().get_tracer(
        "agent_telemetry", SEMCONV_VERSION, schema_url=f"https://opentelemetry.io/schemas/{SEMCONV_VERSION}"
    )
    with tracer.start_as_current_span(name, attributes=merged) as active:
        yield active


def set_content(active: Span, key: str, audit_view: object) -> None:
    """Contenido en trazas: apagado por defecto; si se activa, quien llama pasa la vista `audit`."""
    if _capture_content:
        payload = json.dumps(audit_view, default=str, ensure_ascii=False)
        active.set_attribute(f"agentcore.content.{key}", payload)


def current_trace_id() -> str | None:
    ctx = trace.get_current_span().get_span_context()
    return format(ctx.trace_id, "032x") if ctx.is_valid else None
