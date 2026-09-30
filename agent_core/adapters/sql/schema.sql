-- Esquema plano de M4 (estado del run, lease, resultados, idempotencia, handoffs, outbox, costos).
-- El log de auditoría está en audit_events.sql (append-only). Idempotente: se puede aplicar varias veces.
-- Los JSON se guardan como `text` (JSON de M0 `dumps`): conservan Decimal y coinciden con lo que hashea M11.

CREATE TABLE IF NOT EXISTS runs (
    run_id         text        PRIMARY KEY,
    run_seq        bigserial   NOT NULL,          -- orden de creación (una sesión = el run más nuevo)
    session_id     text,
    state_version  integer     NOT NULL CHECK (state_version >= 1),
    status         text        NOT NULL,
    inactive_after timestamptz,
    state_json     text        NOT NULL
);
CREATE INDEX IF NOT EXISTS runs_session_idx ON runs (session_id, run_seq DESC) WHERE session_id IS NOT NULL;
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
    result_json    text NOT NULL,
    PRIMARY KEY (principal_type, principal_id, idem_key)
);

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
