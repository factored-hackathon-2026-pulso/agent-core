from agent_core.domain import ResponseEmitted
from testing.fakes.gateway import gen
from tests.m08.helpers import GOOD, World


def test_response_emitted_envelope_comes_from_injected_ports() -> None:
    w = World([gen(GOOD, ["f-pqr"])])
    _, _, (event,) = w.run()
    assert isinstance(event, ResponseEmitted)
    assert event.event_id == "event-0001" and event.run_id == w.state.run_id
    assert event.session_id == w.state.session_id and event.turn_id == "turn-0001"
    assert event.ts == w.clock.now() and event.release == w.state.release
    assert (event.seq, event.prev_hash, event.hash) == (None, None, None)  # los asigna M11


def test_event_never_contains_the_draft_text() -> None:
    w = World([gen(GOOD, ["f-pqr"])])
    _, _, (event,) = w.run()
    assert "radicada" not in event.model_dump_json()
