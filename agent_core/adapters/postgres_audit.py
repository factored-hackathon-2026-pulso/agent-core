"""Almacén Postgres de la cadena de auditoría (M11). No encadena: recibe eventos ya encadenados.

M4 lo usa desde su `UnitOfWork` de Postgres, dentro de la misma transacción que `save_run`."""

from importlib import resources
from typing import Any

import psycopg
from psycopg import sql
from pydantic import TypeAdapter

from agent_core.domain import AnyEvent, EngineEvent, dumps, loads

_EVENTS: TypeAdapter[Any] = TypeAdapter(AnyEvent)


def apply_audit_schema(conn: "psycopg.Connection[Any]", app_role: str | None = None) -> None:
    """Crea tabla, índices y triggers (idempotente). `app_role` recibe solo SELECT e INSERT."""
    conn.execute(resources.files("agent_core.adapters").joinpath("sql/audit_events.sql").read_text("utf-8"))
    if app_role is not None:
        conn.execute(sql.SQL("GRANT SELECT, INSERT ON audit_events TO {}").format(sql.Identifier(app_role)))


def _decode(text: str) -> EngineEvent:
    event: EngineEvent = _EVENTS.validate_python(loads(text))
    return event


class PgAuditEvents:
    def __init__(self, conn: "psycopg.Connection[Any]") -> None:
        self._conn = conn

    def append_events(self, run_id: str, events: list[EngineEvent]) -> None:
        rows = []
        for e in events:
            if e.run_id != run_id or e.seq is None or e.hash is None or e.prev_hash is None:
                raise ValueError("solo se persisten eventos encadenados del run indicado")
            event_type = dict(e)["type"]  # discriminador presente en cada evento concreto
            rows.append((run_id, e.seq, e.event_id, event_type, e.release, e.ts,
                         e.prev_hash, e.hash, dumps(e)))
        with self._conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO audit_events (run_id, seq, event_id, type, release, ts, prev_hash, hash, "
                "event_json) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)", rows)

    def last_event(self, run_id: str) -> EngineEvent | None:
        row = self._conn.execute(
            "SELECT event_json FROM audit_events WHERE run_id = %s ORDER BY seq DESC LIMIT 1",
            (run_id,)).fetchone()
        return _decode(row[0]) if row else None

    def read(self, run_id: str) -> list[EngineEvent]:
        rows = self._conn.execute(
            "SELECT event_json FROM audit_events WHERE run_id = %s ORDER BY seq", (run_id,)).fetchall()
        return [_decode(r[0]) for r in rows]
