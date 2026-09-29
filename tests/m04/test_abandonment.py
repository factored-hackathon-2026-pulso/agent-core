"""Paso 4: un turno que encuentra el run vencido lo cierra `abandoned` y responde `410` (P2)."""

from datetime import timedelta

import pytest

from agent_core.domain import EngineError, InvalidationReason, Outcome, ProblemCode
from testing.builders import action
from tests.m04.harness import RUN_ID, World
from tests.m04.helpers import cmd


def proposed_run(w: World) -> None:
    w.open_run(
        active_flow={"flow": "disputa@1.0.0", "node_id": "confirmar"},
        awaiting="confirmation",
        awaiting_node_id="confirmar",
        actions=[action(flow="disputa@1.0.0", state="proposed")],
    )


def test_run_vencido_se_cierra_abandoned_y_el_turno_recibe_410() -> None:
    w = World()
    proposed_run(w)
    w.clock.advance(timedelta(minutes=31))
    with pytest.raises(EngineError) as exc:
        w.turn("hola")
    assert exc.value.code is ProblemCode.run_closed and exc.value.status == 410
    state = w.saved()
    assert (state.status, state.outcome) == ("closed", Outcome.abandoned)
    assert state.closed_at == w.clock.now() and state.inactive_after is None
    assert state.actions[0].state.value == "cancelled"
    assert state.actions[0].cancel_reason is InvalidationReason.abandoned


def test_el_cierre_se_commitea_antes_de_responder_410_y_libera_el_lease() -> None:
    w = World()
    proposed_run(w)
    w.clock.advance(timedelta(minutes=31))
    with pytest.raises(EngineError):
        w.turn("hola")
    assert w.store.leases == {} and w.saved().state_version == 2
    assert w.uow_factory.commits == 1  # type: ignore[attr-defined]


def test_el_mensaje_del_turno_vencido_no_se_procesa() -> None:
    w = World()
    proposed_run(w)
    w.clock.advance(timedelta(minutes=31))
    with pytest.raises(EngineError):
        w.turn("hola")
    assert w.guards.calls == [] and w.understand.calls == [] and w.recorder.calls == []
    assert w.store.turn_results == {}


def test_eventos_del_abandono_en_orden_con_turn_completed_al_final() -> None:
    w = World()
    proposed_run(w)
    w.clock.advance(timedelta(minutes=31))
    with pytest.raises(EngineError):
        w.turn("hola")
    assert w.event_types() == [
        "turn_started",
        "expiry_evaluated",
        "action_cancelled",
        "run_closed",
        "turn_completed",
    ]
    events = w.events()
    assert events[1].payload.expired is True and events[1].payload.ttl == timedelta(minutes=30)
    assert events[3].payload.closed_by == "abandonment" and events[3].payload.outcome is Outcome.abandoned
    assert events[0].payload.guards is None and events[4].payload.awaiting.value == "none"


def test_el_turno_siguiente_al_abandono_tambien_da_410() -> None:
    w = World()
    proposed_run(w)
    w.clock.advance(timedelta(minutes=31))
    with pytest.raises(EngineError):
        w.turn("hola")
    with pytest.raises(EngineError) as exc:
        w.turn("otra vez")
    assert exc.value.status == 410 and RUN_ID in w.store.runs


def test_expiry_evaluated_expired_false_en_un_turno_normal() -> None:
    w = World()
    w.open_run(active=True)
    w.clock.advance(timedelta(minutes=5))
    w.understand.push(cmd("continue"))
    w.turn("cargo desconocido")
    (event,) = [e for e in w.events() if e.type == "expiry_evaluated"]
    assert event.payload.expired is False and event.payload.now == w.clock.now()
    assert event.payload.ttl == timedelta(minutes=30) and event.turn_id == "turn-0001"


def test_ttl_exacto_no_vence() -> None:
    w = World()
    w.open_run(active=True)
    w.clock.advance(timedelta(minutes=30))  # now − last_activity == ttl: estricto, no vence
    w.understand.push(cmd("continue"))
    w.turn("cargo desconocido")
    assert w.saved().status == "open"
    w.clock.advance(timedelta(minutes=30, microseconds=1))
    with pytest.raises(EngineError):
        w.turn("hola otra vez")
    assert w.saved().outcome is Outcome.abandoned
