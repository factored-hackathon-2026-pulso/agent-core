from agent_core.domain import AuthLevel, EntityRef
from agent_core.interpreter.events import Events
from testing.builders import NOW
from tests.m02.harness import World, flow


def test_envelope_and_payloads() -> None:
    w = World()
    state = w.state(flow({"id": "a", "type": "end", "config": {"outcome": "resolved"}}))
    events = Events(w.ctx(turn_id="turn-0007"))
    entered = events.node_entered(state, EntityRef.parse("f@1.0.0"), "a", "end", "none")
    assert (entered.type, entered.run_id, entered.turn_id, entered.ts) == (
        "node_entered", "run-0001", "turn-0007", NOW)
    assert entered.payload.node_type == "end" and entered.payload.resume_kind == "none"
    assert entered.event_id != events.access_denied(state, EntityRef.parse("t@1.0.0")).event_id
    up = events.step_up_requested(state, "a", AuthLevel.step_up, 1)
    assert up.payload.required_level is AuthLevel.step_up and up.payload.attempt == 1
    rule = events.rule_evaluated(state, "a", None, {"facts.x.value": "***"}, True)
    assert rule.payload.result is True and rule.payload.policy is None
    assert events.access_denied(state, EntityRef.parse("t@1.0.0")).payload.reason.value == "tool_denied"
