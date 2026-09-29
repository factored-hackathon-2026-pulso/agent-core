"""AuditLog sobre el UoW en memoria: T-M11-03 (cadena rota vía el log) y encadenado entre commits."""

from agent_core.audit.log import AuditLog
from testing.builders import run_state
from testing.fakes.storage import InMemoryAuditSink, InMemoryStore
from tests.m11.helpers import events_of


def make() -> tuple[InMemoryStore, AuditLog]:
    store = InMemoryStore()
    return store, AuditLog(InMemoryAuditSink(store), store.uow)


def test_append_chains_within_one_uow_and_across_commits() -> None:
    store, log = make()
    with store.uow() as uow:
        first = log.append(uow, "run-0001", events_of("run-0001", 2))
        second = log.append(uow, "run-0001", events_of("run-0001", 1))  # ve los eventos pendientes
        uow.commit()
    assert [e.seq for e in first + second] == [0, 1, 2]
    with store.uow() as uow:
        third = log.append(uow, "run-0001", events_of("run-0001", 2))
        uow.commit()
    assert [e.seq for e in third] == [3, 4] and third[0].prev_hash == second[-1].hash
    assert log.verify_chain("run-0001").ok


def test_uncommitted_append_leaves_no_trace() -> None:
    store, log = make()
    with store.uow() as uow:
        log.append(uow, "run-0001", events_of("run-0001", 2))
    assert InMemoryAuditSink(store).read("run-0001") == []
    assert log.verify_chain("run-0001").ok


def test_verify_chain_detects_tampering_in_storage() -> None:
    store, log = make()
    with store.uow() as uow:
        log.append(uow, "run-0001", events_of("run-0001", 4))
        uow.commit()
    stored = store.events["run-0001"]
    stored[1] = stored[1].model_copy(update={"release": "rel-alterada"})
    check = log.verify_chain("run-0001")
    assert (check.ok, check.broken_at) == (False, 1)


def test_runs_have_independent_chains() -> None:
    store, log = make()
    with store.uow() as uow:
        a = log.append(uow, "run-0001", events_of("run-0001", 1))
        b = log.append(uow, "run-0002", events_of("run-0002", 1))
        uow.commit()
    assert a[0].seq == b[0].seq == 0 and a[0].prev_hash != b[0].prev_hash


def test_append_standalone_commits_its_own_uow() -> None:
    _store, log = make()
    out = log.append_standalone("run-0001", events_of("run-0001", 2))
    assert [e.seq for e in out] == [0, 1] and log.verify_chain("run-0001").ok


def test_recorder_has_event_recorder_shape_and_chains() -> None:
    store, log = make()
    state = run_state(run_id="run-0001")
    with store.uow() as uow:
        log.recorder()(uow, state, events_of("run-0001", 2))
        uow.commit()
    assert log.verify_chain("run-0001").ok and len(store.events["run-0001"]) == 2


def test_empty_append_is_a_noop() -> None:
    store, log = make()
    with store.uow() as uow:
        assert log.append(uow, "run-0001", []) == []
