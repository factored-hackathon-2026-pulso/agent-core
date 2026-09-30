"""Idempotencia de `start_run` por `(principal, idempotency_key)` (M9 §3.3, T-M9-11), dentro de M4.

El registro se escribe en la misma transacción que el run: no hay un run sin su clave ni una clave sin su
run, y un reintento devuelve el mismo `RunResult`."""

from typing import Any

import pytest

from agent_core.domain import AgentSelector, EngineError, ProblemCode, RunInput
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
