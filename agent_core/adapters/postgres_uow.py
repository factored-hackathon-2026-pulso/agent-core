"""Adaptador Postgres de `UnitOfWork` y de los puertos que lee la unidad 4 (M4, ADR 0007).

Semántica idéntica a la del doble en memoria (`tests/contracts/test_uow_contract.py` corre contra los dos):
todas las escrituras se acumulan en la UoW y se aplican en **una** transacción en `commit()` (por eso un
turno hace una sola transacción de estado); el bloqueo optimista es `UPDATE ... WHERE state_version = base`
dentro de esa transacción (0 filas → `VersionConflict`, y no se aplica nada); el lease de turno se toma en una
sentencia autocommit propia (visible de inmediato; no lo deshace un rollback). Cada UoW abre su conexión.

At most one open run per session: the partial unique index `runs_one_open_per_session` (`sql/schema.sql`)
enforces it. `_apply` writes the runs that do not end open first, and a violation is a `VersionConflict` with
its own message (`ONE_OPEN_RUN_MESSAGE`). Not run against a real Postgres in phase 7 (no docker):
unverified."""

from bisect import insort
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from importlib import resources
from types import TracebackType
from typing import Any, Self

import psycopg

from agent_core.adapters.postgres_audit import PgAuditEvents, apply_audit_schema
from agent_core.domain import (
    EngineEvent,
    JsonValue,
    OutboxMessage,
    PrincipalKey,
    RunResult,
    RunState,
    TurnInProgress,
    TurnResult,
    VersionConflict,
    dumps,
    loads,
)
from agent_core.ports.export import RunSummary

ONE_OPEN_RUN_INDEX = "runs_one_open_per_session"
ONE_OPEN_RUN_MESSAGE = "la sesión ya tiene un run abierto (runs_one_open_per_session)"


def apply_schema(conn: "psycopg.Connection[Any]", app_role: str | None = None) -> None:
    """Crea las tablas de M4 y el log de auditoría (idempotente). `app_role` recibe solo SELECT e INSERT en
    `audit_events` (ver `apply_audit_schema`)."""
    conn.execute(resources.files("agent_core.adapters").joinpath("sql/schema.sql").read_text("utf-8"))
    apply_audit_schema(conn, app_role)


class PostgresStore:
    """Fábrica de UoW y de los puertos de lectura sobre una misma base. `schema` fija el `search_path`."""

    def __init__(self, dsn: str, *, schema: str | None = None) -> None:
        self._dsn = dsn
        self._options = f"-c search_path={schema}" if schema else None

    def connect(self) -> "psycopg.Connection[Any]":
        return psycopg.connect(self._dsn, autocommit=True, options=self._options)

    def ping(self, timeout_s: int = 3) -> bool:
        """`True` si la base responde a `SELECT 1`. Falla cerrado y sin detalle: el error de psycopg puede
        traer el host o la contraseña."""
        try:
            with psycopg.connect(self._dsn, autocommit=True, options=self._options,
                                 connect_timeout=timeout_s) as conn:
                conn.execute("SELECT 1")
        except psycopg.Error:
            return False
        return True

    def uow(self) -> "PostgresUoW":
        return PostgresUoW(self.connect())

    def audit(self) -> "PostgresAuditSink":
        return PostgresAuditSink(self)

    def outbox(self) -> "PostgresOutbox":
        return PostgresOutbox(self)

    def costs(self) -> "PostgresCostCounters":
        return PostgresCostCounters(self)

    @contextmanager
    def reading(self) -> Iterator["psycopg.Connection[Any]"]:
        with self.connect() as conn:
            yield conn


