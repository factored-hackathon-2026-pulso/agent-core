"""`HandoffService.escalate`: T-M10-01, T-M10-04, T-M10-07 y T-M10-08 (con la transacción que armará M4)."""

import pytest

from agent_core.domain import Escalated, EscalationRequest, Outcome, dumps
from agent_core.handoff import HandoffPreconditionError
from agent_core.handoff.packet import HandoffRecord
from testing.fakes.storage import InMemoryOutbox, SimulatedCrash
from tests.m10.helpers import (
    DOC,
    escalate_and_commit,
    make_state,
    make_world,
    rule_evaluated,
    seed_run,
    tool_called,
)

REQUEST = EscalationRequest(reason_code="customer_request", target_queue="disputas", priority="normal")
MONTO = EscalationRequest(reason_code="policy:escalamiento-disputa-monto", target_queue="disputas-alto-monto",
                          priority="high")


def test_t_m10_01_escalate_emits_handoff_created_and_closes_the_run_for_the_bot() -> None:
    """T-M10-01: emite `handoff_created` en el outbox y cierra el run (el `410` siguiente es de M4)."""
    world = make_world()
    state = seed_run(world, make_state())
    closed, events, outbox, message = escalate_and_commit(world, state, REQUEST)

    stored = world.store.runs["run-0001"]
    assert stored.status == "escalated" and stored.outcome is Outcome.escalated
    assert stored.handoff_ref == closed.handoff_ref == "handoff-0001"
    assert stored.closed_at == world.clock.now() and stored.inactive_after is None
    assert stored.awaiting.value == "none" and stored.awaiting_node_id is None
    assert stored.actions == state.actions  # M10 no toca las acciones

    (pending,) = InMemoryOutbox(world.store).pending(10)
    assert pending == outbox and pending.type == "handoff_created" and pending.run_id == "run-0001"
    assert pending.payload == {
        "handoff_ref": "handoff-0001", "run_id": "run-0001", "target_queue": "disputas", "priority": "normal",
        "reason_code": "customer_request", "language": "es", "reportable_attrs": {"country": "CO"},
    }
    (event,) = world.store.events["run-0001"]
    assert isinstance(event, Escalated) and event == events[0]
    assert event.turn_id == "turn-0001" and event.release == "rel-2026-09-28"
    assert event.payload.model_dump() == {"reason_code": "customer_request", "target_queue": "disputas",
                                          "priority": "normal", "handoff_ref": "handoff-0001"}
    assert not any(e.type == "run_closed" for e in events)  # `run_closed` es solo de M4
    assert message.kind == "template" and message.locale == "es"
    assert message.text == "Te paso con una persona del equipo de disputas."
    assert "handoff-0001" in world.store.handoffs


def test_handoff_message_falls_back_to_the_default_when_the_template_is_missing() -> None:
    world = make_world(with_template=False)
    state = seed_run(world, make_state(locale="pt"))
    *_, message = world.service.escalate(state, REQUEST, [], uow=world.store.uow(), turn_id=None)
    assert message.locale == "pt" and "equipe" in message.text


def test_t_m10_04_persisted_packet_has_no_clear_pii() -> None:
    """T-M10-04: lo persistido no contiene `pii_direct` en claro."""
    world = make_world()
    state = seed_run(world, make_state())
    escalate_and_commit(world, state, REQUEST)
    raw = world.store.handoffs["handoff-0001"]
    facts_full = {n: f.value for n, f in state.facts.items()} | {"documento": DOC}
    assert world.service._views.find_clear_pii(dumps(raw), facts_full) == []
    assert DOC not in dumps(raw)
    record = HandoffRecord.model_validate(raw)
    assert record.resolution is None and not record.packet.degraded_packet


def test_t_m10_07_amount_policy_escalation_keeps_priority_and_evidence() -> None:
    """T-M10-07: escalamiento por monto (`policy:escalamiento-disputa-monto`) con la prioridad correcta."""
    world = make_world()
    state = seed_run(world, make_state())
    events = [tool_called("call-0001"), rule_evaluated("escalamiento-disputa-monto@1.0.0")]
    _, _, outbox, _ = escalate_and_commit(world, state, MONTO, events)
    packet = HandoffRecord.model_validate(world.store.handoffs["handoff-0001"]).packet
    assert (packet.reason_code, packet.priority, packet.target_queue) == (
        "policy:escalamiento-disputa-monto", "high", "disputas-alto-monto")
    assert "policy:escalamiento-disputa-monto@1.0.0" in packet.evidence_refs
    assert "call:call-0001" in packet.evidence_refs
    assert outbox.payload["priority"] == "high" and outbox.payload["reason_code"] == MONTO.reason_code
    assert world.store.events["run-0001"][0].payload.priority == "high"  # type: ignore[union-attr]


