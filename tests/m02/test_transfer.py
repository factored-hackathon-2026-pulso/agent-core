"""`transfer` node (ADR 0021): terminal, with the request M4 resolves."""

from typing import Any

from agent_core.interpreter import Stop
from testing.fakes.decision import make_decision
from tests.m02.harness import World, fact, flow, slot

SNAPSHOT: dict[str, Any] = {
    "directory": "customer-care", "hash": "b" * 64, "choices": ["disputas"],
    "entries": [{"agent_id": "disputas", "release_id": "rel-2", "summary": "s", "examples": [],
                 "accepts": {"slots": {"problem": {"type": "string", "required": True}}},
                 "supported_locales": ["es"]}]}
TRANSFER = {"id": "transfer", "type": "transfer",
            "config": {"target_from": "decisions.route.choice", "directory_from": "directory",
                       "packet": {"reason": "routed", "slots": ["problem", "card"]}},
            "next": {"rejected": "esc"}}
ESC = {"id": "esc", "type": "escalate", "config": {"reason_code": "policy:transfer_rejected"}}


def _state(w: World, **over: Any) -> Any:
    decision = make_decision({"choice": "disputas"}, {"choice": True}).decision
    base: dict[str, Any] = {"slots": {"problem": slot("no reconozco un cargo")},
                            "facts": {"directory": fact(SNAPSHOT)}, "decisions": {"route": decision}}
    return w.state(flow(TRANSFER, ESC), **(base | over))


def test_transfer_stops_terminal_with_the_request() -> None:
    w = World()
    out = w.step(_state(w))
    assert out.stop is Stop.terminal and out.escalation is None
    request = out.transfer
    assert request is not None and request.target == "disputas" and request.reason == "routed"
    assert request.slots == {"problem": "no reconozco un cargo"}  # `card` was never collected: omitted
    assert request.snapshot is not None and request.snapshot.choices == ["disputas"]
    assert out.state.active_flow.node_id == "transfer"  # M4 may still follow `rejected`


def test_missing_decision_gives_a_request_without_target() -> None:
    w = World()
    out = w.step(_state(w, decisions={}))
    assert out.transfer is not None and out.transfer.target is None


def test_claimed_slots_do_not_travel() -> None:
    w = World()
    out = w.step(_state(w, slots={"problem": slot("x", "claimed")}))
    assert out.transfer is not None and out.transfer.slots == {}
