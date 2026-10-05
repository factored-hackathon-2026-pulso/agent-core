"""Versión del esquema (brief A3): se aplica una vez, bajo candado, y deja constancia de su versión."""

from typing import Any

import pytest

from agent_core.composition import schema_version as sv


class Cursor:
    def __init__(self, row: tuple[Any, ...] | None) -> None:
        self._row = row

    def fetchone(self) -> tuple[Any, ...] | None:
        return self._row


class Conn:
    """Registra el SQL; `stored` es el resumen que 'hay' en la tabla de versiones."""

    def __init__(self, stored: str | None = None) -> None:
        self.sql: list[str] = []
        self.stored = stored

    def execute(self, query: Any, params: Any = None) -> Cursor:
        text = query if isinstance(query, str) else query.as_string()
        self.sql.append(text)
        if text.lstrip().startswith("SELECT digest"):
            return Cursor(None if self.stored is None else (self.stored,))
        if text.lstrip().startswith("INSERT INTO " + sv.LEDGER):
            self.stored = params[1]
        return Cursor(None)


def _ensure(conn: Conn, scope: str = "main") -> tuple[bool, list[Conn]]:
    applied: list[Conn] = []
    return sv.ensure_schema(conn, scope, applied.append), applied  # type: ignore[arg-type]


def test_the_lock_is_taken_before_anything_else_touches_the_database() -> None:
    conn = Conn()
    _ensure(conn)
    assert "pg_advisory_xact_lock" in conn.sql[0]
    assert any(sv.LEDGER in s for s in conn.sql[1:])


def test_a_new_database_gets_the_schema_and_its_version_recorded() -> None:
    conn = Conn()
    changed, applied = _ensure(conn)
    assert changed and applied == [conn]
    assert conn.stored == sv.expected_digest("main")


def test_a_database_already_at_the_expected_version_is_left_alone() -> None:
    conn = Conn(stored=sv.expected_digest("main"))
    changed, applied = _ensure(conn)
    assert not changed and applied == []
    assert not any(s.lstrip().startswith("INSERT") for s in conn.sql)


def test_an_older_version_is_migrated_and_updated() -> None:
    conn = Conn(stored="viejo")
    changed, applied = _ensure(conn)
    assert changed and applied and conn.stored == sv.expected_digest("main")


def test_the_scopes_have_different_versions_and_each_is_stable() -> None:
    assert sv.expected_digest("main") != sv.expected_digest("eval")
    assert sv.expected_digest("main") == sv.expected_digest("main")
    assert len(sv.expected_digest("main")) == 16


def test_an_unknown_scope_is_a_programming_error() -> None:
    with pytest.raises(ValueError):
        sv.expected_digest("otra")


def test_schema_is_current_fails_closed_when_the_database_is_unreachable() -> None:
    assert sv.schema_is_current("postgresql://u:secret@127.0.0.1:1/none", "main", timeout_s=1) is False


class FakeBootstrapConn:
    attempts = 0


def test_the_bootstrap_retries_until_the_database_answers_and_never_raises() -> None:
    calls: list[int] = []

    def migrate() -> None:
        calls.append(1)
        if len(calls) < 3:
            raise sv.psycopg.OperationalError("password=hunter2 host=db.internal")

    boot = sv.SchemaBootstrap(migrate, retry_seconds=0.01)
    boot.start()
    assert boot.wait(timeout_s=5)
    boot.stop()
    assert len(calls) == 3 and boot.last_error is None


def test_the_bootstrap_keeps_only_the_error_type_and_can_be_stopped() -> None:
    def migrate() -> None:
        raise sv.psycopg.OperationalError("password=hunter2")

    boot = sv.SchemaBootstrap(migrate, retry_seconds=0.01)
    boot.start()
    for _ in range(200):
        if boot.last_error:
            break
        boot.wait(timeout_s=0.01)
    boot.stop()
    assert boot.last_error == "OperationalError"
    assert not boot.done

