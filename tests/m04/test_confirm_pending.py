"""Manejadores con `confirm` pendiente y botón (T-M4-02, 04, 05; ADR 0007)."""

import pytest

from agent_core.domain import InvalidationReason, Outcome
from tests.m04.harness import World
from tests.m04.helpers import cmd


def escalated_reasons(w: World) -> list[str]:
    return [e.payload.reason_code for e in w.events() if e.type == "escalated"]


def test_t_m4_02_intencion_nueva_en_confirm_es_pendiente_mas_unclear() -> None:
    w = World()
    w.seed_at_confirm()
    w.understand.push(cmd("start_flow", flow="bloquear-tarjeta"))
    result = w.turn("también bloquea mi tarjeta")
    saved = w.saved()
    assert [p.flow for p in saved.pending_intents] == ["bloquear-tarjeta"]
    assert saved.actions[0].state.value == "proposed"  # una intención pendiente no invalida la acción
    assert any(m.text == w.text("t-acuse") for m in result.messages)
    assert result.messages[-1].text == w.text("t-acuse")  # el acuse va después de los mensajes del flow
    assert result.awaiting.value == "confirmation" and result.confirmation is not None
    assert saved.repair_turns_used == 1 and saved.node_attempts["confirmar"] == 1  # unclear de texto


def test_additional_flows_con_confirm_pendiente_tambien_van_a_pendientes() -> None:
    w = World()
    w.seed_at_confirm()
    w.understand.push(cmd("continue", additional=("bloquear-tarjeta",)))
    w.turn("y también bloquea mi tarjeta")
    assert [p.flow for p in w.saved().pending_intents] == ["bloquear-tarjeta"]


@pytest.mark.parametrize(
    ("command", "extra", "effect"),
    [
        ("cancel", {}, "cancelled"),
        ("handoff", {}, "escalated"),
        ("interrupt", {"interrupt": "fraude"}, "escalated"),
    ],
)
def test_t_m4_04_cancel_handoff_e_interrupcion_si_aplican_con_confirm(
    command: str, extra: dict[str, str], effect: str
) -> None:
    w = World()
    w.seed_at_confirm()
    w.understand.push(cmd(command, **extra))
    w.turn("lo que sea")
    saved = w.saved()
    assert saved.actions[0].state.value == "cancelled"
    assert (saved.status == "escalated") is (effect == "escalated")
    if command == "cancel":
        assert saved.status == "open" and saved.actions[0].cancel_reason is InvalidationReason.cancel


def test_t_m4_04_out_of_scope_da_unclear_y_no_cierra() -> None:
    w = World()
    w.seed_at_confirm()
    w.understand.push(cmd("out_of_scope"))
    w.turn("¿y el clima?")
    saved = w.saved()
    assert saved.status == "open" and saved.actions[0].state.value == "proposed"
    assert saved.repair_turns_used == 1 and "run_closed" not in w.event_types()


def test_clarify_con_confirm_pendiente_es_unclear_y_no_gasta_aclaraciones() -> None:
    w = World()
    w.seed_at_confirm()
    w.understand.push(cmd("clarify"))
    w.turn("mmm")
    saved = w.saved()
    assert saved.clarifications_used == 0 and saved.repair_turns_used == 1 and saved.status == "open"


def test_t_m4_05_boton_no_pasa_por_understand_y_no_da_unclear() -> None:
    w = World()
    prompt = w.seed_at_confirm()
    result = w.turn_confirm(prompt.token, "yes")
    assert w.understand.calls == [] and w.saved().repair_turns_used == 0
    emitted = [e for e in w.events() if e.type == "command_emitted"]
    assert [e.payload.source for e in emitted] == ["button"]
    assert emitted[0].payload.command.value == "affirm" and emitted[0].payload.decision_id is None
    assert w.saved().actions[0].state.value == "verified" and len(w.write_calls()) == 1
    assert (result.status, result.outcome) == ("closed", Outcome.resolved)
    turn_completed = [e for e in w.events() if e.type == "turn_completed"][-1]
    assert turn_completed.payload.stages.understand_ms is None


