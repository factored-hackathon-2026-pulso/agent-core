"""Versión del esquema de Postgres: un resumen de los scripts, guardado en la propia base.

Los scripts son idempotentes y no están numerados, así que la "versión" es el resumen (sha256 truncado) de los
archivos SQL que componen cada ámbito: cambia sola cuando alguno cambia. `ensure_schema` toma un candado
asesor de transacción, compara con lo guardado y aplica los scripts solo si difiere; así dos instancias que
arrancan a la vez no se pisan y la segunda no repite el trabajo. Nada de esto lee reloj ni imprime DSN."""

import threading
from collections.abc import Callable
from importlib import resources
from typing import Any

import psycopg

from agent_core.domain import sha256_hex

LEDGER = "agentcore_schema_version"
LOCK_KEY = 6_161_616_162  # candado asesor del esquema (el del relay es 6_161_616_161)
_SCRIPTS: dict[str, tuple[tuple[str, str], ...]] = {
    "main": (("agent_core.adapters", "sql/schema.sql"), ("agent_core.adapters", "sql/audit_events.sql"),
             ("agent_core.registry.postgres", "schema.sql")),
    "eval": (("agent_core.adapters", "sql/schema.sql"), ("agent_core.adapters", "sql/audit_events.sql")),
}


def expected_digest(scope: str) -> str:
    """La versión esperada para `scope`: `main` (motor, auditoría y registry) o `eval` (sin registry)."""
    if scope not in _SCRIPTS:
        raise ValueError(f"ámbito de esquema desconocido: {scope}")
    text = "\n".join(resources.files(pkg).joinpath(name).read_text("utf-8") for pkg, name in _SCRIPTS[scope])
    return sha256_hex(text.encode("utf-8"))[:16]


def ensure_schema(conn: "psycopg.Connection[Any]", scope: str,
                  apply: Callable[["psycopg.Connection[Any]"], None], *, force: bool = False) -> bool:
    """Aplica `apply(conn)` si la versión guardada difiere de la esperada (o siempre con `force`: el comando
    `migrate` re-aplica también los permisos del rol); `True` si migró. Todo en la
    transacción de `conn` (sin autocommit), bajo el candado: el commit al salir del contexto lo suelta."""
    wanted = expected_digest(scope)
    conn.execute("SELECT pg_advisory_xact_lock(%s)", (LOCK_KEY,))
    conn.execute(f"CREATE TABLE IF NOT EXISTS {LEDGER} (scope text PRIMARY KEY, digest text NOT NULL, "
                 "applied_at timestamptz NOT NULL DEFAULT now())")
    row = conn.execute(f"SELECT digest FROM {LEDGER} WHERE scope = %s", (scope,)).fetchone()
    if not force and row is not None and row[0] == wanted:
        return False
    apply(conn)
    conn.execute(f"INSERT INTO {LEDGER} (scope, digest) VALUES (%s, %s) "
                 "ON CONFLICT (scope) DO UPDATE SET digest = EXCLUDED.digest, applied_at = now()",
                 (scope, wanted))
    return True


def schema_is_current(dsn: str, scope: str, *, timeout_s: int = 3, options: str | None = None) -> bool:
    """`True` si la base guarda la versión esperada. Falla cerrado y sin detalle (el error de psycopg puede
    traer el host o la contraseña)."""
    try:
        with psycopg.connect(dsn, autocommit=True, connect_timeout=timeout_s, options=options) as conn:
            row = conn.execute(f"SELECT digest FROM {LEDGER} WHERE scope = %s", (scope,)).fetchone()
    except psycopg.Error:
        return False
    return row is not None and row[0] == expected_digest(scope)


class SchemaBootstrap:
    """Migra en segundo plano y reintenta hasta lograrlo: `serve` arranca aunque la base no esté (su
    `/readyz` dice 503 mientras tanto). Solo guarda el tipo del último error, nunca su mensaje."""

    def __init__(self, migrate: Callable[[], None], *, retry_seconds: float = 2.0) -> None:
        self._migrate = migrate
        self._retry = retry_seconds
        self._stop = threading.Event()
        self._done = threading.Event()
        self._thread = threading.Thread(target=self._run, name="schema-bootstrap", daemon=True)
        self.last_error: str | None = None

    @property
    def done(self) -> bool:
        return self._done.is_set()

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread.is_alive():
            self._thread.join(timeout=5)

    def wait(self, timeout_s: float) -> bool:
        return self._done.wait(timeout_s)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self._migrate()
            except Exception as exc:  # cualquier fallo se reintenta; solo se recuerda su tipo
                self.last_error = type(exc).__name__
                self._stop.wait(self._retry)
                continue
            self.last_error = None
            self._done.set()
            return