class PostgresUoW:
    """Una instancia = una transacción. Lee sus propias escrituras; `acquire_turn` es inmediato."""

    def __init__(self, conn: "psycopg.Connection[Any]") -> None:
        self._conn = conn
        self._runs: dict[str, RunState] = {}
        self._base_versions: dict[str, int] = {}
        self._turn_results: dict[tuple[str, str], TurnResult] = {}
        self._idempotency: dict[tuple[PrincipalKey, str], tuple[str, RunResult]] = {}
        self._handoffs: dict[str, dict[str, JsonValue]] = {}
        self._events: dict[str, list[EngineEvent]] = {}
        self._outbox: list[OutboxMessage] = []
        self._usage: list[tuple[PrincipalKey, datetime, Decimal]] = []
        self._released: list[tuple[str, str]] = []
        self._done = False

    def __enter__(self) -> Self:
        return self

    def __exit__(self, exc_type: type[BaseException] | None, exc: BaseException | None,
                 tb: TracebackType | None) -> None:
        self._done = True  # lo no commiteado se descarta
        self._conn.close()

    def _check_open(self) -> None:
        if self._done:
            raise RuntimeError("la UoW ya terminó; abre una nueva")

    # --- lease -----------------------------------------------------------------------------------------

    def acquire_turn(self, run_id: str, turn_id: str, now: datetime, ttl: timedelta) -> None:
        self._check_open()
        if ttl <= timedelta(0):
            raise ValueError("el TTL del lease debe ser positivo")
        row = self._conn.execute(
            "INSERT INTO turn_leases (run_id, turn_id, expires_at) VALUES (%s, %s, %s) "
            "ON CONFLICT (run_id) DO UPDATE SET turn_id = EXCLUDED.turn_id, expires_at = EXCLUDED.expires_at "
            "WHERE turn_leases.turn_id = EXCLUDED.turn_id OR turn_leases.expires_at <= %s RETURNING 1",
            (run_id, turn_id, now + ttl, now)).fetchone()
        if row is None:
            holder = self._conn.execute(
                "SELECT turn_id FROM turn_leases WHERE run_id = %s", (run_id,)).fetchone()
            raise TurnInProgress(f"turno {holder[0] if holder else '?'} en curso en {run_id}")

    def release_turn(self, run_id: str, turn_id: str) -> None:
        self._check_open()
        self._released.append((run_id, turn_id))

    # --- estado del run --------------------------------------------------------------------------------

    def _committed_run(self, run_id: str) -> RunState | None:
        row = self._conn.execute("SELECT state_json FROM runs WHERE run_id = %s", (run_id,)).fetchone()
        return RunState.model_validate(loads(row[0])) if row else None

    def load_run(self, run_id: str) -> RunState | None:
        local = self._runs.get(run_id)
        if local is not None:
            return local.model_copy(deep=True)
        return self._committed_run(run_id)

    def list_runs_by_session(self, session_id: str) -> list[RunState]:
        rows = self._conn.execute(
            "SELECT run_id FROM runs WHERE session_id = %s ORDER BY run_seq", (session_id,)).fetchall()
        ids = [row[0] for row in rows]
        ids += [rid for rid, s in self._runs.items() if s.session_id == session_id and rid not in ids]
        return [state for rid in ids if (state := self.load_run(rid)) is not None]

    def find_run_by_session(self, session_id: str) -> RunState | None:
        runs = self.list_runs_by_session(session_id)
        open_runs = [r for r in runs if r.status == "open"]
        if open_runs:
            return open_runs[-1]
        return runs[-1] if runs else None

    def save_run(self, state: RunState, expected_version: int) -> RunState:
        self._check_open()
        local = self._runs.get(state.run_id)
        if local is not None:
            current_version = local.state_version
        else:
            row = self._conn.execute(
                "SELECT state_version FROM runs WHERE run_id = %s", (state.run_id,)).fetchone()
            current_version = row[0] if row else 0
        if expected_version != current_version:
            raise VersionConflict(f"{state.run_id}: esperada {expected_version}, vigente {current_version}")
        validated = RunState.model_validate(state.model_dump())  # revalida y corta todo alias
        saved = validated.model_copy(update={"state_version": expected_version + 1})
        self._base_versions.setdefault(state.run_id, current_version)
        self._runs[state.run_id] = saved
        return saved.model_copy(deep=True)

    def list_inactive(self, now: datetime, limit: int) -> list[str]:
        if limit <= 0:
            return []
        merged: list[tuple[datetime, str]] = []
        for s in self._runs.values():
            if s.status == "open" and s.inactive_after is not None and s.inactive_after < now:
                insort(merged, (s.inactive_after, s.run_id))
        rows = self._conn.execute(
            "SELECT run_id, inactive_after FROM runs WHERE status = 'open' AND inactive_after < %s "
            "AND NOT (run_id = ANY(%s)) ORDER BY inactive_after, run_id LIMIT %s",
            (now, list(self._runs), limit)).fetchall()
        for run_id, at in rows:
            insort(merged, (at, run_id))
        return [run_id for _, run_id in merged[:limit]]

    # --- resultados, idempotencia, handoffs ------------------------------------------------------------

    def get_turn_result(self, run_id: str, client_turn_id: str) -> TurnResult | None:
        local = self._turn_results.get((run_id, client_turn_id))
        if local is not None:
            return local.model_copy(deep=True)
        row = self._conn.execute(
            "SELECT result_json FROM turn_results WHERE run_id = %s AND client_turn_id = %s",
            (run_id, client_turn_id)).fetchone()
        return TurnResult.model_validate(loads(row[0])) if row else None

    def put_turn_result(self, run_id: str, client_turn_id: str, result: TurnResult) -> None:
        self._check_open()
        self._turn_results[(run_id, client_turn_id)] = result.model_copy(deep=True)

    def get_run_idempotency(self, principal: PrincipalKey, key: str) -> tuple[str, RunResult] | None:
        local = self._idempotency.get((principal, key))
        if local is not None:
            return local[0], local[1].model_copy(deep=True)
        row = self._conn.execute(
            "SELECT body_hash, result_json FROM run_idempotency "
            "WHERE principal_type = %s AND principal_id = %s AND idem_key = %s",
            (principal.type.value, principal.id, key)).fetchone()
        return (row[0], RunResult.model_validate(loads(row[1]))) if row else None

    def put_run_idempotency(self, principal: PrincipalKey, key: str, body_hash: str,
                            result: RunResult) -> None:
        self._check_open()
        self._idempotency[(principal, key)] = (body_hash, result.model_copy(deep=True))

    def put_handoff(self, handoff_ref: str, packet: dict[str, JsonValue]) -> None:
        self._check_open()
        self._handoffs[handoff_ref] = _copy(packet)

    def get_handoff(self, handoff_ref: str) -> dict[str, JsonValue] | None:
        found = self._handoffs.get(handoff_ref)
        if found is not None:
            return _copy(found)
        row = self._conn.execute(
            "SELECT packet_json FROM handoffs WHERE handoff_ref = %s", (handoff_ref,)).fetchone()
        return _copy(loads(row[0])) if row else None

    # --- auditoría, outbox, costos ---------------------------------------------------------------------

    def append_events(self, run_id: str, events: list[EngineEvent]) -> None:
        self._check_open()
        for event in events:
            if event.run_id != run_id or event.seq is None or event.hash is None or event.prev_hash is None:
                raise ValueError("solo se persisten eventos encadenados del run indicado")
        self._events.setdefault(run_id, []).extend(e.model_copy(deep=True) for e in events)

    def last_event(self, run_id: str) -> EngineEvent | None:
        pending = self._events.get(run_id)
        if pending:
            return pending[-1].model_copy(deep=True)
        return PgAuditEvents(self._conn).last_event(run_id)

    def enqueue_outbox(self, message: OutboxMessage) -> None:
        self._check_open()
        self._outbox.append(message.model_copy(deep=True))

    def add_usage(self, principal: PrincipalKey, cost_usd: Decimal, now: datetime) -> None:
        self._check_open()
        if not cost_usd.is_finite() or cost_usd < 0:
            raise ValueError("el costo debe ser un Decimal finito y no negativo")
        self._usage.append((principal, now, cost_usd))

    # --- commit ----------------------------------------------------------------------------------------

    def commit(self) -> None:
        self._check_open()
        try:
            with self._conn.transaction():  # una sola transacción: se aplica completa o no se aplica
                self._apply()
        except psycopg.errors.UniqueViolation as exc:
            if exc.diag.constraint_name == ONE_OPEN_RUN_INDEX:
                # A second open run in the session (this commit, or another writer that committed first).
                # Nobody retries: neither M4 nor M9 catches `VersionConflict`, so the client gets
                # `500 internal_error` from M9's generic handler (m09 §3.5).
                raise VersionConflict(ONE_OPEN_RUN_MESSAGE) from None
            # Otro escritor encadenó el mismo `seq` (o repitió el `event_id`) sin pasar por `save_run`: es una
            # carrera de versión, no un error de base. No se aplica nada; tampoco hay reintento (M9 responde
            # 500 internal_error).
            raise VersionConflict("la cadena de auditoría cambió: otro escritor commiteó primero") from None
        finally:
            self._done = True

    def _apply(self) -> None:
        conn = self._conn
        # Writes that leave a run not open go first: `runs_one_open_per_session` cannot be deferred and is
        # checked on every row, so the UPDATE that closes a transfer's origin must precede the INSERT of its
        # open target, whatever order the caller saved them in. `sorted` is stable: save order is kept within
        # each group.
        ordered = sorted(self._base_versions.items(), key=lambda item: self._runs[item[0]].status == "open")
        for run_id, base in ordered:
            state = self._runs[run_id]
            values = (state.session_id, state.state_version, state.status, state.inactive_after,
                      dumps(state))
            if base == 0:
                cur = conn.execute(
                    "INSERT INTO runs (run_id, session_id, state_version, status, inactive_after, "
                    "state_json) VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (run_id) DO NOTHING",
                    (run_id, *values))
            else:
                cur = conn.execute(
                    "UPDATE runs SET session_id = %s, state_version = %s, status = %s, inactive_after = %s, "
                    "state_json = %s WHERE run_id = %s AND state_version = %s", (*values, run_id, base))
            if cur.rowcount == 0:
                raise VersionConflict(f"{run_id}: otra transacción commiteó primero (base {base})")
        for (run_id, client_turn_id), result in self._turn_results.items():
            conn.execute(
                "INSERT INTO turn_results (run_id, client_turn_id, result_json) VALUES (%s, %s, %s) "
                "ON CONFLICT (run_id, client_turn_id) DO UPDATE SET result_json = EXCLUDED.result_json",
                (run_id, client_turn_id, dumps(result)))
        for (principal, key), (body_hash, run_result) in self._idempotency.items():
            conn.execute(  # el primer registro gana: un replay no lo pisa
                "INSERT INTO run_idempotency (principal_type, principal_id, idem_key, body_hash, "
                "result_json) VALUES (%s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
                (principal.type.value, principal.id, key, body_hash, dumps(run_result)))
        for handoff_ref, packet in self._handoffs.items():
            conn.execute(
                "INSERT INTO handoffs (handoff_ref, packet_json) VALUES (%s, %s) "
                "ON CONFLICT (handoff_ref) DO UPDATE SET packet_json = EXCLUDED.packet_json",
                (handoff_ref, dumps(packet)))
        for run_id, events in self._events.items():
            PgAuditEvents(conn).append_events(run_id, events)
        for message in self._outbox:
            conn.execute(
                "INSERT INTO outbox (message_id, message_json) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                (message.message_id, dumps(message)))
        for principal, at, cost in self._usage:
            conn.execute(
                "INSERT INTO usage (principal_type, principal_id, at, cost_usd) VALUES (%s, %s, %s, %s)",
                (principal.type.value, principal.id, at, cost))
        for run_id, turn_id in self._released:
            conn.execute("DELETE FROM turn_leases WHERE run_id = %s AND turn_id = %s", (run_id, turn_id))


def _copy(packet: JsonValue) -> dict[str, JsonValue]:
    copied = loads(dumps(packet))
    assert isinstance(copied, dict)
    return copied


class PostgresAuditSink:
    """`AuditSink`: lectura de la cadena y appends fuera de un turno. No encadena (M11 no es importable
    desde adapters): solo acepta eventos ya encadenados; M9 usa `AuditLog.append_standalone`."""

    def __init__(self, store: PostgresStore) -> None:
        self._store = store

    def read(self, run_id: str) -> list[EngineEvent]:
        with self._store.reading() as conn:
            return PgAuditEvents(conn).read(run_id)

    def append_outside_turn(self, run_id: str, events: list[EngineEvent]) -> None:
        with self._store.reading() as conn, conn.transaction():
            PgAuditEvents(conn).append_events(run_id, events)

    # `RunExport` (N-08): lectura paginada para quien ingiere
    def list_runs(self, after_seq: int, limit: int) -> list[RunSummary]:
        with self._store.reading() as conn:
            rows = conn.execute("SELECT run_seq, state_json FROM runs WHERE run_seq > %s ORDER BY run_seq "
                                "LIMIT %s", (after_seq, max(limit, 0))).fetchall()
        return [RunSummary.of(int(r[0]), RunState.model_validate(loads(r[1]))) for r in rows]

    def events_after(self, run_id: str, after_seq: int, limit: int) -> list[EngineEvent]:
        with self._store.reading() as conn:
            return PgAuditEvents(conn).read_after(run_id, after_seq, limit)


class PostgresOutbox:
    """`Outbox`: at-least-once, en orden de inserción; una entrega marcada no se reencola."""

    def __init__(self, store: PostgresStore) -> None:
        self._store = store

    def pending(self, limit: int) -> list[OutboxMessage]:
        if limit <= 0:
            return []
        with self._store.reading() as conn:
            rows = conn.execute("SELECT message_json FROM outbox WHERE delivered_at IS NULL "
                                "ORDER BY seq LIMIT %s", (limit,)).fetchall()
        return [OutboxMessage.model_validate(loads(r[0])) for r in rows]

    def mark_delivered(self, message_id: str) -> None:
        with self._store.reading() as conn:
            conn.execute("UPDATE outbox SET delivered_at = now() WHERE message_id = %s "
                         "AND delivered_at IS NULL", (message_id,))


class PostgresCostCounters:
    """`CostCounters`: lee lo que escribe `add_usage`. "Hoy" = día calendario UTC de `now`."""

    def __init__(self, store: PostgresStore) -> None:
        self._store = store

    def spent_today(self, principal: PrincipalKey, now: datetime) -> Decimal:
        day = now.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        with self._store.reading() as conn:
            row = conn.execute(
                "SELECT COALESCE(SUM(cost_usd), 0) FROM usage WHERE principal_type = %s "
                "AND principal_id = %s AND at >= %s AND at < %s",
                (principal.type.value, principal.id, day, day + timedelta(days=1))).fetchone()
        return Decimal(row[0]) if row else Decimal(0)

    def hits(self, principal: PrincipalKey, window: timedelta, now: datetime) -> int:
        with self._store.reading() as conn:
            row = conn.execute(
                "SELECT count(*) FROM usage WHERE principal_type = %s AND principal_id = %s "
                "AND at > %s AND at <= %s",
                (principal.type.value, principal.id, now - window, now)).fetchone()
        return int(row[0]) if row else 0
