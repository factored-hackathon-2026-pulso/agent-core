"""`TurnTelemetry` doubles (m04 §3.9): one records what M4 reports, in order; one fails on purpose."""

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field

from agent_core.domain import EngineEvent
from agent_core.turn import TransferOutcome, TransferSpan, TurnScope, TurnSpan


@dataclass
class RecordedTransfer:
    transfer_id: str
    outcome: TransferOutcome | None = None
    link: object = field(default_factory=object)


@dataclass
class RecordedTurn:
    scope: TurnScope
    links: tuple[object, ...]
    events: list[EngineEvent] = field(default_factory=list)
    transfers: list[RecordedTransfer] = field(default_factory=list)
    error: str | None = None
    closed: bool = False


class _TransferSpan:
    def __init__(self, recorded: RecordedTransfer) -> None:
        self._recorded = recorded
        self.link: object | None = recorded.link

    def finish(self, outcome: TransferOutcome) -> None:
        self._recorded.outcome = outcome


class _TurnSpan:
    def __init__(self, turn: RecordedTurn) -> None:
        self._turn = turn

    def record(self, events: Sequence[EngineEvent]) -> None:
        self._turn.events.extend(events)

    @contextmanager
    def transfer(self, transfer_id: str) -> Iterator[TransferSpan]:
        recorded = RecordedTransfer(transfer_id)
        self._turn.transfers.append(recorded)
        yield _TransferSpan(recorded)


class RecordingTelemetry:
    def __init__(self) -> None:
        self.turns: list[RecordedTurn] = []

    @contextmanager
    def turn(self, scope: TurnScope, links: Sequence[object] = ()) -> Iterator[TurnSpan]:
        recorded = RecordedTurn(scope, tuple(links))
        self.turns.append(recorded)
        try:
            yield _TurnSpan(recorded)
        except BaseException as exc:
            recorded.error = type(exc).__name__
            raise
        finally:
            recorded.closed = True


class TelemetryBroken(RuntimeError):
    """What `FailingTelemetry` raises. Its message stands in for a secret that must never reach a log."""


class _FailingSpan:
    def __init__(self, owner: "FailingTelemetry") -> None:
        self._owner = owner

    def record(self, events: Sequence[EngineEvent]) -> None:
        if "record" in self._owner.fail_on:
            raise TelemetryBroken("SECRETO-record")

    @contextmanager
    def transfer(self, transfer_id: str) -> Iterator[TransferSpan]:
        raise TelemetryBroken("SECRETO-transfer")
        yield  # pragma: no cover


class _FailingTurn:
    """A hand-written context manager, so a test can make each of `__enter__` and `__exit__` fail."""

    def __init__(self, owner: "FailingTelemetry") -> None:
        self._owner = owner

    def __enter__(self) -> TurnSpan:
        if "enter" in self._owner.fail_on:
            raise TelemetryBroken("SECRETO-enter")
        return _FailingSpan(self._owner)

    def __exit__(self, *exc_info: object) -> bool:
        if "exit" in self._owner.fail_on:
            raise TelemetryBroken("SECRETO-exit")
        return self._owner.swallow


class FailingTelemetry:
    """Fails where `fail_on` says (`turn`, `enter`, `record`, `exit`); `swallow=True` makes `__exit__` claim
    to handle the turn's own exception."""

    def __init__(self, *fail_on: str, swallow: bool = False) -> None:
        self.fail_on = frozenset(fail_on)
        self.swallow = swallow

    def turn(self, scope: TurnScope, links: Sequence[object] = ()) -> _FailingTurn:
        if "turn" in self.fail_on:
            raise TelemetryBroken("SECRETO-turn")
        return _FailingTurn(self)
