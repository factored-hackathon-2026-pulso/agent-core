"""Fábrica de eventos de M4 (m04 §6). Solo datos sintéticos."""

from datetime import timedelta

from agent_core.domain import EVENT_EMITTERS, Awaiting, Command, EntityRef, Outcome, TurnStages
from agent_core.turn.events import CommandInfo, TurnEvents
from testing.builders import run_state
from testing.fakes.clock import FakeClock
from testing.fakes.ids import FakeIds


def make() -> tuple[TurnEvents, FakeClock]:
    clock = FakeClock()
    return TurnEvents(FakeIds(), clock), clock


def test_todos_los_eventos_de_m4_estan_permitidos_a_m4() -> None:
    assert {
        "run_started",
        "turn_started",
        "command_emitted",
        "expiry_evaluated",
        "turn_completed",
        "run_closed",
    } <= {t for t, who in EVENT_EMITTERS.items() if "M4" in who}


def test_turn_started_usa_el_instante_del_clock_y_sin_guardas() -> None:
    ev, clock = make()
    event = ev.turn_started(run_state(), "turn-1", "client-1", guards=None)
    assert event.ts == clock.now() and event.payload.guards is None and event.turn_id == "turn-1"
    assert event.payload.client_turn_id == "client-1" and event.session_id == "session-0001"


def test_turn_completed_lleva_stages_y_awaiting() -> None:
    ev, _ = make()
    event = ev.turn_completed(
        run_state(),
        "turn-1",
        "turn",
        "client-1",
        duration_ms=87,
        stages=TurnStages(guards_ms=2, understand_ms=30, flow_ms=50, response_ms=5),
        degraded=False,
        awaiting=Awaiting.confirmation,
    )
    assert event.payload.duration_ms == 87 and event.payload.stages.understand_ms == 30
    assert event.payload.awaiting is Awaiting.confirmation and event.payload.entry == "turn"


def test_expiry_evaluated_registra_instante_y_ttl() -> None:
    ev, clock = make()
    state = run_state()
    event = ev.expiry_evaluated(state, "turn-1", clock.now(), timedelta(minutes=30), expired=False)
    assert event.payload.ttl == timedelta(minutes=30) and event.payload.expired is False
    assert event.payload.now == clock.now() and event.payload.last_activity_at == state.last_activity_at


def test_run_closed_por_escalamiento() -> None:
    ev, _ = make()
    event = ev.run_closed(run_state(), "turn-1", Outcome.escalated, "escalation")
    assert event.payload.closed_by == "escalation" and event.payload.outcome is Outcome.escalated


def test_command_emitted_de_understand_copia_el_resultado() -> None:
    ev, _ = make()
    info = CommandInfo(
        command=Command.start_flow,
        flow="disputa",
        additional_flows=["bloquear-tarjeta"],
        above_threshold={"command": True, "flow": False},
        decision_id="decision-1",
    )
    event = ev.command_emitted(run_state(), "turn-1", info, source="understand")
    p = event.payload
    assert (p.command, p.flow, p.source) == (Command.start_flow, "disputa", "understand")
    assert p.decision_id == "decision-1" and p.additional_flows == ["bloquear-tarjeta"]
    assert p.above_threshold == {"command": True, "flow": False}


def test_command_emitted_por_boton_no_pasa_por_understand() -> None:
    ev, _ = make()
    event = ev.command_emitted(run_state(), "turn-1", CommandInfo(command=Command.affirm), source="button")
    assert event.payload.source == "button" and event.payload.above_threshold == {}
    assert event.payload.decision_id is None


def test_run_started_solo_lleva_lo_reportable_y_sin_texto() -> None:
    ev, _ = make()
    state = run_state()
    event = ev.run_started(state, EntityRef(id="atencion", version="1.0.0"), {"segmento": "demo"})
    p = event.payload
    assert p.reportable_attrs == {"segmento": "demo"} and p.mode == state.mode and p.locale == "es"
    assert p.principal_type == state.principal.type and p.subject_kind == "customer"
    assert event.turn_id is None


def test_los_ids_de_evento_son_unicos_y_vienen_de_ids() -> None:
    ev, _ = make()
    a = ev.turn_started(run_state(), "turn-1", None, guards=None)
    b = ev.turn_started(run_state(), "turn-1", None, guards=None)
    assert a.event_id != b.event_id
