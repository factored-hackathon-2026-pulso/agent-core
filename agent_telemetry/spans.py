"""Spans `invoke_agent` › `agentcore.decide`, `agentcore.rule`, `execute_tool`, `chat` (M11 §3.2).

Telemetry never fails a caller and never leaks a payload (I4, C2, rule 6):
- a span without `run_id` or `agentcore.release` is a no-op with one warning per span name, except in strict
  mode (tests), where it raises `MissingTelemetryContext`;
- an exception inside a span leaves only its type (`error.type`), never its message or stack;
- attributes go through the closed list `ALLOWED_ATTRIBUTES`: anything else is dropped, or raises `ValueError`
  in strict mode."""

import json
import logging
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from typing import Any

from opentelemetry import trace
from opentelemetry.context import Context
from opentelemetry.trace import Link, Span, Status, StatusCode

from agent_telemetry.context import current, trace_fallback
from agent_telemetry.semconv import SEMCONV_VERSION
from agent_telemetry.setup import tracer as _tracer

__all__ = [
    "ALLOWED_ATTRIBUTES",
    "CHAT",
    "CONTENT_ATTRIBUTES",
    "DECIDE",
    "EXECUTE_TOOL",
    "INVOKE_AGENT",
    "RULE",
    "SEMCONV_VERSION",
    "TRANSFER",
    "MissingTelemetryContext",
    "configure",
    "current_trace_id",
    "mark_error",
    "record_span",
    "set_attributes",
    "set_content",
    "span",
]

INVOKE_AGENT, DECIDE, RULE, EXECUTE_TOOL, CHAT, TRANSFER = (
    "invoke_agent",
    "agentcore.decide",
    "agentcore.rule",
    "execute_tool",
    "chat",
    "agentcore.transfer",
)
_SCOPE = "agent_telemetry"
_REQUIRED = ("run_id", "agentcore.release")
_LOG = logging.getLogger("agent_telemetry")

# Closed list (rule 6): ids, entity refs, enums, counters and booleans. Never a payload value or customer
# text.
# Adding a name here is an interface change of the operational plane: say why in the M11 spec.
ALLOWED_ATTRIBUTES: frozenset[str] = frozenset({
    # correlation (ADR 0003 #4, `bind`)
    "run_id", "turn_id", "session_id", "agentcore.release", "agentcore.agent",
    # invoke_agent
    "agentcore.entry", "agentcore.principal_type", "agentcore.locale", "agentcore.flow", "agentcore.node",
    "agentcore.problem_code", "error.type",
    # GenAI semconv
    "gen_ai.operation.name", "gen_ai.agent.name", "gen_ai.tool.name", "gen_ai.tool.call.id",
    # derived children (M11 §3.2)
    "agentcore.decision.id", "agentcore.decision.model", "agentcore.decision.provider",
    "agentcore.decision.fallback_depth", "agentcore.decision.tokens",
    "agentcore.rule.policy", "agentcore.rule.result",
    "agentcore.tool", "agentcore.tool.status", "agentcore.tool.attempt",
    # transfer (ADR 0021 D9): ids and enums only. `from_agent` / `to_agent` are agent ids without version.
    "agentcore.transfer.id", "agentcore.transfer.from_agent", "agentcore.transfer.to_agent",
    "agentcore.transfer.to_release_id", "agentcore.transfer.outcome", "agentcore.transfer.reason_code",
    # Langfuse trace-level and observation fields (ids, tags and enums; no payload). They are inert for any
    # other OTLP backend. Derived from `bind` only when `configure(langfuse_attributes=True)`.
    "langfuse.session.id", "langfuse.user.id", "langfuse.trace.tags", "langfuse.trace.name",
    "langfuse.release", "langfuse.environment", "langfuse.observation.type",
    "agentcore.agent_step.tokens", "agentcore.agent_step.kind",
})
# Content (prompts and completions): allowed only while `configure(capture_content=True)` (rule 6,
# ADR 0003 #4). Off, they are dropped silently, never an error. The caller passes the `audit` view (M7).
CONTENT_ATTRIBUTES: frozenset[str] = frozenset({
    "gen_ai.prompt", "gen_ai.completion", "langfuse.observation.input", "langfuse.observation.output",
})
_OBSERVATION_TYPES = {INVOKE_AGENT: "agent", EXECUTE_TOOL: "tool"}
_capture_content = False
_langfuse_attributes = False
_strict = False
_warned: set[str] = set()


class MissingTelemetryContext(RuntimeError):
    """A span without `run_id` or `agentcore.release` would break correlation (ADR 0003 #4). Strict mode
    only."""


def configure(*, capture_content: bool | None = None, strict: bool | None = None,
              langfuse_attributes: bool | None = None) -> None:
    """`None` leaves a setting as it is. `strict=True` is for tests: missing context and attributes outside
    the closed list raise instead of degrading. `capture_content` admits `CONTENT_ATTRIBUTES`;
    `langfuse_attributes` derives the `langfuse.*` set from the bound context (default off)."""
    global _capture_content, _strict, _langfuse_attributes
    if capture_content is not None:
        _capture_content = capture_content
    if langfuse_attributes is not None:
        _langfuse_attributes = langfuse_attributes
    if strict is not None:
        _strict = strict


