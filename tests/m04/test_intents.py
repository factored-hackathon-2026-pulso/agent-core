"""Paso 10: flow activo, intenciones pendientes y ofertas (T-M4-01, T-M4-14; ADR 0004)."""

from tests.m04.harness import World, flow, node
from tests.m04.helpers import cmd

EMPATE_A = flow(
    "empate-a",
    50,
    node("pedir", "collect", {"slot": "x", "prompt_ref": "t-pedir@1.0.0"}, ok="fin"),
    node("fin", "end", {"outcome": "resolved"}),
)
EMPATE_B = flow(
    "empate-b",
    50,
    node("pedir", "collect", {"slot": "y", "prompt_ref": "t-pedir-tarjeta@1.0.0"}, ok="fin"),
    node("fin", "end", {"outcome": "resolved"}),
)


def with_flow_and_pending(w: World) -> None:
    """Bloquear-tarjeta activo (esperando la tarjeta) con `disputa` pendiente."""
    w.open_run(
        active_flow={"flow": "bloquear-tarjeta@1.0.0", "node_id": "pedir_tarjeta"},
        awaiting="slot",
        awaiting_node_id="pedir_tarjeta",
        pending_intents=[{"flow": "disputa", "priority": 50, "mention_order": 0}],
    )


def test_t_m4_01_arranca_la_de_mayor_prioridad_y_la_otra_queda_pendiente_con_acuse() -> None:
    w = World()
    w.open_run()
    w.understand.push(cmd("start_flow", flow="disputa", additional=("bloquear-tarjeta",)))
    result = w.turn("bloquea mi tarjeta y disputa este cargo")
    state = w.saved()
    assert state.active_flow is not None and state.active_flow.flow.id == "bloquear-tarjeta"  # 80 > 50
    assert [(p.flow, p.priority, p.mention_order) for p in state.pending_intents] == [("disputa", 50, 0)]
    assert [m.text for m in result.messages] == [w.text("t-pedir-tarjeta"), w.text("t-acuse")]
    assert result.awaiting.value == "slot" and state.awaiting_node_id == "pedir_tarjeta"


def test_empate_de_prioridad_desempata_por_orden_de_mencion() -> None:
    for first, second in (("empate-a", "empate-b"), ("empate-b", "empate-a")):
        w = World(extra=(EMPATE_A, EMPATE_B))
        w.open_run()
        w.understand.push(cmd("start_flow", flow=first, additional=(second,)))
        w.turn("las dos cosas")
        state = w.saved()
        assert state.active_flow is not None and state.active_flow.flow.id == first
        assert [(p.flow, p.mention_order) for p in state.pending_intents] == [(second, 1)]


def test_con_flow_activo_la_nueva_intencion_va_a_pendientes() -> None:
    w = World()
    w.open_run(active=True)
    w.understand.push(cmd("start_flow", flow="bloquear-tarjeta"))
    result = w.turn("también bloquea mi tarjeta")
    state = w.saved()
    assert state.active_flow is not None and state.active_flow.flow.id == "disputa"
    assert [(p.flow, p.priority) for p in state.pending_intents] == [("bloquear-tarjeta", 80)]
    assert [m.text for m in result.messages] == [w.text("t-pedir"), w.text("t-acuse")]


def test_la_misma_intencion_que_el_flow_activo_no_se_encola() -> None:
    w = World()
    w.open_run(active=True)
    w.understand.push(cmd("start_flow", flow="disputa"))
    result = w.turn("otra vez la disputa")
    assert w.saved().pending_intents == [] and w.text("t-acuse") not in [m.text for m in result.messages]


def test_continue_reanuda_collect_con_slot_answer_con_el_texto_crudo() -> None:
    w = World()
    w.open_run(active=True)
    w.understand.push(cmd("continue"))
    result = w.turn("cargo desconocido de 50 dólares")
    slot = w.saved().slots["descripcion"]
    assert (slot.value, slot.status) == ("cargo desconocido de 50 dólares", "validated")  # sin "[model]"
    assert result.awaiting.value == "confirmation" and result.confirmation is not None


def test_los_slots_de_understand_entran_claimed() -> None:
    w = World()
    w.open_run()
    w.understand.push(cmd("start_flow", flow="disputa", slots={"descripcion": "cargo raro"}))
    result = w.turn("disputa un cargo raro")
    slot = w.saved().slots["descripcion"]
    assert (slot.value, slot.status) == ("cargo raro", "claimed")
    assert result.awaiting.value == "slot"  # M2 no lo toma por hecho: vuelve a preguntar


def test_un_slot_ya_validado_no_lo_pisa_understand() -> None:
    w = World()
    w.open_run(
        active_flow={"flow": "disputa@1.0.0", "node_id": "pedir"},
        awaiting="slot",
        awaiting_node_id="pedir",
        slots={"descripcion": {"value": "ya validado", "status": "validated", "source_turn": 1}},
    )
    w.understand.push(cmd("start_flow", flow="bloquear-tarjeta", slots={"descripcion": "otro"}))
    w.turn("también bloquea mi tarjeta")
    slot = w.saved().slots["descripcion"]
    assert (slot.value, slot.status) == ("ya validado", "validated")


def test_t_m4_14_al_terminar_el_flow_se_ofrece_la_pendiente_y_run_queda_esperando() -> None:
    w = World()
    with_flow_and_pending(w)
    w.understand.push(cmd("continue"))
    result = w.turn("tarjeta terminada en 4242")
    state = w.saved()
    assert [m.text for m in result.messages] == [w.text("t-oferta")]
    assert (state.status, state.active_flow, state.pending_offer) == ("open", None, "disputa")
    assert state.pending_intents == [] and state.awaiting.value == "input" and state.awaiting_node_id is None
    assert result.awaiting.value == "input" and "run_closed" not in w.event_types()


def test_t_m4_14_la_oferta_solo_arranca_con_affirm() -> None:
    w = World()
    with_flow_and_pending(w)
    w.understand.push(cmd("continue"), cmd("affirm"))
    w.turn("tarjeta terminada en 4242")
    result = w.turn("sí, veamos")
    state = w.saved()
    assert state.active_flow is not None and state.active_flow.flow.id == "disputa"
    assert state.pending_offer is None and [m.text for m in result.messages] == [w.text("t-pedir")]


def test_sin_pendientes_el_flow_que_termina_cierra_el_run() -> None:
    w = World()
    w.open_run(
        active_flow={"flow": "bloquear-tarjeta@1.0.0", "node_id": "pedir_tarjeta"},
        awaiting="slot",
        awaiting_node_id="pedir_tarjeta",
    )
    w.understand.push(cmd("continue"))
    result = w.turn("tarjeta terminada en 4242")
    assert (result.status, result.outcome.value if result.outcome else None) == ("closed", "resolved")


def test_sin_flow_ni_oferta_un_comando_que_no_arranca_nada_pide_aclaracion() -> None:
    w = World()
    w.open_run()
    w.understand.push(cmd("continue"))
    result = w.turn("¿y entonces?")
    assert [m.text for m in result.messages] == [w.text("t-aclarar")]
    assert w.saved().clarifications_used == 1
