"""Paso 4: un turno que encuentra el run vencido lo cierra `abandoned` y responde `410` (P2)."""

from datetime import timedelta

import pytest

from agent_core.domain import EngineError, InvalidationReason, Outcome, ProblemCode
from testing.builders import action
from tests.m04.harness import RUN_ID, World


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
    pytest.skip("Task 11: necesita el turno completo")


def test_ttl_exacto_no_vence() -> None:
    pytest.skip("Task 11: necesita el turno completo")
