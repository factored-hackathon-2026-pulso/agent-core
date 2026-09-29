-- Log de auditoría (M11, ADR 0003 #2): append-only impuesto por la base, no solo por el código.
CREATE TABLE IF NOT EXISTS audit_events (
    run_id     text        NOT NULL,
    seq        integer     NOT NULL CHECK (seq >= 0),
    event_id   text        NOT NULL UNIQUE,
    type       text        NOT NULL,
    release    text        NOT NULL,
    ts         timestamptz NOT NULL,
    prev_hash  char(64)    NOT NULL,
    hash       char(64)    NOT NULL,
    event_json text        NOT NULL,   -- JSON de M0 (`dumps`): conserva Decimal y permite recalcular el hash
    PRIMARY KEY (run_id, seq)
);
CREATE INDEX IF NOT EXISTS audit_events_release_type_idx ON audit_events (release, type);

CREATE OR REPLACE FUNCTION audit_events_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'audit_events es append-only' USING ERRCODE = '42501';
END $$;

DROP TRIGGER IF EXISTS audit_events_no_update ON audit_events;
CREATE TRIGGER audit_events_no_update BEFORE UPDATE OR DELETE ON audit_events
    FOR EACH ROW EXECUTE FUNCTION audit_events_immutable();
DROP TRIGGER IF EXISTS audit_events_no_truncate ON audit_events;
CREATE TRIGGER audit_events_no_truncate BEFORE TRUNCATE ON audit_events
    FOR EACH STATEMENT EXECUTE FUNCTION audit_events_immutable();

REVOKE ALL ON audit_events FROM PUBLIC;
