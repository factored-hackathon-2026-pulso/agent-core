"""Run correlation context (ADR 0003 #4): run_id, turn_id, session_id, release and agent."""

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from types import MappingProxyType

_CTX: ContextVar[Mapping[str, str]] = ContextVar("agent_telemetry_ctx", default=MappingProxyType({}))


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