def test_boton_no_cancela_la_accion() -> None:
    w = World()
    prompt = w.seed_at_confirm()
    result = w.turn_confirm(prompt.token, "no")
    assert w.write_calls() == [] and w.saved().actions[0].state.value == "cancelled"
    assert w.saved().actions[0].cancel_reason is InvalidationReason.denied_by_user
    assert (result.status, result.outcome) == ("closed", Outcome.cancelled)
    assert [e.payload.command.value for e in w.events() if e.type == "command_emitted"] == ["deny"]


def test_affirm_sobre_umbral_confirma_por_texto() -> None:
    w = World()
    w.seed_at_confirm()
    w.understand.push(cmd("affirm"))
    result = w.turn("sí, adelante")
    assert len(w.write_calls()) == 1 and result.outcome is Outcome.resolved


def test_deny_sobre_umbral_cancela_por_texto() -> None:
    w = World()
    w.seed_at_confirm()
    w.understand.push(cmd("deny"))
    result = w.turn("no gracias")
    assert w.write_calls() == [] and result.outcome is Outcome.cancelled


def test_affirm_bajo_umbral_da_unclear() -> None:
    w = World()
    w.seed_at_confirm()
    w.understand.push(cmd("affirm", above={"command": False}))
    w.turn("quizá")
    saved = w.saved()
    assert (
        w.write_calls() == [] and saved.actions[0].state.value == "proposed" and saved.repair_turns_used == 1
    )


def test_boton_con_token_rotado_no_suma_reparacion() -> None:
    w = World()
    first = w.seed_at_confirm()
    w.understand.push(cmd("out_of_scope"))
    w.turn("¿y el clima?")  # unclear de texto: rota el token
    assert w.saved().repair_turns_used == 1
    result = w.turn_confirm(first.token, "yes")  # token viejo → M3 devuelve unclear sin intento
    assert w.saved().repair_turns_used == 1 and w.write_calls() == []
    assert result.awaiting.value == "confirmation"
    assert w.saved().actions[0].state.value == "proposed"


def test_t_m4_15_clarify_agotado_end_clarify_exhausted() -> None:
    w = World(agent_over={"max_clarifications": 1, "on_clarify_exhausted": "end"})
    w.open_run(active=True)
    w.understand.push(cmd("clarify"), cmd("clarify"))
    first = w.turn("mmm")
    assert [m.text for m in first.messages] == [w.text("t-aclarar")] and first.status == "open"
    second = w.turn("mmm")
    assert (second.status, second.outcome) == ("closed", Outcome.clarify_exhausted)
    closed = [e for e in w.events() if e.type == "run_closed"]
    assert [e.payload.closed_by for e in closed] == ["flow"]


def test_t_m4_15_clarify_agotado_escalate_low_confidence() -> None:
    w = World(agent_over={"max_clarifications": 1, "on_clarify_exhausted": "escalate"})
    w.open_run(active=True)
    w.understand.push(cmd("clarify"), cmd("clarify"))
    w.turn("mmm")
    assert w.turn("mmm").status == "escalated"
    assert escalated_reasons(w) == ["low_confidence"]


@pytest.mark.parametrize(
    "outcome",
    [
        cmd("start_flow", flow="disputa", above={"command": False}),
        cmd("start_flow", flow="disputa", above={"flow": False}),
    ],
)
def test_command_o_flow_bajo_umbral_pide_aclaracion_y_suma_clarifications_used(outcome: object) -> None:
    w = World()
    w.open_run(active=True)
    w.understand.push(outcome)  # type: ignore[arg-type]
    result = w.turn("no sé")
    saved = w.saved()
    assert [m.text for m in result.messages] == [w.text("t-aclarar")]
    assert saved.clarifications_used == 1 and saved.repair_turns_used == 1
    assert saved.active_flow is not None and saved.active_flow.node_id == "pedir"
