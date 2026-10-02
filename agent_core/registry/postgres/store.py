"""`RegistryStore` sobre Postgres: una conexión por transacción, FOR UPDATE en propuesta y alias."""

from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from importlib import resources
from typing import Any

import psycopg
from psycopg import sql

from agent_core.domain import Release, dumps, loads
from agent_core.registry.blobs import BlobStore, verified
from agent_core.registry.errors import IntegrityError
from agent_core.registry.evaluation.report import EvalReport
from agent_core.registry.models import (
    AliasChange,
    Approval,
    EntityDraft,
    EvalRun,
    Proposal,
    RegistryEvent,
    StoredRelease,
    StoredVersion,
    VersionDocs,
    VersionRef,
)
from agent_core.registry.store import RegistryTx, Status

_INSERT_ONLY = ("reg_blobs", "reg_entity_versions", "reg_releases", "reg_release_entities",
                "reg_release_eval_suites", "reg_approvals", "reg_eval_runs", "reg_events", "reg_alias_log")
# Mutables y controladas (spec §3.2): el rol de la aplicación nunca borra; cada tabla tiene lo mínimo que usa.
_GRANTS_MUTABLE = {
    "reg_release_status": "SELECT, INSERT, UPDATE",   # alta al publicar, `revoke`
    "reg_aliases": "SELECT, INSERT, UPDATE",          # `publish` (staging) y `promote`
    "reg_publish_keys": "SELECT, INSERT",             # idempotencia: solo se escribe una vez
    "reg_proposals": "SELECT, INSERT, UPDATE",
    "reg_proposal_changes": "SELECT, INSERT, UPDATE",
}


def apply_registry_schema(conn: "psycopg.Connection[Any]", app_role: str | None = None) -> None:
    conn.execute(resources.files("agent_core.registry.postgres").joinpath("schema.sql").read_text("utf-8"))
    if app_role is None:
        return
    role = sql.Identifier(app_role)
    for table in _INSERT_ONLY:
        conn.execute(sql.SQL("GRANT SELECT, INSERT ON {} TO {}").format(sql.Identifier(table), role))
    for table, privileges in _GRANTS_MUTABLE.items():
        grant = sql.SQL("GRANT {} ON {} TO {}").format(sql.SQL(privileges), sql.Identifier(table), role)
        conn.execute(grant)
    row = conn.execute("SELECT current_schema()").fetchone()
    assert row is not None
    seqs = sql.SQL("GRANT USAGE ON ALL SEQUENCES IN SCHEMA {} TO {}")
    conn.execute(seqs.format(sql.Identifier(row[0]), role))


