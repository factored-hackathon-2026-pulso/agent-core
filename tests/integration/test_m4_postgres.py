"""M4 sobre Postgres real (DoD 2 de m04 §10): bloqueo optimista, `409` con dos conexiones y una sola
transacción de estado por turno."""

from datetime import timedelta

import pytest

from agent_core.audit import AuditLog
from agent_core.domain import EngineError, ProblemCode, VersionConflict
from tests.integration.pg_world import PgWorld
from tests.m04.harness import RUN_ID
from tests.m04.helpers import cmd
from tests.support.pg import postgres_store

pytestmark = pytest.mark.integration


def test_un_turno_es_una_sola_transaccion_de_estado_y_la_cadena_queda_valida() -> None:
    with postgres_store("m4_turn") as pg:
        w = PgWorld(pg)
        w.open_run(active=True)
        w.uow_factory.commits = 0  # type: ignore[attr-defined]
        w.understand.push(cmd("out_of_scope"))
        w.turn("hola")
        assert w.uow_factory.commits == 1  # type: ignore[attr-defined]
        assert w.saved().state_version == 2
        assert "turn_completed" in w.event_types()
        assert AuditLog(w.audit).verify_chain(RUN_ID).ok


def test_con_una_escritura_son_tres_commits_y_el_estado_avanza_sin_conflicto() -> None:
    with postgres_store("m4_turn") as pg:
        w = PgWorld(pg)
        prompt = w.seed_at_confirm()
        before = w.saved().state_version
        w.turn_confirm(prompt.token, "yes")
        assert w.uow_factory.commits == 3  # type: ignore[attr-defined]
        assert w.saved().state_version == before + 3
        assert AuditLog(w.audit).verify_chain(RUN_ID).ok


def test_t_m4_10_turno_concurrente_con_otra_conexion_da_409() -> None:
    with postgres_store("m4_409") as pg:
        w = PgWorld(pg)
        w.open_run()
        with pg.uow() as other:  # otra conexión toma el lease (autocommit: visible sin commit)
            other.acquire_turn(RUN_ID, "turn-otro", w.clock.now(), timedelta(seconds=60))
            with pytest.raises(EngineError) as exc:
                w.turn("hola")
        assert exc.value.code is ProblemCode.turn_in_progress and exc.value.status == 409
        assert w.understand.calls == [] and "turn_completed" not in w.event_types()


def test_dos_turnos_reales_solapados_uno_gana_y_el_otro_recibe_409() -> None:
    with postgres_store("m4_409") as pg:
        w = PgWorld(pg)
        w.open_run(active=True)
        w.understand.push(cmd("out_of_scope"))
        seen: list[EngineError] = []

        def second_turn() -> None:  # corre mientras el primero está dentro de Understand (lease tomado)
            w.understand.hook = None
            with pytest.raises(EngineError) as exc:
                w.turn("otro", client_turn_id="c-otro")
            seen.append(exc.value)

        w.understand.hook = second_turn
        result = w.turn("hola", client_turn_id="c-1")
        assert result.messages and [e.status for e in seen] == [409]
        assert w.saved().state_version == 2  # solo el ganador escribió


def test_lease_vencido_dos_escritores_la_segunda_transaccion_pierde_por_version() -> None:
    """Sin lease vigente la defensa es el bloqueo optimista: el turno rezagado no pisa al que ya commiteó."""
    with postgres_store("m4_lock") as pg:
        w = PgWorld(pg)
        w.open_run(active=True)
        w.understand.push(cmd("out_of_scope"), cmd("out_of_scope"))

        def overtaking_turn() -> None:
            w.understand.hook = None
            w.clock.advance(timedelta(seconds=61))  # el lease del turno lento venció
            w.turn("rápido", client_turn_id="c-rapido")

        w.understand.hook = overtaking_turn
        with pytest.raises(VersionConflict):
            w.turn("lento", client_turn_id="c-lento")
        state = w.saved()
        assert state.state_version == 2  # ganó el rápido
        with pg.uow() as uow:
            assert uow.get_turn_result(RUN_ID, "c-rapido") is not None
            assert uow.get_turn_result(RUN_ID, "c-lento") is None  # nada a medias del perdedor


def test_si_el_transcript_falla_no_persiste_nada_del_turno_y_el_lease_se_libera() -> None:
    with postgres_store("m4_atomic") as pg:
        w = PgWorld(pg)
        w.open_run(active=True)
        w.understand.push(cmd("out_of_scope"))
        w.recorder.fail = RuntimeError("transcript no disponible")
        with pytest.raises(RuntimeError):
            w.turn("hola", client_turn_id="c-1")
        assert w.saved().state_version == 1
        assert w.event_types() == []
        with pg.uow() as uow:
            assert uow.get_turn_result(RUN_ID, "c-1") is None
            uow.acquire_turn(RUN_ID, "turn-siguiente", w.clock.now(), timedelta(seconds=60))  # lease libre
