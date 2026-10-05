"""`ensure_schema` sobre Postgres real: se aplica una vez aunque varias instancias arranquen a la vez."""

import threading
from typing import Any

import psycopg

from agent_core.adapters.postgres_uow import apply_schema
from agent_core.composition import schema_version as sv
from tests.integration.conftest import ADMIN_DSN, SCHEMA


def _connect() -> "psycopg.Connection[Any]":
    return psycopg.connect(ADMIN_DSN, options=f"-c search_path={SCHEMA}")


def _apply(counter: list[int]) -> Any:
    def apply(conn: "psycopg.Connection[Any]") -> None:
        counter.append(1)
        apply_schema(conn)

    return apply


def test_a_fresh_schema_is_applied_once_and_the_second_call_is_a_no_op(admin_conn: Any) -> None:
    counter: list[int] = []
    with _connect() as conn:
        assert sv.ensure_schema(conn, "eval", _apply(counter)) is True
    with _connect() as conn:
        assert sv.ensure_schema(conn, "eval", _apply(counter)) is False
    assert counter == [1]
    row = admin_conn.execute(f"SELECT digest FROM {sv.LEDGER} WHERE scope = 'eval'").fetchone()
    assert row == (sv.expected_digest("eval"),)


def test_instances_starting_together_apply_the_schema_exactly_once(admin_conn: Any) -> None:
    counter: list[int] = []
    errors: list[BaseException] = []
    barrier = threading.Barrier(6)

    def instance() -> None:
        try:
            barrier.wait(timeout=10)
            with _connect() as conn:
                sv.ensure_schema(conn, "eval", _apply(counter))
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=instance) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert errors == [] and counter == [1]


def test_a_stale_version_is_migrated_again(admin_conn: Any) -> None:
    counter: list[int] = []
    with _connect() as conn:
        sv.ensure_schema(conn, "eval", _apply(counter))
    admin_conn.execute(f"UPDATE {sv.LEDGER} SET digest = 'viejo' WHERE scope = 'eval'")
    with _connect() as conn:
        assert sv.ensure_schema(conn, "eval", _apply(counter)) is True
    assert counter == [1, 1]


def test_schema_is_current_tracks_the_recorded_version(admin_conn: Any) -> None:
    dsn = ADMIN_DSN
    options = f"-c search_path={SCHEMA}"
    with _connect() as conn:
        sv.ensure_schema(conn, "eval", _apply([]))
    assert sv.schema_is_current(dsn, "eval", options=options) is True
    admin_conn.execute(f"UPDATE {sv.LEDGER} SET digest = 'viejo' WHERE scope = 'eval'")
    assert sv.schema_is_current(dsn, "eval", options=options) is False
