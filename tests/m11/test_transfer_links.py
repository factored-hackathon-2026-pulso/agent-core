"""Hash link between the origin and target chains of a transfer (ADR 0021 D9, P2, Review Focus 4)."""

import re
from typing import Any

from agent_core.audit import AuditLog, chain_events, verify_transfer_link
from agent_core.domain import (
    AccessDenied,
    AccessDeniedPayload,
    AccessDeniedReason,
    EngineEvent,
    RunStarted,
    RunStartedPayload,
    RunState,
)
from tests.m04.harness import World
from tests.m04.helpers import cmd

TEXT = "no reconozco un cargo"
HEX64 = re.compile(r"[0-9a-f]{64}")


class _Sink:
    """Minimal read-only `AuditSink` over fixed chains."""

    def __init__(self, chains: dict[str, list[EngineEvent]]) -> None:
        self._chains = chains

    def read(self, run_id: str) -> list[EngineEvent]:
        return list(self._chains.get(run_id, []))

    def append_outside_turn(self, run_id: str, events: list[EngineEvent]) -> None:
        raise AssertionError("verification must not write")


def _world() -> World:
    w = World(chain_factory=AuditLog)
    state = w.reception()
    assert state is not None
    # The harness opens the reception run without `start_run`: seal the `run_started` a real run starts with.
    started = RunStarted(
        event_id="evt-started",
        run_id=state.run_id,
        session_id=state.session_id,
        release=state.release,
        ts=w.clock.now(),
        payload=RunStartedPayload(
            agent=state.agent,
            mode=state.mode,
            principal_type=state.principal.type,
            locale=state.locale,
        ),
    )
    AuditLog(w.audit, w.store.uow).append_standalone(state.run_id, [started])
    w.understand.push(cmd("continue"), cmd("start_flow", flow="disputa"))
    w.turn(TEXT)
    return w


def _runs(w: World) -> tuple[RunState, RunState]:
    source, target = w.session_runs()
    return source, target


def _index(events: list[EngineEvent], kind: str) -> int:
    return next(i for i, e in enumerate(events) if e.type == kind)


def _rechain(run_id: str, events: list[EngineEvent]) -> list[EngineEvent]:
    bare = [e.model_copy(update={"seq": None, "prev_hash": None, "hash": None}) for e in events]
    return chain_events(run_id, bare, None)


def _with_origin(target: RunState, **changes: Any) -> RunState:
    assert target.origin is not None
    return target.model_copy(update={"origin": target.origin.model_copy(update=changes)})


def _sink_for(w: World, source: RunState, source_events: list[EngineEvent]) -> _Sink:
    target_id = w.session_runs()[1].run_id
    return _Sink({source.run_id: source_events, target_id: w.audit.read(target_id)})


def _assert_readable_and_dataless(problems: list[str]) -> None:
    for problem in problems:
        assert isinstance(problem, str) and problem
        assert not HEX64.search(problem)
        assert TEXT not in problem


def test_a_fresh_transfer_verifies() -> None:
    w = _world()
    assert verify_transfer_link(_runs(w)[1], w.audit) == []


def test_the_link_points_at_turn_completed_not_the_last_event() -> None:
    w = _world()
    source, target = _runs(w)
    events = w.audit.read(source.run_id)
    assert target.origin is not None
    assert events[_index(events, "turn_completed")].hash == target.origin.from_event_hash


def test_later_events_on_the_origin_chain_do_not_break_the_link() -> None:
    w = _world()
    source, target = _runs(w)
    denied = AccessDenied(
        event_id="evt-late",
        run_id=source.run_id,
        session_id=source.session_id,
        release=source.release,
        ts=w.clock.now(),
        payload=AccessDeniedPayload(reason=AccessDeniedReason.principal_mismatch),
    )
    AuditLog(w.audit, w.store.uow).append_standalone(source.run_id, [denied])
    assert w.audit.read(source.run_id)[-1].type == "access_denied"
    assert verify_transfer_link(target, w.audit) == []


