"""Pasos 1-3 de `handle_turn`: deduplicación, lease, `410` y release revocada (T-M4-09, 10, 11)."""

from datetime import timedelta
from typing import Any

import pytest

from agent_core.domain import EngineError, Outcome, ProblemCode, TurnResult
from testing.fakes.storage import InMemoryUoW
from tests.m03.harness import ProcessDied
from tests.m04.harness import RELEASE_ID, RUN_ID, World


def revoked_world() -> World:
    w = World()
    w.open_run(active=True)
    w.registry.revoke(RELEASE_ID)
    return w


def test_t_m4_10_turnos_concurrentes_dan_409() -> None:
    w = World()
    w.open_run()
    with w.store.uow() as other:
        other.acquire_turn(RUN_ID, "turn-otro", w.clock.now(), timedelta(seconds=60))
    with pytest.raises(EngineError) as exc:
        w.turn("hola")
    assert exc.value.code is ProblemCode.turn_in_progress and exc.value.status == 409
    assert w.understand.calls == [] and w.guards.calls == []
    assert "turn_completed" not in w.event_types()


def test_el_lease_vencido_no_bloquea() -> None:
    w = revoked_world()
    with w.store.uow() as other:
        other.acquire_turn(RUN_ID, "turn-viejo", w.clock.now(), timedelta(seconds=60))
        other.commit()
    w.clock.advance(timedelta(seconds=61))
    assert w.turn("hola").status == "escalated"


def test_sesion_desconocida_es_404() -> None:
    w = World()
    with pytest.raises(EngineError) as exc:
        w.turn("hola")
    assert exc.value.code is ProblemCode.not_found


def test_t_m4_11_client_turn_id_repetido_no_reprocesa() -> None:
    w = revoked_world()
    first = w.turn("hola", client_turn_id="c-1")
    events, opened = len(w.events()), w.runtimes.opened
    again = w.turn("hola", client_turn_id="c-1")
    assert again == first and len(w.events()) == events and w.runtimes.opened == opened
    assert w.store.leases == {}


def test_duplicado_de_un_turno_que_cerro_el_run_devuelve_el_resultado_y_no_410() -> None:
    w = revoked_world()
    first = w.turn("hola", client_turn_id="c-1")
    assert w.saved().status == "escalated"
    assert w.turn("hola", client_turn_id="c-1") == first
    with pytest.raises(EngineError) as exc:
        w.turn("otra", client_turn_id="c-2")
    assert exc.value.status == 410


def test_dedupe_tras_el_lease_cierra_la_carrera(monkeypatch: pytest.MonkeyPatch) -> None:
    w = World()
    w.open_run()
    stored = TurnResult(
        run_id=RUN_ID,
        turn_id="turn-otro",
        messages=[],
        locale="es",
        awaiting="none",  # type: ignore[arg-type]
        status="open",
        trace_id="trace-x",
    )
    original = InMemoryUoW.acquire_turn

    def racing(self: InMemoryUoW, *args: Any, **kwargs: Any) -> None:
        w.store.turn_results[(RUN_ID, "c-race")] = stored  # el otro turno commiteó justo antes
        original(self, *args, **kwargs)

    monkeypatch.setattr(InMemoryUoW, "acquire_turn", racing)
    assert w.turn("hola", client_turn_id="c-race") == stored
    assert w.store.leases == {} and w.guards.calls == [] and w.uow_factory.commits == 1  # type: ignore[attr-defined]


def test_run_cerrado_da_410_y_libera_el_lease() -> None:
    w = World()
    w.open_run(status="closed", outcome="resolved", closed_at=w.clock.now(), inactive_after=None)
    with pytest.raises(EngineError) as exc:
        w.turn("hola")
    assert exc.value.code is ProblemCode.run_closed and exc.value.status == 410
    assert w.store.leases == {} and "turn_completed" not in w.event_types()


def test_t_m4_09_release_revocada_escala_sin_ejecutar_nodos() -> None:
    w = revoked_world()
    result = w.turn("hola")
    assert result.status == "escalated" and w.understand.calls == [] and w.guards.calls == []
    assert not [e for e in w.events() if e.type in ("node_entered", "command_emitted")]
    assert [e.payload.reason_code for e in w.events() if e.type == "escalated"] == ["release_revoked"]
    assert w.saved().outcome is Outcome.escalated and result.handoff_ref == w.saved().handoff_ref
    closed = [e for e in w.events() if e.type == "run_closed"]
    assert [e.payload.closed_by for e in closed] == ["revocation"]


def test_una_excepcion_libera_el_lease_para_el_reintento() -> None:
    w = revoked_world()
    w.recorder.fail = RuntimeError("boom")
    with pytest.raises(RuntimeError):
        w.turn("hola", client_turn_id="c-1")
    assert w.store.leases == {} and w.saved().state_version == 1 and w.saved().status == "open"
    w.recorder.fail = None
    assert w.turn("hola", client_turn_id="c-1").status == "escalated"


def test_una_caida_simulada_no_libera_el_lease() -> None:
    w = revoked_world()
    w.recorder.fail = ProcessDied()  # type: ignore[assignment]
    with pytest.raises(ProcessDied):
        w.turn("hola")
    assert RUN_ID in w.store.leases and w.saved().state_version == 1


def test_el_texto_crudo_nunca_llega_a_guardas_ni_a_understand() -> None:
    from tests.m04.helpers import cmd

    w = World()
    w.open_run(active=True)
    w.understand.push(cmd("out_of_scope"))
    w.turn("mi correo es user@example.test")
    assert w.guards.calls == ["[model]mi correo es user@example.test"]
    assert [c.text_model for c in w.understand.calls] == w.guards.calls
    assert w.recorder.calls[0][2] == w.guards.calls[0]


def test_run_cerrado_con_lease_ajeno_da_410_y_no_409() -> None:
    w = World()
    w.open_run(status="closed", outcome="resolved", closed_at=w.clock.now(), inactive_after=None)
    with w.store.uow() as other:
        other.acquire_turn(RUN_ID, "turn-otro", w.clock.now(), timedelta(seconds=60))
    with pytest.raises(EngineError) as exc:
        w.turn("hola")
    assert exc.value.code is ProblemCode.run_closed and exc.value.status == 410
