"""`PostgresUoW` and `runs_one_open_per_session` without a database (phase 7, F6).

A recording connection stands in for psycopg: it checks the order of the run writes in `_apply` and the
mapping of the index violation to `VersionConflict`. The real index runs only in `tests/integration`
(needs docker)."""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

import psycopg
import pytest

from agent_core.adapters.postgres_uow import ONE_OPEN_RUN_MESSAGE, PostgresUoW
from agent_core.domain import VersionConflict
from testing.builders import NOW, run_state
from testing.fakes.storage import InMemoryStore

_TRANSFERRED = {"status": "closed", "outcome": "transferred", "closed_at": NOW, "inactive_after": None}


class _Cursor:
    def __init__(self, row: tuple[Any, ...] | None = None) -> None:
        self.rowcount = 1
        self._row = row

    def fetchone(self) -> tuple[Any, ...] | None:
        return self._row


@dataclass
class _RecordingConn:
    versions: dict[str, int]
    fail_with: Exception | None = None
    writes: list[tuple[str, str]] = field(default_factory=list)

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> _Cursor:
        if sql.startswith("SELECT state_version FROM runs"):
            version = self.versions.get(params[0])
            return _Cursor((version,) if version is not None else None)
        if sql.startswith("INSERT INTO runs"):
            self.writes.append(("insert", params[0]))
        elif sql.startswith("UPDATE runs"):
            self.writes.append(("update", params[5]))
        if self.fail_with is not None:
            raise self.fail_with
        return _Cursor()

    @contextmanager
    def transaction(self) -> Iterator[None]:
        yield

    def close(self) -> None:
        pass


def _transfer_saved_target_first(conn: _RecordingConn) -> PostgresUoW:
    uow = PostgresUoW(conn)  # type: ignore[arg-type]
    uow.save_run(run_state(run_id="run-b"), 0)  # the open target is saved first
    uow.save_run(run_state(run_id="run-a", **_TRANSFERRED), 1)  # then the origin closes
    return uow


def test_closing_writes_are_applied_before_the_open_target() -> None:
    conn = _RecordingConn(versions={"run-a": 1})
    _transfer_saved_target_first(conn).commit()
    assert conn.writes == [("update", "run-a"), ("insert", "run-b")]


def test_save_order_is_kept_within_each_group() -> None:
    conn = _RecordingConn(versions={})
    uow = PostgresUoW(conn)  # type: ignore[arg-type]
    for rid in ("run-c", "run-a", "run-b"):
        uow.save_run(run_state(run_id=rid, session_id=f"s-{rid}"), 0)
    uow.save_run(run_state(run_id="run-z", session_id="s-z", **_TRANSFERRED), 0)
    uow.commit()
    assert [rid for _, rid in conn.writes] == ["run-z", "run-c", "run-a", "run-b"]


class _Diag:
    def __init__(self, constraint_name: str) -> None:
        self.constraint_name = constraint_name


def _violation(constraint_name: str) -> psycopg.errors.UniqueViolation:
    class _Violation(psycopg.errors.UniqueViolation):
        @property
        def diag(self) -> Any:
            return _Diag(constraint_name)

    return _Violation("duplicate key value violates unique constraint")


def test_the_index_violation_is_a_version_conflict_with_its_own_message() -> None:
    conn = _RecordingConn(versions={}, fail_with=_violation("runs_one_open_per_session"))
    uow = PostgresUoW(conn)  # type: ignore[arg-type]
    uow.save_run(run_state(run_id="run-b"), 0)
    with pytest.raises(VersionConflict) as exc:
        uow.commit()
    assert str(exc.value) == ONE_OPEN_RUN_MESSAGE


def test_other_unique_violations_keep_the_audit_chain_message() -> None:
    conn = _RecordingConn(versions={}, fail_with=_violation("audit_events_pkey"))
    uow = PostgresUoW(conn)  # type: ignore[arg-type]
    uow.save_run(run_state(run_id="run-b"), 0)
    with pytest.raises(VersionConflict, match="cadena de auditoría"):
        uow.commit()


def test_the_in_memory_double_raises_the_same_message() -> None:
    store = InMemoryStore()
    with store.uow() as uow:
        uow.save_run(run_state(run_id="run-a"), 0)
        uow.save_run(run_state(run_id="run-b"), 0)
        with pytest.raises(VersionConflict) as exc:
            uow.commit()
    assert str(exc.value) == ONE_OPEN_RUN_MESSAGE
