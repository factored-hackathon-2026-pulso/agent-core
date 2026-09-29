"""Contexto de correlación del run (ADR 0003 #4): run_id, turn_id, session_id y release."""

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
) -> Iterator[None]:
    values = {"run_id": run_id, "turn_id": turn_id, "session_id": session_id, "agentcore.release": release}
    token = _CTX.set({**_CTX.get(), **{k: v for k, v in values.items() if v is not None}})
    try:
        yield
    finally:
        _CTX.reset(token)


def current() -> Mapping[str, str]:
    return _CTX.get()
