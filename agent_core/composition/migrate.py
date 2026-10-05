"""`agentcore migrate`: aplica los esquemas de Postgres (idempotentes) antes de arrancar o actualizar.

Motor y auditoría en la base principal junto con el registry; las evaluaciones del registry tienen su propia
base (`--eval-dsn`) con el esquema del motor. Cada script es `CREATE ... IF NOT EXISTS`; la versión es el
resumen de los scripts (`schema_version`), guardado en la base y aplicado bajo un candado asesor. `serve`
migra solo al arrancar con la misma función; este comando siempre re-aplica (permisos del rol incluidos)."""

import argparse
import sys
from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager
from typing import Any

import psycopg

from agent_core.adapters.postgres_uow import apply_schema
from agent_core.composition.blobs import BUCKET_ENV
from agent_core.composition.schema_version import ensure_schema
from agent_core.composition.serve_ports import DSN_ENV, EVAL_DSN_ENV
from agent_core.registry import apply_registry_schema
from agent_core.registry.postgres.store import detach_blob_foreign_key

Connect = Callable[[str], AbstractContextManager["psycopg.Connection[Any]"]]


def add_migrate_parser(sub: Any) -> None:
    migrate = sub.add_parser("migrate", help="aplica los esquemas de Postgres (idempotente)")
    migrate.add_argument("--dsn", default=None,
                         help=f"DSN de la base del motor y el registry; prefiere {DSN_ENV}: por argv la "
                              "contraseña queda visible en la lista de procesos. No se imprime nunca")
    migrate.add_argument("--eval-dsn", default=None,
                         help=f"DSN de la base de las evaluaciones del registry (o {EVAL_DSN_ENV}); opcional")
    migrate.add_argument("--app-role", default=None,
                         help="rol de la aplicación: recibe solo los permisos mínimos sobre las tablas "
                              "de solo inserción")


def _connect(dsn: str) -> AbstractContextManager["psycopg.Connection[Any]"]:
    return psycopg.connect(dsn)


def _apply_main(app_role: str | None, env: Mapping[str, str]) -> Callable[["psycopg.Connection[Any]"], None]:
    def apply(conn: "psycopg.Connection[Any]") -> None:
        apply_schema(conn, app_role)
        apply_registry_schema(conn, app_role)
        if env.get(BUCKET_ENV, "").strip():  # blobs en S3: la FK a `reg_blobs` ya no puede cumplirse
            detach_blob_foreign_key(conn)

    return apply


def _apply_eval(app_role: str | None) -> Callable[["psycopg.Connection[Any]"], None]:
    return lambda conn: apply_schema(conn, app_role)


def migrate_databases(dsn: str, eval_dsn: str | None, env: Mapping[str, str], *, app_role: str | None = None,
                      force: bool = False, connect: Connect = _connect) -> list[str]:
    """Lleva la base principal (y la de evaluación, si hay) a la versión esperada, bajo el candado asesor.
    Devuelve los ámbitos que migró. Lanza `psycopg.Error`."""
    done: list[str] = []
    with connect(dsn) as conn:
        if ensure_schema(conn, "main", _apply_main(app_role, env), force=force):
            done.append("main")
    if eval_dsn:
        with connect(eval_dsn) as conn:
            if ensure_schema(conn, "eval", _apply_eval(app_role), force=force):
                done.append("eval")
    return done


def run_migrate(args: argparse.Namespace, env: Mapping[str, str], *, connect: Connect = _connect) -> int:
    dsn = args.dsn or env.get(DSN_ENV)
    eval_dsn = args.eval_dsn or env.get(EVAL_DSN_ENV)
    if not dsn:
        print(f"agentcore migrate necesita --dsn (o {DSN_ENV})", file=sys.stderr)
        return 2
    try:
        migrate_databases(dsn, eval_dsn, env, app_role=args.app_role, force=True, connect=connect)
    except psycopg.Error as error:  # el mensaje de psycopg puede traer el host o la contraseña: solo la clase
        print(f"migrate: falla de Postgres ({type(error).__name__})", file=sys.stderr)
        return 1
    print("ok: motor, auditoría y registry")
    if eval_dsn:
        print("ok: evaluaciones")
    return 0
