-- Registry (spec §3.2, ADR 0017): inmutabilidad impuesta por permisos y por triggers.
CREATE TABLE IF NOT EXISTS reg_blobs (hash char(64) PRIMARY KEY, bytes bytea NOT NULL);
CREATE TABLE IF NOT EXISTS reg_entity_versions (
    kind text NOT NULL, id text NOT NULL, version text NOT NULL,
    content_hash char(64) NOT NULL REFERENCES reg_blobs(hash),
    docs text NOT NULL, proposal_id text, created_by text NOT NULL, created_at timestamptz NOT NULL,
    PRIMARY KEY (kind, id, version));
CREATE TABLE IF NOT EXISTS reg_releases (
    release_id text PRIMARY KEY, release_json text NOT NULL, release_hash char(64) NOT NULL,
    agent_id text NOT NULL, agent_version text NOT NULL, base_release_id text, proposal_id text,
    published_by text NOT NULL, published_at timestamptz NOT NULL);
CREATE INDEX IF NOT EXISTS reg_releases_agent_version ON reg_releases (agent_id, agent_version, published_at);
CREATE TABLE IF NOT EXISTS reg_release_entities (
    release_id text NOT NULL REFERENCES reg_releases(release_id),
    kind text NOT NULL, id text NOT NULL, version text NOT NULL,
    PRIMARY KEY (release_id, kind, id),
    FOREIGN KEY (kind, id, version) REFERENCES reg_entity_versions(kind, id, version));
-- ADR 0020: the suite the release passed the gate with (old yardstick of the next proposal). Insert-only.
CREATE TABLE IF NOT EXISTS reg_release_eval_suites (
    release_id text NOT NULL REFERENCES reg_releases(release_id),
    kind text NOT NULL DEFAULT 'eval_suite' CHECK (kind = 'eval_suite'),
    id text NOT NULL, version text NOT NULL,
    PRIMARY KEY (release_id, id),
    FOREIGN KEY (kind, id, version) REFERENCES reg_entity_versions(kind, id, version));
CREATE TABLE IF NOT EXISTS reg_approvals (
    seq bigserial PRIMARY KEY, proposal_id text NOT NULL, candidate_hash text NOT NULL, actor text NOT NULL,
    decision text NOT NULL CHECK (decision IN ('approved', 'rejected')), reason text, at timestamptz NOT NULL);
CREATE TABLE IF NOT EXISTS reg_eval_runs (
    eval_run_id text PRIMARY KEY, seq bigserial UNIQUE, proposal_id text NOT NULL, candidate_hash text NOT NULL,
    base_release_id text, suite text NOT NULL, verdict text NOT NULL, report text NOT NULL, at timestamptz NOT NULL);
CREATE TABLE IF NOT EXISTS reg_events (seq bigserial PRIMARY KEY, event_json text NOT NULL);
CREATE TABLE IF NOT EXISTS reg_alias_log (seq bigserial PRIMARY KEY, change_json text NOT NULL);
-- mutables controladas
CREATE TABLE IF NOT EXISTS reg_release_status (
    release_id text PRIMARY KEY REFERENCES reg_releases(release_id),
    status text NOT NULL CHECK (status IN ('active', 'revoked')));
CREATE TABLE IF NOT EXISTS reg_aliases (
    agent_id text NOT NULL, alias text NOT NULL, release_id text NOT NULL REFERENCES reg_releases(release_id),
    PRIMARY KEY (agent_id, alias));
CREATE TABLE IF NOT EXISTS reg_proposals (proposal_id text PRIMARY KEY, proposal_json text NOT NULL);
CREATE TABLE IF NOT EXISTS reg_proposal_changes (proposal_id text PRIMARY KEY, drafts_json text NOT NULL);
CREATE TABLE IF NOT EXISTS reg_publish_keys (
    key text PRIMARY KEY, proposal_id text NOT NULL, release_id text NOT NULL);

CREATE OR REPLACE FUNCTION reg_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION '% es de solo inserción', TG_TABLE_NAME USING ERRCODE = '42501';
END $$;

DO $$
DECLARE t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['reg_blobs', 'reg_entity_versions', 'reg_releases', 'reg_release_entities',
                             'reg_release_eval_suites', 'reg_approvals', 'reg_eval_runs', 'reg_events',
                             'reg_alias_log'] LOOP
        EXECUTE format('DROP TRIGGER IF EXISTS %I_no_update ON %I', t, t);
        EXECUTE format('CREATE TRIGGER %I_no_update BEFORE UPDATE OR DELETE ON %I '
                       'FOR EACH ROW EXECUTE FUNCTION reg_immutable()', t, t);
        EXECUTE format('DROP TRIGGER IF EXISTS %I_no_truncate ON %I', t, t);
        EXECUTE format('CREATE TRIGGER %I_no_truncate BEFORE TRUNCATE ON %I '
                       'FOR EACH STATEMENT EXECUTE FUNCTION reg_immutable()', t, t);
        EXECUTE format('REVOKE ALL ON %I FROM PUBLIC', t);
    END LOOP;
END $$;
