"""Barrido de inactividad (m04 §3.6; T-M4-07) y `agentcore sweep`."""

from datetime import timedelta
from typing import Any

import pytest

from agent_core.cli import main
from agent_core.domain import InvalidationReason, Outcome
from agent_core.turn import SweepReport, TurnConfig
from testing.builders import action, run_state
from tests.m04.harness import AGENT_REF, RELEASE_ID, RUN_ID, World


def proposed_run(w: World) -> None:
    w.open_run(
        active_flow={"flow": "disputa@1.0.0", "node_id": "confirmar"},
        awaiting="confirmation",
        awaiting_node_id="confirmar",
        actions=[action(flow="disputa@1.0.0", state="proposed")],
    )


def test_t_m4_07_inactividad_cierra_abandoned_con_acciones_canceladas_y_expiry_evaluated() -> None:
    w = World()
    proposed_run(w)
    w.clock.advance(timedelta(minutes=31))
    report = w.engine.sweep(w.clock.now())
    state = w.saved()
    assert report == SweepReport(evaluated=1, abandoned=1, skipped=0)
    assert (state.status, state.outcome) == ("closed", Outcome.abandoned) and state.inactive_after is None
    assert state.actions[0].state.value == "cancelled"
    assert state.actions[0].cancel_reason is InvalidationReason.abandoned
    types = w.event_types()
    assert types == ["expiry_evaluated", "action_cancelled", "run_closed"]  # sin turn_completed (§3.7)
    events = w.events()
    assert events[0].payload.expired is True and events[0].payload.now == w.clock.now()
    assert events[2].payload.closed_by == "abandonment" and w.store.leases == {}


def test_un_run_activo_no_se_cierra_y_no_hay_expiry_evaluated() -> None:
    w = World()
    proposed_run(w)
    w.clock.advance(timedelta(minutes=10))
    assert w.engine.sweep(w.clock.now()) == SweepReport(evaluated=0, abandoned=0, skipped=0)
    assert w.saved().status == "open" and w.event_types() == []


def test_un_turno_en_curso_gana_al_barrido() -> None:
    w = World()
    proposed_run(w)
    with w.store.uow() as other:
        other.acquire_turn(RUN_ID, "turn-vivo", w.clock.now(), timedelta(minutes=45))
        other.commit()
    w.clock.advance(timedelta(minutes=31))
    report = w.engine.sweep(w.clock.now())
    assert report == SweepReport(evaluated=0, abandoned=0, skipped=1)
    assert w.saved().status == "open" and w.store.leases[RUN_ID].turn_id == "turn-vivo"


def seed_many(w: World, n: int) -> None:
    for i in range(n):
        state = run_state(
            run_id=f"run-{i + 10:04d}",
            session_id=f"session-{i + 10:04d}",
            release=RELEASE_ID,
            agent=AGENT_REF,
            inactive_after=w.clock.now() + timedelta(minutes=30),
        )
        with w.store.uow() as uow:
            uow.save_run(state, 0)
            uow.commit()


def test_el_barrido_procesa_mas_de_un_lote() -> None:
    w = World(config=TurnConfig(sweep_batch=2))
    seed_many(w, 5)
    w.clock.advance(timedelta(minutes=31))
    report = w.engine.sweep(w.clock.now())
    assert report == SweepReport(evaluated=5, abandoned=5, skipped=0)
    assert all(s.status == "closed" for s in w.store.runs.values())


def test_los_saltados_no_hacen_un_bucle_infinito_ni_bloquean_al_resto() -> None:
    w = World(config=TurnConfig(sweep_batch=2))
    seed_many(w, 4)
    with w.store.uow() as other:
        for rid in ("run-0010", "run-0011", "run-0012"):
            other.acquire_turn(rid, "turn-vivo", w.clock.now(), timedelta(minutes=45))
        other.commit()
    w.clock.advance(timedelta(minutes=31))
    assert w.engine.sweep(w.clock.now()) == SweepReport(evaluated=1, abandoned=1, skipped=3)


def test_sweep_es_idempotente() -> None:
    w = World()
    proposed_run(w)
    w.clock.advance(timedelta(minutes=31))
    w.engine.sweep(w.clock.now())
    events = len(w.events())
    assert w.engine.sweep(w.clock.now()) == SweepReport(0, 0, 0) and len(w.events()) == events


def test_solo_los_runs_open_con_inactive_after_vencido() -> None:
    w = World()
    w.open_run(status="closed", outcome="resolved", closed_at=w.clock.now(), inactive_after=None)
    w.clock.advance(timedelta(hours=2))
    assert w.engine.sweep(w.clock.now()) == SweepReport(0, 0, 0)


def test_el_barrido_no_necesita_understand_guardas_ni_runtime() -> None:
    w = World()
    proposed_run(w)
    w.clock.advance(timedelta(minutes=31))
    w.engine.sweep(w.clock.now())
    assert w.understand.calls == [] and w.guards.calls == [] and w.runtimes.opened == 0


class FakeSweeper:
    def __init__(self) -> None:
        self.called: list[Any] = []

    def sweep(self, now: Any) -> SweepReport:
        self.called.append(now)
        return SweepReport(evaluated=3, abandoned=2, skipped=1)


def test_agentcore_sweep_imprime_una_linea_sin_datos_de_runs(capsys: pytest.CaptureFixture[str]) -> None:
    w = World()
    sweeper = FakeSweeper()
    code = main(["sweep", "--once"], sweeper=sweeper, clock=w.clock)
    assert code == 0 and sweeper.called == [w.clock.now()]
    assert capsys.readouterr().out.strip() == "evaluated=3 abandoned=2 skipped=1"


def test_agentcore_sweep_sin_dsn_ni_sweeper_avisa_que_falta_postgres(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["sweep"]) == 2
    assert "--dsn" in capsys.readouterr().err


def test_el_cierre_del_barrido_es_igual_al_de_un_turno_que_encuentra_el_run_vencido() -> None:
    """Un solo cierre `abandoned`: el barrido y el paso 4 dejan exactamente el mismo estado."""
    from agent_core.domain import EngineError

    swept, turned = World(), World()
    for w in (swept, turned):
        proposed_run(w)
        w.clock.advance(timedelta(minutes=31))
    swept.engine.sweep(swept.clock.now())
    with pytest.raises(EngineError):
        turned.turn("hola")
    # El barrido no es un turno: no cuenta `turn_count` ni abre presupuestos por turno.
    skip = {"turn_count", "budgets_used", "state_version"}
    a = swept.saved().model_dump(mode="json", exclude=skip)
    b = turned.saved().model_dump(mode="json", exclude=skip)
    assert a == b
    assert a["last_activity_at"] == a["closed_at"]
