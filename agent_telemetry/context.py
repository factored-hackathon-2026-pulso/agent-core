"""Run correlation context (ADR 0003 #4): run_id, turn_id, session_id, release and agent."""

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from types import MappingProxyType

_CTX: ContextVar[Mapping[str, str]] = ContextVar("agent_telemetry_ctx", default=MappingProxyType({}))
_TRACE_FALLBACK: ContextVar[str | None] = ContextVar("agent_telemetry_trace_fallback", default=None)


@contextmanager
def bind(
    *,
    run_id: str | None = None,
    turn_id: str | None = None,
    session_id: str | None = None,
    release: str | None = None,
    agent: str | None = None,
) -> Iterator[None]:
    """`agent` is `"id@version"` (M11 §3.2). Nested binds add to (and override) the enclosing one."""
    values = {"run_id": run_id, "turn_id": turn_id, "session_id": session_id, "agentcore.release": release,
              "agentcore.agent": agent}
    token = _CTX.set(MappingProxyType(
        {**_CTX.get(), **{k: v for k, v in values.items() if v is not None}}))
    try:
        yield
    finally:
        _CTX.reset(token)


def current() -> Mapping[str, str]:
    return _CTX.get()


def correlation() -> Mapping[str, str]:
    """The bound correlation attributes (read-only), for callers that build their own spans or log lines."""
    return _CTX.get()


@contextmanager
def bind_trace_id(trace_id: str) -> Iterator[None]:
    """The request's trace id when no OTel trace is active (U3): M9's middleware binds the id it took from its
    `IdSource`, so the turn's `TurnResult`, the logs and `problem+json` give the same value. Like any
    `ContextVar`, it reaches the threadpool that runs a sync endpoint (anyio copies the context)."""
    token = _TRACE_FALLBACK.set(trace_id)
    try:
        yield
    finally:
        _TRACE_FALLBACK.reset(token)


def trace_fallback() -> str | None:
    return _TRACE_FALLBACK.get()
