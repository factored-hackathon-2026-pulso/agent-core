"""Rama `deny` y respuestas ajenas a la oferta de una intención pendiente (P1, decidida por el usuario)."""

from tests.m04.harness import World
from tests.m04.helpers import cmd


def offering(w: World, *pending: tuple[str, int]) -> None:
    w.open_run(
        pending_offer="disputa",
        awaiting="input",
        pending_intents=[
            {"flow": flow_id, "priority": priority, "mention_order": i}
            for i, (flow_id, priority) in enumerate(pending)
        ],
    )


def test_p1_deny_descarta_la_oferta_y_ofrece_la_siguiente() -> None:
    w = World()
    offering(w, ("bloquear-tarjeta", 80))
    w.understand.push(cmd("deny"))
    result = w.turn("no, esa no")
    state = w.saved()
    assert [m.text for m in result.messages] == [w.text("t-oferta")]
    assert (state.pending_offer, state.pending_intents, state.status) == ("bloquear-tarjeta", [], "open")
    assert result.awaiting.value == "input"


def test_p1_deny_de_la_ultima_no_ofrece_nada_y_vuelve_la_conversacion_normal() -> None:
    w = World()
    offering(w)
    w.understand.push(cmd("deny"))
    result = w.turn("no gracias")
    state = w.saved()
    assert [m.text for m in result.messages] == [w.text("t-aclarar")]  # plantilla del agente
    assert (state.pending_offer, state.status, state.awaiting.value) == (None, "open", "none")
    types = w.event_types()
    assert state.outcome is None and "escalated" not in types and "run_closed" not in types
    assert (state.clarifications_used, state.repair_turns_used) == (0, 0)  # el deny no es reparación


def test_p1_respuesta_ajena_mantiene_la_oferta_una_vez_mas_y_suma_reparacion() -> None:
    w = World()
    offering(w)
    w.understand.push(cmd("continue"))
    result = w.turn("mmm")
    state = w.saved()
    assert [m.text for m in result.messages] == [w.text("t-oferta")]
    assert state.pending_offer == "disputa" and state.repair_turns_used == 1
    assert state.awaiting.value == "input" and state.node_attempts == {"offer:disputa": 1}


def test_p1_la_segunda_respuesta_ajena_descarta_la_oferta() -> None:
    w = World()
    offering(w, ("bloquear-tarjeta", 80))
    w.understand.push(cmd("continue"), cmd("continue"), cmd("continue"), cmd("continue"))
    w.turn("mmm")
    second = w.turn("mmm otra vez")  # descarta `disputa` y ofrece la siguiente
    state = w.saved()
    assert state.pending_offer == "bloquear-tarjeta" and state.repair_turns_used == 2
    assert [m.text for m in second.messages] == [w.text("t-oferta")] and state.node_attempts == {}
    w.turn("mmm")
    last = w.turn("mmm")  # la última se descarta y no queda nada: conversación normal
    state = w.saved()
    assert state.pending_offer is None and state.status == "open" and state.repair_turns_used == 4
    assert [m.text for m in last.messages] == [w.text("t-aclarar")]


def test_p1_affirm_despues_de_una_respuesta_ajena_todavia_arranca_la_oferta() -> None:
    w = World()
    offering(w)
    w.understand.push(cmd("continue"), cmd("affirm"))
    w.turn("mmm")
    result = w.turn("sí")
    assert w.saved().active_flow is not None and [m.text for m in result.messages] == [w.text("t-pedir")]
    assert w.saved().node_attempts == {}


def test_p1_affirm_bajo_umbral_pide_aclaracion_y_conserva_la_oferta() -> None:
    w = World()
    offering(w)
    w.understand.push(cmd("affirm", above={"command": False}))
    result = w.turn("quizá")  # el manejador de aclaración (bajo umbral) tiene precedencia
    assert [m.text for m in result.messages] == [w.text("t-aclarar")] and w.saved().pending_offer == "disputa"
