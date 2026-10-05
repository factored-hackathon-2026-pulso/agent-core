"""Idempotencia de `start_run` por `(principal, idempotency_key)` (M9 §3.3, T-M9-11), dentro de M4.

El registro se escribe en la misma transacción que el run: no hay un run sin su clave ni una clave sin su
run, y un reintento devuelve el mismo `RunResult`."""

import threading
from datetime import timedelta
from typing import Any

import pytest

from agent_core.domain import Agent, AgentSelector, EngineError, EntityRef, ProblemCode, RunInput
from testing.builders import principal
from testing.fakes.storage import SimulatedCrash
from tests.m04.harness import World


def run_input(key: str = "key-1", agent: str = "atencion", **over: Any) -> RunInput:
    return RunInput.model_validate(
        {"agent": AgentSelector(id=agent, alias="prod"), "idempotency_key": key, **over}
    )


def test_repeated_key_and_body_returns_the_same_result_without_a_second_run() -> None:
    w = World()
    first = w.engine.start_run(w.principal, None, run_input())
    second = w.engine.start_run(w.principal, None, run_input())
    assert second == first
    assert list(w.store.runs) == [first.run_id]
    assert [e.type for e in w.store.events[first.run_id]].count("run_started") == 1  # type: ignore[attr-defined]


def test_same_key_with_another_body_is_409_idempotency_conflict() -> None:
    w = World()
    first = w.engine.start_run(w.principal, None, run_input(lang="es"))
    with pytest.raises(EngineError) as info:
        w.engine.start_run(w.principal, None, run_input(lang="pt"))
    assert info.value.code is ProblemCode.idempotency_conflict and info.value.status == 409
    assert list(w.store.runs) == [first.run_id]


def test_the_key_of_the_replay_is_not_part_of_the_body_hash_and_input_order_does_not_matter() -> None:
    w = World()
    first = w.engine.start_run(w.principal, None, run_input(agent="tarea", input={"a": "1", "b": "2"}))
    again = w.engine.start_run(w.principal, None, run_input(agent="tarea", input={"b": "2", "a": "1"}))
    assert again == first


def test_keys_are_scoped_per_principal() -> None:
    w = World()
    mine = w.engine.start_run(w.principal, None, run_input())
    theirs = w.engine.start_run(principal(id="cust-002"), None, run_input())
    assert theirs.run_id != mine.run_id and len(w.store.runs) == 2


def test_a_different_key_creates_a_new_run() -> None:
    w = World()
    one = w.engine.start_run(w.principal, None, run_input("key-1"))
    two = w.engine.start_run(w.principal, None, run_input("key-2"))
    assert one.run_id != two.run_id and len(w.store.runs) == 2


def test_key_and_run_are_committed_atomically() -> None:
    w = World()
    w.store.inject("on_commit")
    with pytest.raises(SimulatedCrash):
        w.engine.start_run(w.principal, None, run_input())
    assert w.store.runs == {} and w.store.idempotency == {}  # ni run huérfano ni clave sin run
    retried = w.engine.start_run(w.principal, None, run_input())
    assert list(w.store.runs) == [retried.run_id]
    assert w.engine.start_run(w.principal, None, run_input()) == retried


def test_a_crash_after_commit_still_replays_the_created_run() -> None:
    w = World()
    w.store.inject("after_commit")
    with pytest.raises(SimulatedCrash):
        w.engine.start_run(w.principal, None, run_input())
    (run_id,) = w.store.runs
    assert w.engine.start_run(w.principal, None, run_input()).run_id == run_id
    assert len(w.store.runs) == 1


def test_a_request_with_the_key_in_flight_is_rejected_without_creating_a_run() -> None:
    w = World()
    with w.store.uow() as uow:  # otra petición ya reservó la clave y sigue trabajando
        assert uow.reserve_run_idempotency(w.principal.key, "key-1", "otro", w.clock.now(),
                                           timedelta(seconds=60)) is None
    with pytest.raises(EngineError) as info:
        w.engine.start_run(w.principal, None, run_input())
    assert info.value.code is ProblemCode.idempotency_in_progress and info.value.status == 409
    assert w.store.runs == {}


def test_a_failed_start_releases_the_key_for_the_retry() -> None:
    w = World()
    w.store.inject("on_commit")
    with pytest.raises(SimulatedCrash):
        w.engine.start_run(w.principal, None, run_input())
    assert w.store.idempotency_reserved == {}
    assert w.engine.start_run(w.principal, None, run_input()).run_id in w.store.runs


def test_concurrent_requests_with_the_same_key_create_one_run() -> None:
    w = World()
    barrier = threading.Barrier(12)
    outcomes: list[object] = []

    def call() -> None:
        barrier.wait()
        try:
            outcomes.append(w.engine.start_run(w.principal, None, run_input()))
        except EngineError as error:
            outcomes.append(error)

    threads = [threading.Thread(target=call) for _ in range(12)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert len(w.store.runs) == 1
    assert all((isinstance(o, EngineError) and o.code is ProblemCode.idempotency_in_progress)
               or getattr(o, "run_id", None) in w.store.runs for o in outcomes)


# --- entrada de un run task: validada contra el input_schema del agente ------------------------------------


def _world_with_schema(schema: dict[str, Any] | None) -> World:
    w = World()
    task = w.registry.get(EntityRef(id="tarea", version="1.0.0"), Agent)
    w.registry._entities[(Agent, "tarea", "1.0.0")] = Agent.model_validate(
        task.model_dump(mode="python") | {"input_schema": schema})
    return w


def test_task_input_without_a_schema_stays_claimed() -> None:
    w = _world_with_schema(None)
    result = w.engine.start_run(w.principal, None, run_input(agent="tarea", input={"a": "1"}))
    assert w.store.runs[result.run_id].slots["a"].status == "claimed"


def test_task_input_that_fits_the_schema_enters_as_validated() -> None:
    w = _world_with_schema({"a": {"type": "string", "required": True}})
    result = w.engine.start_run(w.principal, None, run_input(agent="tarea", input={"a": "1"}))
    assert w.store.runs[result.run_id].slots["a"].status == "validated"


def test_task_input_that_breaks_the_schema_is_422_and_releases_the_key() -> None:
    w = _world_with_schema({"a": {"type": "integer", "required": True}})
    for bad in ({}, {"a": "x"}, {"a": 1, "b": 2}):
        with pytest.raises(EngineError) as info:
            w.engine.start_run(w.principal, None, run_input(agent="tarea", input=bad))
        assert info.value.code is ProblemCode.invalid_request
    assert w.store.runs == {} and w.store.idempotency_reserved == {}


def test_task_input_with_a_list_of_turns_enters_as_validated() -> None:
    """The copilot's suggestions run (ADR 0026) receives the conversation as a bounded list of turns."""
    w = _world_with_schema({"turnos": {"type": "list", "required": True, "max_items": 12,
                                       "items": {"rol": {"type": "string", "required": True},
                                                 "texto": {"type": "string", "required": True}}}})
    turns = [{"rol": "cliente", "texto": "no reconozco un cargo"}, {"rol": "analista", "texto": "reviso"}]
    result = w.engine.start_run(w.principal, None, run_input(agent="tarea", input={"turnos": turns}))
    assert w.store.runs[result.run_id].slots["turnos"].status == "validated"
    with pytest.raises(EngineError) as info:
        w.engine.start_run(w.principal, None, run_input("key-2", agent="tarea", input={"turnos": "suelto"}))
    assert info.value.code is ProblemCode.invalid_request
