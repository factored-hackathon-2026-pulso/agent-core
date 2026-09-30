"""Buffer de eventos pendientes de un turno (m04 §3.1 paso 14).

`turn_started` abre el turno pero lleva la salida de M6, que aún no existe cuando otros eventos ya se
producen: se reserva su lugar al frente y se llena después."""

from collections.abc import Callable

from agent_core.domain import EngineEvent, RunState
from agent_core.ports import UnitOfWork
from agent_core.turn.ports import EventChain


class EventBuffer:
    def __init__(self, turn_id: str | None = None) -> None:
        self._turn_id = turn_id
        self._events: list[EngineEvent] = []
        self._reserved = False
        self._turn_started: EngineEvent | None = None

    @property
    def turn_started_reserved(self) -> bool:
        return self._reserved

    @property
    def turn_started_filled(self) -> bool:
        return self._turn_started is not None

    def add(self, *events: EngineEvent) -> None:
        # Los puertos de M2 (`DecisionPort`, `ResponderPort`) no reciben `turn_id`: sus eventos llegan sin él.
        self._events.extend(self._with_turn(event) for event in events)

    def _with_turn(self, event: EngineEvent) -> EngineEvent:
        if event.turn_id is not None or self._turn_id is None:
            return event
        return event.model_copy(update={"turn_id": self._turn_id})

    def reserve_turn_started(self) -> None:
        self._reserved = True

    def fill(self, event: EngineEvent) -> None:
        if not self._reserved:
            raise RuntimeError("turn_started no fue reservado")
        if self._turn_started is not None:
            raise RuntimeError("turn_started ya fue llenado")
        self._turn_started = event

    def transform(self, fn: Callable[[EngineEvent], EngineEvent]) -> None:
        """Reemplaza cada evento pendiente por `fn(evento)` (p. ej. rellenar `transcript_fp`)."""
        self._events = [fn(e) for e in self._events]

    def peek(self) -> list[EngineEvent]:
        head = [self._turn_started] if self._turn_started is not None else []
        return [*head, *self._events]

    def drain(self) -> list[EngineEvent]:
        if self._reserved and self._turn_started is None:
            raise RuntimeError("turn_started reservado y sin llenar")
        out = self.peek()
        self._events = []
        self._turn_started = None
        self._reserved = False
        return out


class TurnEventSink:
    """`EventRecorder` de M2/M3 (decisión 6): vuelca primero el buffer del turno y luego lo que llega.

    Si `turn_started` está reservado y sin llenar, lo materializa antes (`ensure_turn_started`)."""

    def __init__(
        self, buffer: EventBuffer, chain: EventChain, ensure_turn_started: Callable[[], None]
    ) -> None:
        self._buffer = buffer
        self._chain = chain
        self._ensure = ensure_turn_started

    def record(self, uow: UnitOfWork, state: RunState, events: list[EngineEvent]) -> None:
        if self._buffer.turn_started_reserved and not self._buffer.turn_started_filled:
            self._ensure()
        self._chain.append(uow, state.run_id, [*self._buffer.drain(), *events])
