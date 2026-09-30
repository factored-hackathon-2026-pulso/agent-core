"""`TurnEventSink`: vuelca el buffer del turno y luego los eventos que llegan (decisión 6)."""

from datetime import timedelta

from agent_core.domain import EngineEvent, Outcome
from agent_core.turn.buffer import EventBuffer, TurnEventSink
from agent_core.turn.events import TurnEvents
from testing.builders import run_state
from testing.fakes.clock import FakeClock
from testing.fakes.ids import FakeIds
from testing.fakes.storage import InMemoryStore
from tests.m04.helpers import PlainChain


def setup() -> tuple[TurnEvents, EventBuffer, InMemoryStore, list[int]]:
    return TurnEvents(FakeIds(), FakeClock()), EventBuffer(), InMemoryStore(), []


def types(store: InMemoryStore, uow_events: list[EngineEvent]) -> list[str]:
    return [e.type for e in uow_events]  # type: ignore[attr-defined]


def test_vuelco_previo_materializa_turn_started_sin_guardas_una_sola_vez() -> None:
    ev, buf, store, calls = setup()
    state = run_state()

    def ensure() -> None:
        calls.append(1)
        buf.fill(ev.turn_started(state, "turn-1", None, guards=None))

    buf.reserve_turn_started()
    sink = TurnEventSink(buf, PlainChain(), ensure)
    closed = ev.run_closed(state, "turn-1", Outcome.abandoned, "abandonment")
    with store.uow() as uow:
        sink.record(uow, state, [closed])
        sink.record(uow, state, [closed.model_copy(update={"event_id": "e2"})])
        uow.commit()
    assert calls == [1]
    assert types(store, store.events[state.run_id]) == ["turn_started", "run_closed", "run_closed"]


def test_sin_reserva_no_llama_a_ensure_y_vuelca_pendientes_primero() -> None:
    ev, buf, store, calls = setup()
    state = run_state()
    pending = ev.expiry_evaluated(
        state, "turn-1", state.last_activity_at, timedelta(minutes=30), expired=False
    )
    buf.add(pending)
    sink = TurnEventSink(buf, PlainChain(), lambda: calls.append(1))
    incoming = ev.run_closed(state, "turn-1", Outcome.abandoned, "abandonment")
    with store.uow() as uow:
        sink.record(uow, state, [incoming])
        uow.commit()
    assert calls == []
    assert types(store, store.events[state.run_id]) == ["expiry_evaluated", "run_closed"]
    assert buf.peek() == []


def test_el_buffer_completa_el_turn_id_de_los_eventos_que_llegan_sin_el() -> None:
    """Los puertos de M2 (`DecisionPort`, `ResponderPort`) no reciben `turn_id`: sus eventos traen `None`."""
    ev, _, _, _ = setup()
    state = run_state()
    buf = EventBuffer(turn_id="turn-7")
    sin_turno = ev.run_closed(state, None, Outcome.abandoned, "abandonment")
    con_turno = ev.run_closed(state, "turn-1", Outcome.abandoned, "abandonment")
    buf.add(sin_turno, con_turno)
    assert [e.turn_id for e in buf.peek()] == ["turn-7", "turn-1"]
