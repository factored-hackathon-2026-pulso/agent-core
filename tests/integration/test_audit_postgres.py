"""M11 con Postgres: cadena persistida, append-only por permiso y trigger, unicidad de (run_id, seq)."""

from typing import Any

import psycopg
import pytest

from agent_core.adapters.postgres_audit import PgAuditEvents
from agent_core.audit.chain import chain_events, check_chain
from tests.m11.helpers import events_of

pytestmark = pytest.mark.integration

PgConn = psycopg.Connection[Any]


def persisted(conn: PgConn, run_id: str = "run-0001", count: int = 5) -> PgAuditEvents:
    store = PgAuditEvents(conn)
    store.append_events(run_id, chain_events(run_id, events_of(run_id, count), store.last_event(run_id)))
    conn.commit()
    return store


def test_chain_survives_postgres_roundtrip_for_all_event_types(app_conn: PgConn) -> None:
    store = persisted(app_conn, count=20)
    events = store.read("run-0001")
    assert [e.seq for e in events] == list(range(20))
    assert check_chain("run-0001", events).ok  # el JSON ida y vuelta conserva el hash


def test_last_event_sees_uncommitted_rows_of_the_same_transaction(app_conn: PgConn) -> None:
    store = PgAuditEvents(app_conn)
    store.append_events("run-0001", chain_events("run-0001", events_of("run-0001", 2), None))
    last = store.last_event("run-0001")
    assert last is not None and last.seq == 1
    app_conn.rollback()
    assert store.last_event("run-0001") is None


def test_app_role_cannot_update_delete_or_truncate(app_conn: PgConn) -> None:
    persisted(app_conn)
    for sql in ("UPDATE audit_events SET release = 'x'", "DELETE FROM audit_events", "TRUNCATE audit_events"):
        with pytest.raises(psycopg.errors.InsufficientPrivilege, match="permission denied"):
            app_conn.execute(sql)
        app_conn.rollback()


def test_even_the_table_owner_is_blocked_by_trigger(app_conn: PgConn,
                                                    admin_conn: PgConn) -> None:
    persisted(app_conn)
    for sql in ("UPDATE audit_events SET release = 'x'", "DELETE FROM audit_events", "TRUNCATE audit_events"):
        with pytest.raises(psycopg.errors.InsufficientPrivilege, match="append-only"):
            admin_conn.execute(sql)


def test_seq_is_unique_per_run(app_conn: PgConn) -> None:
    persisted(app_conn, count=2)
    store = PgAuditEvents(app_conn)
    fresh = [e.model_copy(update={"event_id": "evt-fresh-0001"}) for e in events_of("run-0001", 1)]
    dup = chain_events("run-0001", fresh, None)  # seq 0 otra vez, event_id nuevo
    with pytest.raises(psycopg.errors.UniqueViolation) as exc:
        store.append_events("run-0001", dup)
    assert exc.value.diag.constraint_name == "audit_events_pkey"
    app_conn.rollback()


def test_tampering_through_a_privileged_bypass_is_detected_by_verify(admin_conn: PgConn,
                                                                    app_conn: PgConn) -> None:
    store = persisted(app_conn)
    admin_conn.execute("ALTER TABLE audit_events DISABLE TRIGGER USER")
    admin_conn.execute("UPDATE audit_events SET event_json = replace(event_json, 'rel-2026-09-28', 'rel-x') "
                       "WHERE seq = 2")
    assert check_chain("run-0001", store.read("run-0001")).broken_at == 2


def test_read_after_pages_by_seq_for_the_export(app_conn: PgConn) -> None:  # N-08
    store = persisted(app_conn, count=5)
    assert [e.seq for e in store.read_after("run-0001", -1, 2)] == [0, 1]
    assert [e.seq for e in store.read_after("run-0001", 1, 10)] == [2, 3, 4]
    assert store.read_after("run-0001", 4, 10) == []
