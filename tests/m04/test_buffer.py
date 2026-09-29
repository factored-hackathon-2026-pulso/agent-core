"""Buffer de eventos del turno (m04 §3.7 y §3.1 paso 14)."""

from datetime import timedelta

import pytest

from agent_core.domain import EngineEvent, Outcome
from agent_core.turn.buffer import EventBuffer
from agent_core.turn.events import TurnEvents
from testing.builders import run_state
from testing.fakes.clock import FakeClock
from testing.fakes.ids import FakeIds


def events() -> tuple[EngineEvent, EngineEvent, EngineEvent]:
    ev = TurnEvents(FakeIds(), FakeClock())
    state = run_state()
    a = ev.expiry_evaluated(state, "turn-1", state.last_activity_at, timedelta(minutes=30), expired=False)
    b = ev.run_closed(state, "turn-1", Outcome.abandoned, "abandonment")
    ts = ev.turn_started(state, "turn-1", None, guards=None)
    return a, b, ts


def test_turn_started_reservado_va_primero_aunque_se_cree_despues() -> None:
    a, b, ts = events()
    buf = EventBuffer()
    buf.add(a)
    buf.reserve_turn_started()
    buf.add(b)
    assert not buf.turn_started_filled
    buf.fill(ts)
    assert buf.turn_started_filled
    assert buf.peek() == [ts, a, b]


def test_sin_reserva_el_orden_es_el_de_llegada() -> None:
    a, b, _ = events()
    buf = EventBuffer()
    buf.add(a, b)
    assert buf.peek() == [a, b]


def test_drain_vacia_y_no_repite_eventos() -> None:
    a, _, ts = events()
    buf = EventBuffer()
    buf.reserve_turn_started()
    buf.add(a)
    buf.fill(ts)
    assert buf.drain() == [ts, a]
    assert buf.drain() == [] and buf.peek() == []


def test_drain_con_reserva_sin_llenar_es_error() -> None:
    buf = EventBuffer()
    buf.reserve_turn_started()
    with pytest.raises(RuntimeError):
        buf.drain()


def test_fill_sin_reserva_o_repetido_es_error() -> None:
    _, _, ts = events()
    buf = EventBuffer()
    with pytest.raises(RuntimeError):
        buf.fill(ts)
    buf.reserve_turn_started()
    buf.fill(ts)
    with pytest.raises(RuntimeError):
        buf.fill(ts)


def test_peek_devuelve_copia() -> None:
    a, _, _ = events()
    buf = EventBuffer()
    buf.add(a)
    buf.peek().clear()
    assert buf.peek() == [a]
