"""`agentcore migrate`: aplica los esquemas de Postgres (idempotentes) antes de arrancar o actualizar.

Motor y auditoría en la base principal junto con el registry; las evaluaciones del registry tienen su propia
base (`--eval-dsn`) con el esquema del motor. No hay migraciones versionadas todavía: cada script es
`CREATE ... IF NOT EXISTS` y sirve para una base nueva o ya migrada con el mismo esquema."""

import argparse
import sys
from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager
from typing import Any

import psycopg

from agent_core.adapters.postgres_uow import apply_schema
from agent_core.composition.serve_ports import DSN_ENV, EVAL_DSN_ENV
from agent_core.registry import apply_registry_schema

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


def run_migrate(args: argparse.Namespace, env: Mapping[str, str], *, connect: Connect = _connect) -> int:
    dsn = args.dsn or env.get(DSN_ENV)
    eval_dsn = args.eval_dsn or env.get(EVAL_DSN_ENV)
    if not dsn:
        print(f"agentcore migrate necesita --dsn (o {DSN_ENV})", file=sys.stderr)
        return 2
    try:
        with connect(dsn) as conn:
            apply_schema(conn, args.app_role)
            apply_registry_schema(conn, args.app_role)
        print("ok: motor, auditoría y registry")
        if eval_dsn:
            with connect(eval_dsn) as conn:
                apply_schema(conn, args.app_role)
            print("ok: evaluaciones")
    except psycopg.Error as error:  # el mensaje de psycopg puede traer el host o la contraseña: solo la clase
        print(f"migrate: falla de Postgres ({type(error).__name__})", file=sys.stderr)
        return 1
    return 0