class _PgBlobs:
    def __init__(self, conn: "psycopg.Connection[Any]") -> None:
        self._c = conn

    def put(self, data: bytes) -> str:
        from agent_core.domain import sha256_hex
        digest = sha256_hex(data)
        self._c.execute("INSERT INTO reg_blobs (hash, bytes) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                        (digest, data))
        return digest

    def get(self, digest: str) -> bytes:
        row = self._c.execute("SELECT bytes FROM reg_blobs WHERE hash = %s", (digest,)).fetchone()
        if row is None:
            raise IntegrityError(f"no existe el contenido {digest[:12]}…")
        return verified(digest, bytes(row[0]))


def _ref(kind: str, ident: str, version: str) -> VersionRef:
    return VersionRef(kind=kind, id=ident, version=version)


class _PgTx:
    def __init__(self, conn: "psycopg.Connection[Any]", fail_on: Callable[[str], bool] | None) -> None:
        self._c = conn
        self._fail_on = fail_on
        self.blobs: BlobStore = _PgBlobs(conn)

    def __getattribute__(self, name: str) -> Any:
        fail_on = object.__getattribute__(self, "_fail_on")
        if fail_on is not None and not name.startswith("_") and fail_on(name):
            raise RuntimeError(f"falla inyectada en {name}")
        return object.__getattribute__(self, name)

    def _one(self, query: str, params: tuple[Any, ...]) -> Any:
        return self._c.execute(query, params).fetchone()

    # propuestas
    def get_proposal(self, proposal_id: str, *, for_update: bool = False) -> Proposal | None:
        lock = " FOR UPDATE" if for_update else ""
        row = self._one("SELECT proposal_json FROM reg_proposals WHERE proposal_id = %s" + lock,
                        (proposal_id,))
        return Proposal.model_validate(loads(row[0])) if row else None

    def save_proposal(self, proposal: Proposal) -> None:
        self._c.execute("INSERT INTO reg_proposals (proposal_id, proposal_json) VALUES (%s, %s) "
                        "ON CONFLICT (proposal_id) DO UPDATE SET proposal_json = EXCLUDED.proposal_json",
                        (proposal.proposal_id, dumps(proposal)))

    def get_changes(self, proposal_id: str) -> list[EntityDraft]:
        row = self._one("SELECT drafts_json FROM reg_proposal_changes WHERE proposal_id = %s", (proposal_id,))
        if not row:
            return []
        drafts = loads(row[0])
        assert isinstance(drafts, list)
        return [EntityDraft.model_validate(d) for d in drafts]

    def replace_changes(self, proposal_id: str, drafts: Sequence[EntityDraft]) -> None:
        self._c.execute("INSERT INTO reg_proposal_changes (proposal_id, drafts_json) VALUES (%s, %s) "
                        "ON CONFLICT (proposal_id) DO UPDATE SET drafts_json = EXCLUDED.drafts_json",
                        (proposal_id, dumps([d.model_dump(mode="json") for d in drafts])))

    # versiones
    def _version(self, row: Any) -> StoredVersion:
        return StoredVersion(ref=_ref(row[0], row[1], row[2]), content_hash=row[3],
                             docs=VersionDocs.model_validate(loads(row[4])), proposal_id=row[5],
                             created_by=row[6], created_at=row[7])

    _VCOLS = "kind, id, version, content_hash, docs, proposal_id, created_by, created_at"

    def get_version(self, ref: VersionRef) -> StoredVersion | None:
        row = self._one(f"SELECT {self._VCOLS} FROM reg_entity_versions "
                        "WHERE kind = %s AND id = %s AND version = %s", (ref.kind, ref.id, ref.version))
        return self._version(row) if row else None

    def list_versions(self, kind: str, entity_id: str) -> list[StoredVersion]:
        rows = self._c.execute(f"SELECT {self._VCOLS} FROM reg_entity_versions WHERE kind = %s AND id = %s",
                               (kind, entity_id)).fetchall()
        return [self._version(r) for r in rows]

    def insert_version(self, v: StoredVersion) -> None:
        self._c.execute(f"INSERT INTO reg_entity_versions ({self._VCOLS}) "
                        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                        (v.ref.kind, v.ref.id, v.ref.version, v.content_hash, dumps(v.docs), v.proposal_id,
                         v.created_by, v.created_at))

    # releases
    def get_release(self, release_id: str) -> StoredRelease | None:
        row = self._one("SELECT release_json, release_hash, agent_id, agent_version, base_release_id, "
                        "proposal_id, published_by, published_at FROM reg_releases WHERE release_id = %s",
                        (release_id,))
        if row is None:
            return None
        suites = self._c.execute("SELECT kind, id, version FROM reg_release_eval_suites "
                                 "WHERE release_id = %s ORDER BY id", (release_id,)).fetchall()
        return StoredRelease(release=Release.model_validate(loads(row[0])), release_hash=row[1],
                             agent_id=row[2], agent_version=row[3], base_release_id=row[4],
                             proposal_id=row[5], published_by=row[6], published_at=row[7],
                             eval_suite_refs=[_ref(*r) for r in suites])

    def release_refs(self, release_id: str) -> list[VersionRef]:
        rows = self._c.execute("SELECT kind, id, version FROM reg_release_entities WHERE release_id = %s "
                               "ORDER BY kind, id", (release_id,)).fetchall()
        return [_ref(*r) for r in rows]

    def insert_release(self, s: StoredRelease, refs: Sequence[VersionRef]) -> None:
        self._c.execute("INSERT INTO reg_releases (release_id, release_json, release_hash, agent_id, "
                        "agent_version, base_release_id, proposal_id, published_by, published_at) "
                        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                        (s.release.id, dumps(s.release), s.release_hash, s.agent_id, s.agent_version,
                         s.base_release_id, s.proposal_id, s.published_by, s.published_at))
        with self._c.cursor() as cur:
            cur.executemany("INSERT INTO reg_release_entities (release_id, kind, id, version) "
                            "VALUES (%s, %s, %s, %s)",
                            [(s.release.id, r.kind, r.id, r.version) for r in refs])
        if s.eval_suite_refs:
            with self._c.cursor() as cur:
                cur.executemany("INSERT INTO reg_release_eval_suites (release_id, kind, id, version) "
                                "VALUES (%s, %s, %s, %s)",
                                [(s.release.id, r.kind, r.id, r.version) for r in s.eval_suite_refs])
        self._c.execute("INSERT INTO reg_release_status (release_id, status) VALUES (%s, 'active')",
                        (s.release.id,))

    def release_status(self, release_id: str) -> Status | None:
        row = self._one("SELECT status FROM reg_release_status WHERE release_id = %s", (release_id,))
        return row[0] if row else None

    def set_release_status(self, release_id: str, status: Status) -> None:
        self._c.execute("UPDATE reg_release_status SET status = %s WHERE release_id = %s",
                        (status, release_id))

    def latest_release_for_agent_version(self, agent_id: str, version: str) -> str | None:
        row = self._one("SELECT release_id FROM reg_releases WHERE agent_id = %s AND agent_version = %s "
                        "ORDER BY published_at DESC, release_id DESC LIMIT 1", (agent_id, version))
        return row[0] if row else None

    # alias
    def lock_agent(self, agent_id: str) -> None:
        self._c.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (agent_id,))

    def get_alias(self, agent_id: str, alias: str, *, for_update: bool = False) -> str | None:
        lock = " FOR UPDATE" if for_update else ""
        row = self._one("SELECT release_id FROM reg_aliases WHERE agent_id = %s AND alias = %s" + lock,
                        (agent_id, alias))
        return row[0] if row else None

    def set_alias(self, change: AliasChange) -> None:
        self._c.execute("INSERT INTO reg_aliases (agent_id, alias, release_id) VALUES (%s, %s, %s) "
                        "ON CONFLICT (agent_id, alias) DO UPDATE SET release_id = EXCLUDED.release_id",
                        (change.agent_id, change.alias, change.after))
        self._c.execute("INSERT INTO reg_alias_log (change_json) VALUES (%s)", (dumps(change),))

    def aliases_to(self, release_id: str) -> list[tuple[str, str]]:
        rows = self._c.execute("SELECT agent_id, alias FROM reg_aliases WHERE release_id = %s ORDER BY 1, 2",
                               (release_id,)).fetchall()
        return [(r[0], r[1]) for r in rows]

    # evaluación y aprobaciones
    def insert_eval_run(self, run: EvalRun) -> None:
        self._c.execute("INSERT INTO reg_eval_runs (eval_run_id, proposal_id, candidate_hash, "
                        "base_release_id, suite, verdict, report, at) "
                        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                        (run.eval_run_id, run.proposal_id, run.candidate_hash, run.base_release_id,
                         dumps(run.suite), run.verdict, dumps(run.report), run.at))

    def latest_eval_run(self, proposal_id: str, candidate_hash: str) -> EvalRun | None:
        row = self._one("SELECT eval_run_id, base_release_id, suite, verdict, report, at FROM reg_eval_runs "
                        "WHERE proposal_id = %s AND candidate_hash = %s ORDER BY seq DESC LIMIT 1",
                        (proposal_id, candidate_hash))
        if row is None:
            return None
        return EvalRun(eval_run_id=row[0], proposal_id=proposal_id, candidate_hash=candidate_hash,
                       base_release_id=row[1], suite=VersionRef.model_validate(loads(row[2])), verdict=row[3],
                       report=EvalReport.model_validate(loads(row[4])), at=row[5])

    def insert_approval(self, a: Approval) -> None:
        self._c.execute("INSERT INTO reg_approvals (proposal_id, candidate_hash, actor, decision, "
                        "reason, at) VALUES (%s, %s, %s, %s, %s, %s)",
                        (a.proposal_id, a.candidate_hash, a.actor, a.decision, a.reason, a.at))

    def latest_approval(self, proposal_id: str, candidate_hash: str) -> Approval | None:
        row = self._one("SELECT actor, decision, reason, at FROM reg_approvals WHERE proposal_id = %s "
                        "AND candidate_hash = %s ORDER BY seq DESC LIMIT 1", (proposal_id, candidate_hash))
        if row is None:
            return None
        return Approval(proposal_id=proposal_id, candidate_hash=candidate_hash, actor=row[0],
                        decision=row[1], reason=row[2], at=row[3])

    def append_event(self, event: RegistryEvent) -> None:
        self._c.execute("INSERT INTO reg_events (event_json) VALUES (%s)", (dumps(event),))

    def events(self) -> list[RegistryEvent]:
        rows = self._c.execute("SELECT event_json FROM reg_events ORDER BY seq").fetchall()
        return [RegistryEvent.model_validate(loads(r[0])) for r in rows]

    def get_publish_key(self, key: str) -> tuple[str, str] | None:
        row = self._one("SELECT proposal_id, release_id FROM reg_publish_keys WHERE key = %s", (key,))
        return (row[0], row[1]) if row else None

    def put_publish_key(self, key: str, proposal_id: str, release_id: str) -> None:
        self._c.execute("INSERT INTO reg_publish_keys (key, proposal_id, release_id) VALUES (%s, %s, %s)",
                        (key, proposal_id, release_id))


class PgRegistryStore:
    def __init__(self, connect: Callable[[], "psycopg.Connection[Any]"]) -> None:
        self.connect = connect
        self.fail_on: Callable[[str], bool] | None = None

    @contextmanager
    def transaction(self) -> Iterator[RegistryTx]:
        with self.connect() as conn:  # psycopg: commit al salir sin error, rollback con excepción
            yield _PgTx(conn, self.fail_on)
