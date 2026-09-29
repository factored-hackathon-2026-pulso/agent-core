"""Pasos 5-6: recuperación de acciones en `executing` y tokens vencidos (T-M4-12)."""

import pytest

from agent_core.domain import EntityRef, Flow, RunState
from agent_core.turn.recovery import position_at_verify
from testing.builders import action
from tests.m04.harness import RUN_ID, World

WRITE_REF = EntityRef(id="radicar_pqr", version="1.0.0")


def crashed_in_write(w: World) -> None:
    """Estado tras un `after_call`: acción `executing` (commit 1) y la escritura ya aplicada en el backend."""
    w.open_run(
        active_flow={"flow": "disputa@1.0.0", "node_id": "radicar"},
        actions=[action(flow="disputa@1.0.0", state="executing", idempotency_key="action-0001")],
    )
    w.tools.effects[WRITE_REF] = {"action-0001": {"status": "Open", "pqr_id": "pqr-demo-1"}}


def test_position_at_verify_va_al_next_uncertain_del_nodo_de_escritura() -> None:
    w = World()
    crashed_in_write(w)
    state: RunState = w.saved()
    flow = w.registry.get(EntityRef(id="disputa", version="1.0.0"), Flow)
    moved = position_at_verify(state, ["action-0001"], flow)
    assert moved.active_flow is not None and moved.active_flow.node_id == "verificar"


def test_position_at_verify_sin_nodo_de_escritura_para_la_accion_es_error() -> None:
    w = World()
    crashed_in_write(w)
    flow = w.registry.get(EntityRef(id="bloquear-tarjeta", version="1.0.0"), Flow)
    with pytest.raises(LookupError):
        position_at_verify(w.saved(), ["action-0001"], flow)


def test_t_m4_12_accion_executing_va_a_verify_antes_del_mensaje() -> None:
    w = World()
    crashed_in_write(w)
    result = w.turn("¿ya quedó?")
    assert w.write_calls() == []  # nunca se re-ejecuta (ADR 0007 §4)
    types = w.event_types()
    assert "action_verified" in types and types.index("action_verified") < types.index("turn_completed")
    assert w.saved().actions[0].state.value == "verified"
    assert w.guards.calls == [] and w.understand.calls == []  # el flow terminó: el turno acaba ahí (C9)
    assert result.status == "closed" and w.saved().actions[0].state.value == "verified"


def test_recuperacion_que_termina_el_flow_acaba_el_turno() -> None:
    w = World()
    crashed_in_write(w)
    result = w.turn("¿ya quedó?")
    assert (result.status, result.outcome.value if result.outcome else None) == ("closed", "resolved")
    assert [m.text for m in result.messages] == [w.text("t-listo")]
    assert w.saved().pending_offer is None and RUN_ID in w.store.runs


@pytest.mark.skip(
    reason="AMBIGUO (reportado): tras expire_tokens el confirm queda sin acción proposed y M3 lanza "
    "IllegalTransition ante cualquier confirm_answer; el spec no dice qué hace el turno. Ver informe."
)
def test_token_vencido_cancela_la_propuesta_y_emite_action_cancelled() -> None:
    from datetime import timedelta

    from tests.m04.helpers import cmd

    w = World()
    prompt = w.seed_at_confirm()
    w.clock.advance(timedelta(minutes=20))  # el token vence (M3) sin llegar al TTL de inactividad
    w.understand.push(cmd("affirm"))
    w.turn("sí")
    types = w.event_types()
    assert "action_cancelled" in types and w.write_calls() == []  # un token vencido nunca confirma
    cancelled = [e for e in w.events() if e.type == "action_cancelled"]
    assert cancelled[0].payload.reason.value == "token_expired"
    assert types.index("expiry_evaluated") < types.index("action_cancelled") < types.index("command_emitted")
    assert prompt.token


def test_sin_acciones_pendientes_no_hay_recuperacion() -> None:
    from tests.m04.helpers import cmd

    w = World()
    w.open_run(active=True)
    w.understand.push(cmd("continue"))
    w.turn("cargo desconocido")
    assert "action_verified" not in w.event_types() and w.write_calls() == []


def test_recuperacion_que_deja_al_usuario_esperando_procesa_el_mensaje() -> None:
    from tests.m04.helpers import cmd

    w = World()
    w.open_run(
        active_flow={"flow": "disputa-larga@1.0.0", "node_id": "radicar"},
        actions=[action(flow="disputa-larga@1.0.0", state="executing", idempotency_key="action-0001")],
    )
    w.tools.effects[WRITE_REF] = {"action-0001": {"status": "Open", "pqr_id": "pqr-demo-1"}}
    w.understand.push(cmd("continue"))
    result = w.turn("¿ya quedó?")
    assert w.write_calls() == [] and w.saved().actions[0].state.value == "verified"
    assert w.guards.calls == ["[model]¿ya quedó?"] and len(w.understand.calls) == 1  # el mensaje se procesó
    types = w.event_types()
    assert types.index("action_verified") < types.index("command_emitted")
    assert [m.text for m in result.messages] == [w.text("t-listo")]  # el de la recuperación
    assert result.status == "closed"