def test_a_tampered_run_transferred_breaks_the_link() -> None:
    w = _world()
    source, target = _runs(w)
    events = w.store.events[source.run_id]
    i = _index(events, "run_transferred")
    payload = events[i].payload.model_copy(update={"reason": "altered"})  # type: ignore[attr-defined]
    events[i] = events[i].model_copy(update={"payload": payload})
    problems = verify_transfer_link(target, w.audit)
    assert problems
    _assert_readable_and_dataless(problems)


def test_tampering_with_an_early_origin_event_breaks_the_link() -> None:
    w = _world()
    source, target = _runs(w)
    events = w.store.events[source.run_id]
    events[0] = events[0].model_copy(update={"event_id": "forged"})
    assert verify_transfer_link(target, w.audit) != []


def test_a_forged_from_event_hash_is_reported() -> None:
    w = _world()
    _, target = _runs(w)
    problems = verify_transfer_link(_with_origin(target, from_event_hash="0" * 64), w.audit)
    assert problems
    _assert_readable_and_dataless(problems)


def test_a_real_hash_of_another_event_is_not_a_valid_link() -> None:
    w = _world()
    source, target = _runs(w)
    events = w.audit.read(source.run_id)
    run_closed = events[_index(events, "run_closed")]
    assert run_closed.hash is not None
    assert verify_transfer_link(_with_origin(target, from_event_hash=run_closed.hash), w.audit) != []
    first = events[0].hash
    assert first is not None
    assert verify_transfer_link(_with_origin(target, from_event_hash=first), w.audit) != []


def test_a_missing_origin_chain_is_reported() -> None:
    w = _world()
    source, target = _runs(w)
    sink = _Sink({target.run_id: w.audit.read(target.run_id)})
    problems = verify_transfer_link(target, sink)
    assert problems
    _assert_readable_and_dataless(problems)
    assert source.run_id not in " ".join(problems)


def test_a_different_transfer_id_in_the_origin_is_reported() -> None:
    w = _world()
    _, target = _runs(w)
    assert verify_transfer_link(_with_origin(target, transfer_id="other"), w.audit) != []


def test_an_origin_chain_without_run_transferred_is_reported() -> None:
    w = _world()
    source, target = _runs(w)
    kept = [e for e in w.audit.read(source.run_id) if e.type != "run_transferred"]
    chain = _rechain(source.run_id, kept)  # valid chain, but the transfer event is gone
    forged = _with_origin(target, from_event_hash=chain[_index(chain, "turn_completed")].hash)
    sink = _sink_for(w, source, chain)
    assert "no hay un run_transferred que apunte a este run" in verify_transfer_link(forged, sink)


def test_a_run_transferred_that_points_to_another_run_is_reported() -> None:
    w = _world()
    source, target = _runs(w)
    events = w.audit.read(source.run_id)
    i = _index(events, "run_transferred")
    payload = events[i].payload.model_copy(update={"to_run_id": "run-elsewhere"})  # type: ignore[attr-defined]
    events[i] = events[i].model_copy(update={"payload": payload})
    chain = _rechain(source.run_id, events)
    forged = _with_origin(target, from_event_hash=chain[_index(chain, "turn_completed")].hash)
    problems = verify_transfer_link(forged, _sink_for(w, source, chain))
    assert "no hay un run_transferred que apunte a este run" in problems


def test_a_target_whose_run_started_carries_another_origin_is_reported() -> None:
    w = _world()
    _, target = _runs(w)
    problems = verify_transfer_link(_with_origin(target, depth=2), w.audit)
    assert any("run_started" in p for p in problems)


def test_a_missing_target_chain_is_reported() -> None:
    w = _world()
    source, target = _runs(w)
    sink = _Sink({source.run_id: w.audit.read(source.run_id)})
    assert verify_transfer_link(target, sink) != []


def test_a_run_without_origin_has_nothing_to_verify() -> None:
    w = _world()
    source, _ = _runs(w)
    assert source.origin is None
    assert verify_transfer_link(source, w.audit) == []


def test_verification_is_read_only() -> None:
    w = _world()
    source, target = _runs(w)
    before = (len(w.audit.read(source.run_id)), len(w.audit.read(target.run_id)))
    verify_transfer_link(target, w.audit)
    assert (len(w.audit.read(source.run_id)), len(w.audit.read(target.run_id))) == before