def test_t_m10_08_outbox_failure_rolls_back_the_run_closure_and_the_packet() -> None:
    """T-M10-08: si el commit falla, el cierre del run, el paquete y el outbox se revierten juntos."""
    world = make_world()
    state = seed_run(world, make_state())
    world.store.inject("on_commit")
    with pytest.raises(SimulatedCrash), world.store.uow() as uow:
        closed, events, outbox, _ = world.service.escalate(state, REQUEST, [], uow=uow, turn_id="turn-0001")
        uow.save_run(closed, expected_version=state.state_version)
        uow.append_events(state.run_id, events)
        uow.enqueue_outbox(outbox)
        uow.commit()
    assert world.store.runs["run-0001"].status == "open"
    assert world.store.handoffs == {} and world.store.outbox_pending == {} and world.store.events == {}


@pytest.mark.parametrize("over", [{"status": "closed", "outcome": "resolved",
                                  "closed_at": "2026-09-28T12:00:00Z", "inactive_after": None}])
def test_escalate_rejects_a_run_that_is_not_open(over: dict[str, object]) -> None:
    world = make_world()
    state = make_state(**over)
    with pytest.raises(HandoffPreconditionError):
        world.service.escalate(state, REQUEST, [], uow=world.store.uow())


def test_escalate_rejects_runs_with_uninvalidated_actions() -> None:
    from testing.builders import action

    world = make_world()
    state = make_state(actions=[action(state="proposed")])
    with pytest.raises(HandoffPreconditionError):
        world.service.escalate(state, REQUEST, [], uow=world.store.uow())


def test_a_failure_building_the_packet_escalates_with_a_degraded_packet() -> None:
    class Broken:
        def project(self, *args: object, **kwargs: object) -> object:
            raise RuntimeError("proyección caída")

    world = make_world()
    world.service._projector._views = Broken()  # type: ignore[assignment]
    state = seed_run(world, make_state())
    escalate_and_commit(world, state, REQUEST)
    packet = HandoffRecord.model_validate(world.store.handoffs["handoff-0001"]).packet
    assert packet.degraded_packet is True and packet.reason_code == "customer_request"
    assert world.store.runs["run-0001"].status == "escalated"
    assert all(f.value is None for f in packet.verified_facts)
    assert packet.evidence_refs == [] and packet.claimed_not_verified == [] and packet.open_questions == []
    (event,) = world.store.events["run-0001"]
    assert event.type == "escalated"
    (pending,) = InMemoryOutbox(world.store).pending(10)
    assert pending.type == "handoff_created"


def test_escalate_rejects_an_already_escalated_run() -> None:
    world = make_world()
    state = seed_run(world, make_state())
    closed, *_ = escalate_and_commit(world, state, REQUEST)
    with pytest.raises(HandoffPreconditionError):
        world.service.escalate(closed, REQUEST, [], uow=world.store.uow())


def test_put_handoff_failure_propagates_and_is_not_degraded() -> None:
    world = make_world()
    state = seed_run(world, make_state())
    uow = world.store.uow()

    def boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("store caído")

    uow.put_handoff = boom  # type: ignore[method-assign]
    with pytest.raises(RuntimeError, match="store caído"):
        world.service.escalate(state, REQUEST, [], uow=uow, turn_id="turn-0001")
    assert world.store.handoffs == {}


def test_escalate_is_deterministic_and_draws_ids_in_a_fixed_order() -> None:
    def run() -> tuple[object, ...]:
        world = make_world()
        state = seed_run(world, make_state())
        closed, events, outbox, message = escalate_and_commit(
            world, state, REQUEST, [tool_called("call-0001")])
        return (closed.model_dump(), [e.model_dump() for e in events], outbox.model_dump(),
                message.model_dump(), world.store.handoffs["handoff-0001"])

    assert run() == run()
    world = make_world()
    state = seed_run(world, make_state())
    closed, events, outbox, _ = escalate_and_commit(world, state, REQUEST)
    assert (closed.handoff_ref, events[0].event_id, outbox.message_id) == (
        "handoff-0001", "event-0001", "message-0001")


def test_reportable_attrs_are_filtered_by_the_authz_port() -> None:
    world = make_world()
    state = seed_run(world, make_state())
    _, _, outbox, _ = escalate_and_commit(world, state, REQUEST)
    assert outbox.payload["reportable_attrs"] == {"country": "CO"}  # `segment` no es reportable
