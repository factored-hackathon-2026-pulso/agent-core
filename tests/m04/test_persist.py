"""Paso 14: persistir en una transacción por turno (invariante de m04 §4)."""

from decimal import Decimal

import pytest

from agent_core.domain import EncryptedBlob, Message
from agent_core.interpreter import GenerateResult
from testing.fakes.storage import SimulatedCrash
from tests.m04.harness import RUN_ID, World
from tests.m04.helpers import FakeRuntime, cmd


def test_un_turno_es_una_sola_transaccion_de_estado() -> None:
    w = World()
    w.open_run(active=True)
    w.uow_factory.commits = 0  # type: ignore[attr-defined]
    w.understand.push(cmd("out_of_scope"))
    w.turn("hola")
    assert w.uow_factory.commits == 1  # type: ignore[attr-defined]


def test_con_una_escritura_son_tres_commits_uno_de_m4_y_dos_de_m3() -> None:
    w = World()
    prompt = w.seed_at_confirm()
    before = w.saved().state_version
    w.turn_confirm(prompt.token, "yes")
    assert w.uow_factory.commits == 3  # type: ignore[attr-defined]
    assert w.saved().state_version == before + 3  # save_run usó la versión que dejó M3, sin conflicto


def test_una_falla_a_mitad_del_turno_no_commitea_y_el_reintento_lo_rehace() -> None:
    w = World()
    w.open_run(active=True)
    w.understand.push(cmd("out_of_scope"), cmd("out_of_scope"))
    w.store.inject("on_commit")
    with pytest.raises(SimulatedCrash):
        w.turn("hola", client_turn_id="c-1")
    assert w.saved().state_version == 1 and w.event_types() == [] and w.store.turn_results == {}
    assert w.store.leases == {}  # una excepción libera el lease para el reintento
    result = w.turn("hola", client_turn_id="c-1")
    assert result.status == "closed" and w.event_types().count("turn_completed") == 1


def test_add_usage_suma_el_costo_del_turno_en_decimal() -> None:
    w = World()
    w.open_run()
    w.understand.push(cmd("start_flow", flow="generar", cost="0.001"))
    w.responder.push(
        GenerateResult(
            message=Message(kind="generated", text="Listo", locale="es"),
            model_calls=1,
            tokens=50,
            cost_usd=Decimal("0.004"),
        )
    )
    w.turn("resumen")
    ((key, entries),) = w.store.usage.items()
    assert key == w.principal.key and len(entries) == 1
    (_, cost) = entries[0]
    assert isinstance(cost, Decimal) and cost == Decimal("0.005")  # Understand + costo del run del turno


def test_cada_turno_es_un_hit_aunque_no_cueste() -> None:
    w = World()
    w.open_run(active=True)
    w.understand.push(cmd("out_of_scope"))
    w.turn("hola")
    ((_, entries),) = w.store.usage.items()
    assert [c for _, c in entries] == [Decimal("0")]


def test_token_map_sellado_se_persiste_en_el_run(monkeypatch: pytest.MonkeyPatch) -> None:
    blob = EncryptedBlob(kid="k1", nonce="bm9uY2U=", ciphertext="Y2lwaGVy")
    monkeypatch.setattr(FakeRuntime, "sealed_token_map", lambda self: blob)
    w = World()
    w.open_run(active=True)
    w.understand.push(cmd("out_of_scope"))
    w.turn("hola")
    assert w.saved().token_map == blob


def test_el_resultado_por_client_turn_id_se_guarda_en_la_misma_transaccion() -> None:
    w = World()
    w.open_run(active=True)
    w.understand.push(cmd("out_of_scope"))
    result = w.turn("hola", client_turn_id="c-7")
    assert w.store.turn_results[(RUN_ID, "c-7")] == result
    assert result.trace_id == f"trace-{result.turn_id}"


def test_last_activity_e_inactive_after_salen_del_clock() -> None:
    from datetime import timedelta

    w = World()
    w.open_run(active=True)
    w.clock.advance(timedelta(minutes=10))
    w.understand.push(cmd("start_flow", flow="disputa"))
    w.turn("sigo aquí")
    saved = w.saved()
    assert saved.last_activity_at == w.clock.now() and saved.inactive_after == w.clock.now() + timedelta(
        minutes=30
    )
    assert saved.turn_count == 2
