"""Buffer de eventos pendientes de un turno (m04 §3.1 paso 14).

`turn_started` abre el turno pero lleva la salida de M6, que aún no existe cuando otros eventos ya se
producen: se reserva su lugar al frente y se llena después."""

from agent_core.domain import EngineEvent


class EventBuffer:
    def __init__(self) -> None:
        self._events: list[EngineEvent] = []
        self._reserved = False
        self._turn_started: EngineEvent | None = None

    @property
    def turn_started_filled(self) -> bool:
        return self._turn_started is not None

    def add(self, *events: EngineEvent) -> None:
        self._events.extend(events)

    def reserve_turn_started(self) -> None:
        self._reserved = True

    def fill(self, event: EngineEvent) -> None:
        if not self._reserved:
            raise RuntimeError("turn_started no fue reservado")
        if self._turn_started is not None:
            raise RuntimeError("turn_started ya fue llenado")
        self._turn_started = event

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
