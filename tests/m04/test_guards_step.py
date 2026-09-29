"""Paso 7: guardas de entrada (M6) dentro del turno (T-M4-13)."""

import pytest

from tests.m04.harness import World
from tests.m04.helpers import too_large, unsupported


def test_t_m4_13_idioma_no_soportado_responde_plantilla_y_no_avanza() -> None:
    w = World()
    w.open_run(active=True)
    before = w.saved().active_flow
    w.guards.result = unsupported("fr")
    result = w.turn("bonjour")
    assert result.messages[0].text == w.text("t-idioma")
    assert w.understand.calls == [] and w.saved().active_flow == before and result.locale == "es"
    assert w.event_types().count("turn_completed") == 1
    assert w.saved().awaiting.value == "slot"  # el run sigue esperando lo mismo


def test_idioma_no_soportado_responde_en_el_default_locale_aunque_el_run_hable_pt() -> None:
    w = World()
    w.open_run(active=True, locale="pt")
    w.guards.result = unsupported("pt")
    result = w.turn("bonjour")
    assert result.messages[0].locale == "es" and result.messages[0].text == w.text("t-idioma", "es")
    assert w.saved().locale == "pt"


def test_tamano_excedido_responde_input_too_large_sin_procesar() -> None:
    w = World()
    w.open_run(active=True)
    w.guards.result = too_large("es")
    result = w.turn("x" * 10)
    assert result.messages[0].text == w.text("t-largo") and w.understand.calls == []
    assert w.saved().active_flow is not None and w.event_types().count("turn_completed") == 1


def test_turn_started_lleva_la_salida_de_m6_y_el_client_turn_id() -> None:
    w = World()
    w.open_run(active=True)
    w.guards.result = unsupported("fr")
    w.turn("bonjour", client_turn_id="c-9")
    started = w.events()[0]
    assert started.type == "turn_started"
    assert started.payload.client_turn_id == "c-9" and started.payload.guards is not None
    assert started.payload.guards.lang.decision == "unsupported" and started.payload.guards.size_ok is True


def test_las_guardas_reciben_solo_el_texto_en_vista_model() -> None:
    w = World()
    w.open_run(active=True)
    w.guards.result = unsupported("fr")
    w.turn("user@example.test", lang="pt")
    assert w.guards.calls == ["[model]user@example.test"]
    assert w.guards.contexts[0]["first_turn"] is False and w.guards.contexts[0]["lang"] == "pt"


def test_la_etapa_de_guardas_se_mide_y_flow_ms_queda_none() -> None:
    from datetime import timedelta

    w = World(guards_advance=timedelta(milliseconds=2))
    w.open_run(active=True)
    w.guards.result = unsupported("fr")
    w.turn("bonjour")
    completed = next(e for e in w.events() if e.type == "turn_completed")
    assert completed.payload.stages.guards_ms == 2 and completed.payload.stages.flow_ms is None
    assert completed.payload.stages.understand_ms is None


def test_injection_marca_modo_degradado_y_lo_pasa_al_interprete() -> None:
    pytest.skip("Task 12: necesita Understand")


def test_injection_flagged_lo_construye_m6_y_lo_agrega_m4() -> None:
    pytest.skip("Task 12: necesita Understand")


def test_locale_del_run_sigue_la_decision_de_m6() -> None:
    pytest.skip("Task 12: necesita Understand")
