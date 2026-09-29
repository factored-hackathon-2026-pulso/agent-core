"""`awaiting_for` y `build_turn_result` (m04 §3.5)."""

from typing import Any

from agent_core.domain import Awaiting, ConfirmationPrompt, Message, Outcome
from agent_core.interpreter import Stop
from agent_core.turn.buffer import EventBuffer
from agent_core.turn.frame import TurnFrame
from agent_core.turn.results import awaiting_for, build_turn_result
from testing.builders import NOW, run_state
from testing.fakes.clock import FakeClock
from tests.m04.helpers import FakeRuntime


def make_frame(**over: Any) -> TurnFrame:
    from agent_core.turn.events import TurnEvents
    from agent_core.turn.metering import StageMeter
    from testing.fakes.ids import FakeIds

    clock = FakeClock()
    base: dict[str, Any] = {
        "uow": None,
        "state": run_state(),
        "turn_id": "turn-1",
        "entry": "turn",
        "client_turn_id": "c-1",
        "agent": None,
        "release": None,
        "runtime": FakeRuntime(None),  # type: ignore[arg-type]
        "meter": StageMeter(clock),
        "buffer": EventBuffer(),
        "events": TurnEvents(FakeIds(), clock),
    }
    return TurnFrame(**(base | over))


def test_stop_se_traduce_a_awaiting() -> None:
    state = run_state(active_flow={"flow": "f@1.0.0", "node_id": "n1"})
    assert awaiting_for(Stop.awaiting_confirmation, state) == (Awaiting.confirmation, "n1")
    assert awaiting_for(Stop.awaiting_slot, state) == (Awaiting.slot, "n1")
    assert awaiting_for(Stop.awaiting_step_up, state) == (Awaiting.step_up, "n1")
    assert awaiting_for(Stop.awaiting_user, state) == (Awaiting.input, "n1")
    assert awaiting_for(Stop.terminal, state) == (Awaiting.none, None)


def test_oferta_pendiente_es_input_sin_nodo() -> None:
    state = run_state(pending_offer="disputa")
    assert awaiting_for(None, state) == (Awaiting.input, None)


def test_run_cerrado_no_espera_nada() -> None:
    state = run_state(status="closed", outcome="resolved", closed_at=NOW, inactive_after=None)
    assert awaiting_for(None, state) == (Awaiting.none, None)


def test_sin_stop_se_conserva_lo_que_el_run_ya_esperaba() -> None:
    state = run_state(
        active_flow={"flow": "f@1.0.0", "node_id": "n1"}, awaiting="confirmation", awaiting_node_id="n1"
    )
    assert awaiting_for(None, state) == (Awaiting.confirmation, "n1")


def test_el_resultado_usa_render_y_no_expone_tokens() -> None:
    summary = Message(kind="template", text="[model]resumen", locale="es")
    prompt = ConfirmationPrompt(action_id="a1", token="tok", expires_at=NOW, summary=summary)
    frame = make_frame(
        messages=[Message(kind="template", text="[model]hola", locale="es")], confirmation=prompt
    )
    result = build_turn_result(frame, frame.state, "trace-1")
    assert [m.text for m in result.messages] == ["hola"]
    assert result.confirmation is not None and result.confirmation.summary.text == "resumen"
    assert result.trace_id == "trace-1" and result.turn_id == "turn-1"


def test_run_escalado_expone_handoff_ref_y_outcome() -> None:
    state = run_state(
        status="escalated",
        outcome="escalated",
        handoff_ref="handoff-0001",
        closed_at=NOW,
        inactive_after=None,
    )
    result = build_turn_result(make_frame(state=state), state, "t")
    assert (result.status, result.outcome, result.handoff_ref) == (
        "escalated",
        Outcome.escalated,
        "handoff-0001",
    )
    assert result.awaiting is Awaiting.none