def _rechained_origin(w: World, **payload_changes: Any) -> tuple[RunState, list[EngineEvent]]:
    source, target = _runs(w)
    events = w.audit.read(source.run_id)
    i = _index(events, "run_transferred")
    payload = events[i].payload.model_copy(update=payload_changes)  # type: ignore[attr-defined]
    events[i] = events[i].model_copy(update={"payload": payload})
    chain = _rechain(source.run_id, events)
    return _with_origin(target, from_event_hash=chain[_index(chain, "turn_completed")].hash), chain


def _other_agent(w: World) -> Any:
    source, _ = _runs(w)
    return source.agent.model_copy(update={"id": "impostor"})


def _forged_target_chain(w: World, **changes: Any) -> tuple[RunState, _Sink]:
    """Valid, rechained target chain whose `run_started` carries altered agent/release."""
    source, target = _runs(w)
    events = w.audit.read(target.run_id)
    first = events[0]
    payload = first.payload.model_copy(update=changes.pop("payload", {}))  # type: ignore[attr-defined]
    events[0] = first.model_copy(update={"payload": payload, **changes})
    chain = _rechain(target.run_id, events)
    return target, _Sink({source.run_id: w.audit.read(source.run_id), target.run_id: chain})


MSG_FROM = "el agente o la release de origen no coinciden con el run_started del origen"
MSG_TO = "el agente o la release del destino no coinciden con el run_transferred"


def test_a_forged_from_agent_is_reported() -> None:
    w = _world()
    _, target = _runs(w)
    problems = verify_transfer_link(_with_origin(target, from_agent=_other_agent(w)), w.audit)
    assert MSG_FROM in problems
    _assert_readable_and_dataless(problems)
    assert "impostor" not in " ".join(problems)


def test_a_forged_from_release_id_is_reported() -> None:
    w = _world()
    _, target = _runs(w)
    problems = verify_transfer_link(_with_origin(target, from_release_id="rel-fake"), w.audit)
    assert MSG_FROM in problems
    _assert_readable_and_dataless(problems)


def test_a_run_transferred_naming_another_to_agent_is_reported() -> None:
    w = _world()
    _, target = _runs(w)
    forged, chain = _rechained_origin(w, to_agent=_other_agent(w))
    problems = verify_transfer_link(forged, _sink_for(w, _runs(w)[0], chain))
    assert problems[-1:] == [MSG_TO] or MSG_TO in problems
    assert target.origin is not None


def test_a_run_transferred_naming_another_to_release_is_reported() -> None:
    w = _world()
    forged, chain = _rechained_origin(w, to_release_id="rel-fake")
    problems = verify_transfer_link(forged, _sink_for(w, _runs(w)[0], chain))
    assert MSG_TO in problems
    _assert_readable_and_dataless(problems)


def test_only_to_agent_or_release_changed_on_the_target_run_started_is_reported() -> None:
    w = _world()
    target, sink = _forged_target_chain(w, release="rel-fake")
    assert verify_transfer_link(target, sink) == [MSG_TO]
    target, sink = _forged_target_chain(w, payload={"agent": _other_agent(w)})
    assert verify_transfer_link(target, sink) == [MSG_TO]


def test_a_turn_completed_of_another_turn_is_reported() -> None:
    w = _world()
    source, target = _runs(w)
    events = w.audit.read(source.run_id)
    i = _index(events, "turn_completed")
    events[i] = events[i].model_copy(update={"turn_id": "turn-other"})
    chain = _rechain(source.run_id, events)
    forged = _with_origin(target, from_event_hash=chain[i].hash)
    problems = verify_transfer_link(forged, _sink_for(w, source, chain))
    assert "el turn_completed no es el del turno que transfirió" in problems
    _assert_readable_and_dataless(problems)


def test_a_target_first_event_of_another_run_is_reported() -> None:
    w = _world()
    source, target = _runs(w)
    other = _Sink({source.run_id: w.audit.read(source.run_id), target.run_id: _rechain(
        "run-other", [e.model_copy(update={"run_id": "run-other"}) for e in w.audit.read(target.run_id)])})
    assert verify_transfer_link(target, other) != []
