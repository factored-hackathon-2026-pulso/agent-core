"""Transfer node, outcome, origin and events (ADR 0021, spec §3.2-§3.4)."""

from typing import Any

import pytest
from pydantic import TypeAdapter, ValidationError

from agent_core import domain
from agent_core.domain import (
    DECLARABLE,
    EVENT_EMITTERS,
    RESULTS,
    TERMINAL,
    AnyEvent,
    Flow,
    Outcome,
    RunOrigin,
    TransferNode,
    is_declarable,
)
from agent_core.ports import IdKind
from testing.builders import run_state
from tests.m00.samples import make_event

TRANSFER: dict[str, Any] = {"id": "transfer", "type": "transfer",
                            "config": {"target_from": "decisions.route.choice", "directory_from": "directory",
                                       "packet": {"reason": "routed", "slots": ["problem"]}},
                            "next": {"rejected": "esc"}}
ORIGIN: dict[str, Any] = {"kind": "transfer", "transfer_id": "transfer-0001", "from_run_id": "run-0001",
                          "from_agent": "reception@1.0.0", "from_release_id": "rel-1",
                          "from_event_hash": "a" * 64, "depth": 1}
FP: dict[str, Any] = {"alg": "HMAC-SHA256", "kid": "test-1", "value": "c" * 64}


def test_transfer_node_parses_and_is_not_in_terminal() -> None:
    flow = Flow.model_validate({"id": "f", "version": "1.0.0", "priority": 1, "nodes": [
        TRANSFER, {"id": "esc", "type": "escalate", "config": {"reason_code": "policy:transfer-rejected"}}]})
    assert isinstance(flow.nodes[0], TransferNode)
    assert RESULTS["transfer"] == frozenset({"rejected"})
    # R2: `transfer` has a wired `rejected` branch, so it is not an endpoint like escalate/end.
    assert "transfer" not in TERMINAL


def test_decide_accepts_choices_from() -> None:
    node = {"id": "route", "type": "decide",
            "config": {"model": "router@1.0.0", "branch_on": "choice", "save_as": "route",
                       "choices_from": "facts.directory.value.choices"},
            "next": {"chosen": "transfer", "none": "esc", "low_confidence": "esc"}}
    flow = Flow.model_validate({"id": "f", "version": "1.0.0", "priority": 1, "nodes": [node]})
    assert flow.nodes[0].config.choices_from == "facts.directory.value.choices"  # type: ignore[union-attr]


def test_transferred_is_never_declarable() -> None:
    assert not any(is_declarable(Outcome.transferred, mode) for mode in DECLARABLE)


def test_run_state_carries_origin_and_closes_as_transferred() -> None:
    state = run_state(origin=ORIGIN)
    assert isinstance(state.origin, RunOrigin) and state.origin.depth == 1
    closed = state.model_copy(update={"status": "closed", "outcome": "transferred",
                                      "closed_at": state.created_at, "inactive_after": None})
    assert closed.outcome is Outcome.transferred


def test_origin_depth_is_positive() -> None:
    with pytest.raises(ValidationError):
        RunOrigin.model_validate(ORIGIN | {"depth": 0})


@pytest.mark.parametrize(("kind", "payload"), [
    ("run_transferred", {"transfer_id": "transfer-0001", "to_agent": "disputas@1.0.0",
                         "to_release_id": "rel-2", "to_run_id": "run-0002", "reason": "routed",
                         "packet_fp": FP,
                         "directory": "customer-care", "directory_hash": "b" * 64,
                         "candidates": ["disputas"]}),
    ("transfer_received", {"transfer_id": "transfer-0001", "accepted_slots": ["problem"], "packet_fp": FP}),
    ("transfer_rejected", {"transfer_id": "transfer-0001", "to_agent": "disputas",
                           "reason_code": "not_in_directory", "directory": "customer-care",
                           "directory_hash": "b" * 64}),
    ("run_closed", {"outcome": "transferred", "closed_by": "transfer"}),
    ("run_started", {"agent": "disputas@1.0.0", "mode": "conversational", "principal_type": "customer",
                     "locale": "es", "origin": ORIGIN}),
])
def test_transfer_events_validate(kind: str, payload: dict[str, Any]) -> None:
    event = TypeAdapter(AnyEvent).validate_python({**make_event(kind), "payload": payload})
    assert event.type == kind
    assert EVENT_EMITTERS[kind] == frozenset({"M4"})


def test_transfer_rejected_reason_is_closed() -> None:
    bad = {"transfer_id": "t", "to_agent": None, "reason_code": "because",
           "directory": None, "directory_hash": None}
    with pytest.raises(ValidationError):
        TypeAdapter(AnyEvent).validate_python({**make_event("transfer_rejected"), "payload": bad})


def test_id_kind_and_turn_result_agent() -> None:
    assert IdKind.transfer.value == "transfer"
    assert "agent" in domain.TurnResult.model_fields


def test_run_started_without_origin_keeps_its_pre_1_2_0_serialization() -> None:
    """Chain hashes are recomputed from the dump, so an empty `origin` must not appear in it."""
    plain = TypeAdapter(AnyEvent).validate_python(make_event("run_started"))
    assert "origin" not in domain.to_jsonable(plain)["payload"]
    transferred = TypeAdapter(AnyEvent).validate_python(
        make_event("run_started") | {"payload": make_event("run_started")["payload"] | {"origin": ORIGIN}})
    assert domain.to_jsonable(transferred)["payload"]["origin"]["depth"] == 1
