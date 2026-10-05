-- Esquema plano de M4 (estado del run, lease, resultados, idempotencia, handoffs, outbox, costos).
-- El log de auditoría está en audit_events.sql (append-only). Idempotente: se puede aplicar varias veces.
-- Los JSON se guardan como `text` (JSON de M0 `dumps`): conservan Decimal y coinciden con lo que hashea M11.

CREATE TABLE IF NOT EXISTS runs (
    run_id         text        PRIMARY KEY,
    run_seq        bigserial   NOT NULL,          -- creation order (the runs of a session, in order)
    session_id     text,
    state_version  integer     NOT NULL CHECK (state_version >= 1),
    status         text        NOT NULL,
    inactive_after timestamptz,
    state_json     text        NOT NULL
);
-- Cursor de la exportación (N-08): la transacción que escribió el run por última vez. A diferencia de `run_seq`
-- (se asigna al insertar), ordena por commit y cambia cuando el run cambia (p. ej. al cerrarse).
ALTER TABLE runs ADD COLUMN IF NOT EXISTS change_xid bigint NOT NULL DEFAULT (pg_current_xact_id()::text::bigint);
CREATE INDEX IF NOT EXISTS runs_change_idx ON runs (change_xid);
CREATE INDEX IF NOT EXISTS runs_session_idx ON runs (session_id, run_seq DESC) WHERE session_id IS NOT NULL;
-- At most one open run per session (ADR 0021 D1; transfer spec §5.3). A transfer closes the origin and opens the
-- target in one commit. A partial unique index cannot be deferred and Postgres checks it on every row written, so
-- `PostgresUoW._apply` applies the writes that leave a run not open first (the UPDATE that closes the origin runs
-- before the INSERT of the target). A violation arrives as `UniqueViolation` with
-- `constraint_name = 'runs_one_open_per_session'`, and the UoW maps it to `VersionConflict`.
-- `task` runs (no `session_id`) are outside the index.
-- Existing database: if it already holds two open runs of one session, this CREATE fails and `apply_schema`
-- raises (the script is sent as one multi-statement query, so it runs as one implicit transaction). Before
-- applying it, this query must return no rows:
--   SELECT session_id, array_agg(run_id ORDER BY run_seq) FROM runs
--    WHERE status = 'open' AND session_id IS NOT NULL GROUP BY session_id HAVING count(*) > 1;
-- If it returns rows, decide by hand which run of each session stays open. There are no automatic migrations.
-- Not run against a real Postgres in phase 7 (no docker): unverified.
CREATE UNIQUE INDEX IF NOT EXISTS runs_one_open_per_session ON runs (session_id)
    WHERE status = 'open' AND session_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS runs_inactive_idx ON runs (inactive_after, run_id)
    WHERE status = 'open' AND inactive_after IS NOT NULL;

-- Lease de turno: se toma en una sentencia propia (visible de inmediato; no lo deshace un rollback del turno).
CREATE TABLE IF NOT EXISTS turn_leases (
    run_id     text        PRIMARY KEY,
    turn_id    text        NOT NULL,
    expires_at timestamptz NOT NULL
);

CREATE TABLE IF NOT EXISTS turn_results (
    run_id         text NOT NULL,
    client_turn_id text NOT NULL,
    result_json    text NOT NULL,
    PRIMARY KEY (run_id, client_turn_id)
);

-- Idempotencia de `start_run`: el principal forma parte de la clave. Solo el hash del body, nunca el body.
CREATE TABLE IF NOT EXISTS run_idempotency (
    principal_type text NOT NULL,
    principal_id   text NOT NULL,
    idem_key       text NOT NULL,
    body_hash      text NOT NULL,
    result_json    text,           -- NULL mientras la clave está solo reservada (el run aún no commitea)
    reserved_until timestamptz,    -- fin de la reserva; vencida, otra petición puede tomar la clave
    PRIMARY KEY (principal_type, principal_id, idem_key)
);
ALTER TABLE run_idempotency ALTER COLUMN result_json DROP NOT NULL;
ALTER TABLE run_idempotency ADD COLUMN IF NOT EXISTS reserved_until timestamptz;

CREATE TABLE IF NOT EXISTS handoffs (
    handoff_ref text PRIMARY KEY,
    packet_json text NOT NULL
);

-- Bandeja de salida at-least-once: leer no consume; una entrega marcada no se reencola.
CREATE TABLE IF NOT EXISTS outbox (
    message_id   text        PRIMARY KEY,
    seq          bigserial   NOT NULL,
    message_json text        NOT NULL,
    delivered_at timestamptz
);
CREATE INDEX IF NOT EXISTS outbox_pending_idx ON outbox (seq) WHERE delivered_at IS NULL;

CREATE TABLE IF NOT EXISTS usage (
    id             bigserial   PRIMARY KEY,
    principal_type text        NOT NULL,
    principal_id   text        NOT NULL,
    at             timestamptz NOT NULL,
    cost_usd       numeric     NOT NULL CHECK (cost_usd >= 0)
);
CREATE INDEX IF NOT EXISTS usage_principal_idx ON usage (principal_type, principal_id, at);

-- Transcript del run (M11, unidad 7): texto en vista `model`, nunca `full`. Sin clave foránea a `runs`: la
-- supresión del transcript (`delete_run`) y la del estado del run son decisiones separadas, y el log de
-- auditoría no depende de esta tabla (T-M11-08). `entry_id` se deriva de `seq`, así que no hay azar.
CREATE TABLE IF NOT EXISTS transcript_entries (
    seq        bigserial PRIMARY KEY,
    run_id     text      NOT NULL,
    turn_id    text      NOT NULL,
    role       text      NOT NULL,
    text_model text      NOT NULL,
    reason     text
);
CREATE INDEX IF NOT EXISTS transcript_entries_run_idx ON transcript_entries (run_id, seq);
