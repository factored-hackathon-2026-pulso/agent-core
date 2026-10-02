"""Telemetry of the turn on M4's side (m04 §3.9).

- `NoTurnTelemetry` is the default `TurnTelemetry`: it does nothing (unit tests, replay, `agentcore record`,
  registry evaluation). The real one lives in composition over `agent_telemetry`; M4 never imports
  OpenTelemetry (`.importlinter`).
- `observed_turn` is how the engine opens a turn: whatever the telemetry does, the turn goes on. A failure of
  the telemetry (opening, `record`, `transfer`, closing) is logged with its type only and never reaches the
  turn; the telemetry can neither swallow nor replace the turn's own exception."""

import logging
from collections.abc import Callable, Iterator, Sequence
from contextlib import AbstractContextManager, contextmanager
from types import TracebackType
from typing import Literal

from agent_core.domain import EngineEvent
from agent_core.turn.ports import TransferOutcome, TransferSpan, TurnScope, TurnSpan, TurnTelemetry

_LOG = logging.getLogger("agent_core.turn")


class _NoTransferSpan:
    link: object | None = None

    def finish(self, outcome: TransferOutcome) -> None:
        return None


class _NoTurnSpan:
    def record(self, events: Sequence[EngineEvent]) -> None:
        return None

    @contextmanager
    def transfer(self, transfer_id: str) -> Iterator[TransferSpan]:
        yield _NoTransferSpan()


NO_SPAN: TurnSpan = _NoTurnSpan()


class NoTurnTelemetry:
    @contextmanager
    def turn(self, scope: TurnScope, links: Sequence[object] = ()) -> Iterator[TurnSpan]:
        yield NO_SPAN


def _failed(where: str, exc: BaseException) -> None:
    """Only the type: the message of a telemetry exception may carry an endpoint or a header (I3)."""
    _LOG.warning("la telemetría del turno falló en %s (%s); el turno sigue sin ella", where,
                 type(exc).__name__)


class _Guarded[T]:
    """Enters `open()`'s context manager with every telemetry failure contained. `wrap` adapts what it yields;
    on failure the context yields `fallback`. `__exit__` never suppresses the body's exception."""

    def __init__(self, where: str, open_: Callable[[], AbstractContextManager[T]], wrap: Callable[[T], T],
                 fallback: T) -> None:
        self._where, self._open, self._wrap, self._fallback = where, open_, wrap, fallback
        self._inner: AbstractContextManager[T] | None = None

    def __enter__(self) -> T:
        try:
            inner = self._open()
            value = inner.__enter__()
        except Exception as exc:
            _failed(self._where, exc)
            return self._fallback
        self._inner = inner
        return self._wrap(value)

    def __exit__(self, exc_type: type[BaseException] | None, exc: BaseException | None,
                 tb: TracebackType | None) -> Literal[False]:
        inner, self._inner = self._inner, None
        if inner is None:
            return False
        try:
            inner.__exit__(exc_type, exc, tb)  # its answer is ignored: telemetry never swallows a turn error
        except Exception as raised:
            if raised is not exc:  # re-raising the turn's own exception is not a failure of the telemetry
                _failed(self._where, raised)
        return False


class _GuardedTransferSpan:
    def __init__(self, inner: TransferSpan) -> None:
        self._inner = inner
        try:
            self.link: object | None = inner.link
        except Exception as exc:
            _failed("transfer.link", exc)
            self.link = None

    def finish(self, outcome: TransferOutcome) -> None:
        try:
            self._inner.finish(outcome)
        except Exception as exc:
            _failed("transfer.finish", exc)


class _GuardedTurnSpan:
    def __init__(self, inner: TurnSpan) -> None:
        self._inner = inner

    def record(self, events: Sequence[EngineEvent]) -> None:
        try:
            self._inner.record(events)
        except Exception as exc:
            _failed("record", exc)

    def transfer(self, transfer_id: str) -> AbstractContextManager[TransferSpan]:
        return _Guarded[TransferSpan](
            "transfer", lambda: self._inner.transfer(transfer_id), _GuardedTransferSpan, _NoTransferSpan())


def observed_turn(telemetry: TurnTelemetry, scope: TurnScope,
                  links: Sequence[object] = ()) -> AbstractContextManager[TurnSpan]:
    """`telemetry.turn(scope, links)` with every failure of the telemetry contained (m04 §3.9)."""
    return _Guarded[TurnSpan]("turn", lambda: telemetry.turn(scope, links), _GuardedTurnSpan, NO_SPAN)
