"""`record_resolution`: T-M10-06. Una sola resolución por handoff; las notas no van al evento."""

from datetime import timedelta

import pytest

from agent_core.domain import EngineError, EscalationRequest, HandoffResolved, ProblemCode
from agent_core.handoff.packet import HandoffRecord
from testing.builders import advisor_with_delegation
from tests.m10.helpers import HandoffAuthz, escalate_and_commit, make_state, make_world, seed_run

REQUEST = EscalationRequest(reason_code="customer_request", target_queue="disputas", priority="normal")


def _world():  # type: ignore[no-untyped-def]
    world = make_world(authz=HandoffAuthz())
    state = seed_run(world, make_state())
    escalate_and_commit(world, state, REQUEST)
    return world


def test_t_m10_06_resolution_is_recorded_once() -> None:
    """T-M10-06: la resolución se registra una sola vez; la segunda da `409 handoff_already_resolved`."""
    world = _world()
    advisor, obo = advisor_with_delegation()
    event = world.service.record_resolution("handoff-0001", advisor, "caso_resuelto", "useful",
                                            "cliente atendido", on_behalf_of=obo)
    assert isinstance(event, HandoffResolved)
    assert event.payload.model_dump() == {"handoff_ref": "handoff-0001", "resolution_code": "caso_resuelto",
                                          "handoff_quality": "useful", "reader_type": advisor.type}
    assert event.run_id == "run-0001" and event.turn_id is None and event.ts == world.clock.now()
    assert [e.type for e in world.store.events["run-0001"]] == ["escalated", "handoff_resolved"]
    record = HandoffRecord.model_validate(world.store.handoffs["handoff-0001"])
    assert record.resolution is not None and record.resolution.notes == "cliente atendido"

    with pytest.raises(EngineError) as again:
        world.service.record_resolution("handoff-0001", advisor, "otra", "incomplete", on_behalf_of=obo)
    assert again.value.code is ProblemCode.handoff_already_resolved and again.value.status == 409
    assert len(world.store.events["run-0001"]) == 2  # sin segundo evento
    assert HandoffRecord.model_validate(world.store.handoffs["handoff-0001"]).resolution == record.resolution


def test_notes_never_reach_the_event_and_get_returns_the_resolution() -> None:
    world = _world()
    advisor, obo = advisor_with_delegation()
    world.service.record_resolution("handoff-0001", advisor, "caso_resuelto", "useful", "nota libre",
                                    on_behalf_of=obo)
    assert "nota libre" not in world.store.events["run-0001"][-1].model_dump_json()
    got = world.service.get("handoff-0001", advisor, obo)
    assert got["resolution"]["handoff_quality"] == "useful"  # type: ignore[index]


def test_resolution_requires_delegation() -> None:
    world = _world()
    advisor, _ = advisor_with_delegation()
    with pytest.raises(EngineError) as denied:
        world.service.record_resolution("handoff-0001", advisor, "x", "useful")
    assert denied.value.status == 403
    assert HandoffRecord.model_validate(world.store.handoffs["handoff-0001"]).resolution is None


@pytest.mark.parametrize(("code", "quality", "notes"), [
    ("", "useful", None), ("Con Mayúsculas", "useful", None), ("ok", "great", None),
    ("ok", "useful", "x" * 2001),
])
def test_invalid_input_is_a_422(code: str, quality: str, notes: str | None) -> None:
    world = _world()
    advisor, obo = advisor_with_delegation()
    with pytest.raises(EngineError) as bad:
        world.service.record_resolution("handoff-0001", advisor, code, quality, notes, on_behalf_of=obo)  # type: ignore[arg-type]
    assert bad.value.code is ProblemCode.invalid_request and bad.value.status == 422


def test_unknown_handoff_is_not_found() -> None:
    world = _world()
    advisor, obo = advisor_with_delegation()
    with pytest.raises(EngineError) as missing:
        world.service.record_resolution("handoff-9999", advisor, "ok", "useful", on_behalf_of=obo)
    assert missing.value.status == 404


def test_concurrent_resolution_is_rejected_while_the_lease_is_held() -> None:
    world = _world()
    advisor, obo = advisor_with_delegation()
    with world.store.uow() as other:  # otro turno/resolución con el lease vigente
        other.acquire_turn("run-0001", "otro-turno", world.clock.now(), timedelta(seconds=30))
        with pytest.raises(EngineError) as busy:
            world.service.record_resolution("handoff-0001", advisor, "ok", "useful", on_behalf_of=obo)
    assert busy.value.code is ProblemCode.turn_in_progress


def test_the_recorder_hook_receives_the_event_in_the_same_transaction() -> None:
    seen: list[tuple[str, list[str]]] = []

    def recorder(uow, state, events):  # type: ignore[no-untyped-def]
        seen.append((state.run_id, [e.type for e in events]))
        uow.append_events(state.run_id, events)

    world = make_world(authz=HandoffAuthz(), record=recorder)
    escalate_and_commit(world, seed_run(world, make_state()), REQUEST)
    advisor, obo = advisor_with_delegation()
    world.service.record_resolution("handoff-0001", advisor, "ok", "useful", on_behalf_of=obo)
    assert seen == [("run-0001", ["handoff_resolved"])]