def _key(name: str) -> str:
    """`agentcore_flow` → `agentcore.flow`; `gen_ai_operation_name` → `gen_ai.operation.name`."""
    for prefix in ("agentcore_", "gen_ai_"):
        if name.startswith(prefix):
            return prefix[:-1] + "." + name[len(prefix) :].replace("_", ".")
    return name


def _langfuse_derived(name: str, bound: Mapping[str, str]) -> dict[str, Any]:
    """The `langfuse.*` attributes of a span, from the bound correlation context; empty unless enabled."""
    if not _langfuse_attributes:
        return {}
    derived: dict[str, Any] = {}
    if (session := bound.get("session_id")) is not None:
        derived["langfuse.session.id"] = session
    if (release := bound.get("agentcore.release")) is not None:
        derived["langfuse.release"] = release
    if (agent := bound.get("agentcore.agent")) is not None:  # `id@version`: the tag is low-cardinality
        derived["langfuse.trace.tags"] = (f"agent:{agent.split('@', 1)[0]}",)
    if (kind := _OBSERVATION_TYPES.get(name)) is not None:
        derived["langfuse.observation.type"] = kind
    return derived


def _allowed(name: str, attrs: Mapping[str, Any]) -> dict[str, Any]:
    permitted = ALLOWED_ATTRIBUTES | CONTENT_ATTRIBUTES if _capture_content else ALLOWED_ATTRIBUTES
    unknown = sorted(k for k in attrs if k not in permitted and k not in CONTENT_ATTRIBUTES)
    if unknown and _strict:
        raise ValueError(f"span {name!r}: atributos fuera de la lista cerrada: {', '.join(unknown)}")
    return {k: v for k, v in attrs.items() if k in permitted and v is not None}


def _missing_context(name: str, merged: Mapping[str, Any]) -> bool:
    """`True` (skip the span) without `run_id` or `agentcore.release`; strict mode raises instead."""
    missing = [k for k in _REQUIRED if k not in merged]
    if not missing:
        return False
    if _strict:
        raise MissingTelemetryContext(f"span {name!r} sin {', '.join(missing)}; usa bind(...)")
    if name not in _warned:  # I4: telemetry never fails a turn; warn once per span name and process
        _warned.add(name)
        _LOG.warning("span %s sin %s: se omite", name, ", ".join(missing))
    return True


def mark_error(active: Span, exc: BaseException) -> None:
    """Only the type: an exception's message may carry secrets or PII (rule 6)."""
    active.set_attribute("error.type", type(exc).__name__)
    active.set_status(Status(StatusCode.ERROR))


@contextmanager
def span(name: str, *, attributes: Mapping[str, Any] | None = None, links: Sequence[Link] = (),
         context: Context | None = None, **attrs: Any) -> Iterator[Span]:
    """A live span under `context` (default: the current one). The bound context (`bind`) wins over
    `attributes` and kwargs: a span cannot change its run_id or release."""
    merged = _allowed(name, {**{_key(k): v for k, v in attrs.items()}, **(attributes or {}), **current(),
                             **_langfuse_derived(name, current())})
    if _missing_context(name, merged):
        yield trace.INVALID_SPAN
        return
    with _tracer(_SCOPE).start_as_current_span(
            name, context=context, links=links, attributes=merged,
            record_exception=False, set_status_on_exception=False) as active:
        try:
            yield active
        except BaseException as exc:
            mark_error(active, exc)
            raise


def record_span(name: str, *, parent: Span, start_ns: int, end_ns: int,
                attributes: Mapping[str, Any]) -> None:
    """A finished child of `parent` with explicit times (derived from audit events, M11 §3.2). No-op on a
    non-recording parent (no provider, sampled out or a no-op span). Needs the bound run context like `span()`
    (strict mode raises without it)."""
    if not parent.is_recording():
        return
    merged = _allowed(name, {**attributes, **current(), **_langfuse_derived(name, current())})
    if _missing_context(name, merged):
        return
    child = _tracer(_SCOPE).start_span(name, context=trace.set_span_in_context(parent), attributes=merged,
                                       start_time=start_ns, record_exception=False,
                                       set_status_on_exception=False)
    child.end(end_time=max(end_ns, start_ns))


def set_attributes(active: Span, attributes: Mapping[str, Any]) -> None:
    """`Span.set_attributes` through the closed list."""
    active.set_attributes(_allowed("set_attributes", attributes))


def set_content(active: Span, key: str, audit_view: object) -> None:
    """Content in traces: off by default; when on, the caller passes the `audit` view (M7)."""
    if _capture_content:
        payload = json.dumps(audit_view, default=str, ensure_ascii=False)
        active.set_attribute(f"agentcore.content.{key}", payload)


def current_trace_id() -> str | None:
    """The active OTel trace id or, without one, the id bound by `bind_trace_id` (U3, F8)."""
    ctx = trace.get_current_span().get_span_context()
    return format(ctx.trace_id, "032x") if ctx.is_valid else trace_fallback()
