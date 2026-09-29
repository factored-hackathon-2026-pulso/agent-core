"""Pasos 8-9: Understand y manejadores globales sin `confirm` pendiente (T-M4-03, T-M4-08 parte)."""

from typing import Any

import pytest

from agent_core.domain import InvalidationReason, Outcome, Policy
from testing.builders import action
from tests.m04.harness import World
from tests.m04.helpers import cmd


def at_confirm_by_hand(w: World, **over: Any) -> None:
    """Run esperando confirmación con una acción `proposed` (equivale a `at_confirm` sin recorrer turnos)."""
    w.open_run(
        active_flow={"flow": "disputa@1.0.0", "node_id": "confirmar"},
        awaiting="confirmation",
        awaiting_node_id="confirmar",
        actions=[action(flow="disputa@1.0.0", state="proposed")],
        **over,
    )


def reasons(w: World) -> list[str]:
    return [e.payload.reason_code for e in w.events() if e.type == "escalated"]


def policy_world(*ids_and_exprs: tuple[str, Any, str]) -> World:
    """Interrupciones por `signal_policy` (sin comando): `(id de interrupción, expr, acción)`."""
    policies = tuple(
        Policy(id=f"p-{iid}", version="1.0.0", owner="test", expr=expr, rationale="prueba")
        for iid, expr, _ in ids_and_exprs
    )
    actions = {
        "escalate": {"type": "escalate", "target_queue": "fraude", "priority": "critical"},
        "start_flow": {"type": "start_flow", "flow": "bloquear-tarjeta@1.0.0"},
    }
    interrupts = [
        {
            "id": iid,
            "priority": 100 - 10 * i,
            "action": actions[kind],
            "signal_policy": f"p-{iid}@1.0.0",
        }
        for i, (iid, _, kind) in enumerate(ids_and_exprs)
    ]
    return World(extra=policies, interrupts=interrupts)


def test_t_m4_03_interrupcion_de_fraude_invalida_acciones_y_escala() -> None:
    w = World()
    at_confirm_by_hand(w)
    w.understand.push(cmd("interrupt", interrupt="fraude"))
    result = w.turn("me robaron la tarjeta")
    assert result.status == "escalated"
    saved = w.saved()
    assert saved.actions[0].state.value == "cancelled"
    assert saved.actions[0].cancel_reason is InvalidationReason.interrupt  # antes de escalar
    assert reasons(w) == ["interrupt:fraude"]
    types = w.event_types()
    assert types.index("action_cancelled") < types.index("escalated") < types.index("run_closed")


def test_interrupcion_start_flow_reemplaza_el_flow_activo_y_lo_deja_pendiente() -> None:
    w = World()
    w.open_run(active=True)
    w.understand.push(cmd("interrupt", interrupt="reemplazo"))
    result = w.turn("mejor bloquea mi tarjeta")
    saved = w.saved()
    assert saved.active_flow is not None and saved.active_flow.flow.id == "bloquear-tarjeta"
    assert [(p.flow, p.priority, p.mention_order) for p in saved.pending_intents] == [("disputa", 50, 0)]
    assert [m.text for m in result.messages] == [w.text("t-pedir-tarjeta")]
    assert result.awaiting.value == "slot" and saved.status == "open"


def test_interrupcion_por_signal_policy_sin_comando() -> None:
    w = policy_world(("fraude", {"in": ["robaron", {"var": "message.text"}]}, "escalate"))
    w.open_run(active=True)
    w.understand.push(cmd("continue"))
    assert w.turn("me robaron la tarjeta").status == "escalated"
    assert reasons(w) == ["interrupt:fraude"]


def test_la_signal_policy_que_no_dispara_no_interrumpe() -> None:
    w = policy_world(("fraude", {"in": ["robaron", {"var": "message.text"}]}, "escalate"))
    w.open_run(active=True)
    w.understand.push(cmd("out_of_scope"))
    result = w.turn("hola")
    assert result.outcome is Outcome.abstained and reasons(w) == []


def test_la_de_mayor_prioridad_gana() -> None:
    always = {"==": [1, 1]}
    w = policy_world(("reemplazo", always, "start_flow"), ("fraude", always, "escalate"))
    w.open_run(active=True)
    w.understand.push(cmd("continue"))
    w.turn("hola")
    # `reemplazo` tiene prioridad 100 (primero de la lista): arranca el flow y no escala
    assert w.saved().status == "open" and reasons(w) == []


def test_cancel_cierra_el_flow_e_invalida_acciones() -> None:
    w = World()
    at_confirm_by_hand(w)
    w.understand.push(cmd("cancel"))
    result = w.turn("mejor no")
    saved = w.saved()
    assert saved.status == "open" and saved.active_flow is None and saved.awaiting.value == "none"
    assert saved.actions[0].state.value == "cancelled"
    assert saved.actions[0].cancel_reason is InvalidationReason.cancel
    assert result.messages == [] and result.awaiting.value == "none"


def test_handoff_escala_customer_request_y_emite_handoff_created() -> None:
    w = World()
    w.open_run(active=True)
    w.understand.push(cmd("handoff"))
    result = w.turn("quiero un asesor")
    assert result.status == "escalated" and reasons(w) == ["customer_request"]
    assert [m.type for m in w.outbox().pending(10)] == ["handoff_created"]
    assert [m.text for m in result.messages] == [w.text("t-traspaso")]


def test_out_of_scope_responde_abstencion_y_cierra_abstained() -> None:
    w = World()
    w.open_run(active=True)
    w.understand.push(cmd("out_of_scope"))
    result = w.turn("¿qué hora es en Tokio?")
    assert [m.text for m in result.messages] == [w.text("t-abstencion")]
    assert (result.status, result.outcome) == ("closed", Outcome.abstained)
    closed = [e for e in w.events() if e.type == "run_closed"]
    assert [e.payload.closed_by for e in closed] == ["flow"]


def test_command_emitted_se_registra_una_vez_por_turno_con_understand() -> None:
    w = World()
    w.open_run(active=True)
    w.understand.push(cmd("out_of_scope"))
    w.turn("hola")
    emitted = [e for e in w.events() if e.type == "command_emitted"]
    assert len(emitted) == 1 and emitted[0].payload.source == "understand"
    assert emitted[0].payload.decision_id == "decision-x"
    types = w.event_types()
    assert types.index("expiry_evaluated") < types.index("command_emitted") < types.index("run_closed")


def test_understand_recibe_solo_el_texto_en_vista_model_y_el_contexto_del_run() -> None:
    w = World()
    w.open_run(active=True)
    w.understand.push(cmd("out_of_scope"))
    w.turn("user@example.test")
    request = w.understand.calls[0]
    assert request.text_model == "[model]user@example.test" and request.locale == "es"
    assert request.awaiting_confirmation is False and request.current_node == "pedir"


def test_el_costo_de_understand_se_suma_al_uso_del_principal() -> None:
    from decimal import Decimal

    w = World()
    w.open_run(active=True)
    w.understand.push(cmd("out_of_scope", cost="0.001"))
    w.turn("hola")
    spent = sum((c for _, c in next(iter(w.store.usage.values()))), Decimal(0))
    assert spent == Decimal("0.001")


@pytest.mark.parametrize("stage", ["understand_ms"])
def test_understand_se_mide_por_etapa(stage: str) -> None:
    from datetime import timedelta

    w = World(understand_advance=timedelta(milliseconds=30))
    w.open_run(active=True)
    w.understand.push(cmd("out_of_scope"))
    w.turn("hola")
    completed = next(e for e in w.events() if e.type == "turn_completed")
    assert getattr(completed.payload.stages, stage) == 30
