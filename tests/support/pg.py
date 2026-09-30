"""Postgres de desarrollo para las pruebas (docker compose). Sin él se omiten, salvo con
`AGENTCORE_REQUIRE_POSTGRES=1` (CI). Cada prueba usa un esquema propio, que se borra al terminar."""

import os
from collections.abc import Iterator
from contextlib import contextmanager

import psycopg
import pytest

from agent_core.adapters.postgres_uow import PostgresStore, apply_schema

# Credencial de desarrollo local de docker-compose.yml (no es secreta).
ADMIN_DSN = os.environ.get("AGENTCORE_TEST_DATABASE_URL",
                           "postgresql://agentcore:agentcore-dev-only@127.0.0.1:5432/agentcore")


@contextmanager
def postgres_store(schema: str) -> Iterator[PostgresStore]:
    try:
        conn = psycopg.connect(ADMIN_DSN, autocommit=True, connect_timeout=3)
    except psycopg.OperationalError:
        if os.environ.get("AGENTCORE_REQUIRE_POSTGRES") == "1":
            raise
        pytest.skip("Postgres no disponible (docker compose up -d postgres)")
    with conn:
        conn.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
        conn.execute(f"CREATE SCHEMA {schema}")
        conn.execute(f"SET search_path TO {schema}")
        apply_schema(conn)
        try:
            yield PostgresStore(ADMIN_DSN, schema=schema)
        finally:
            conn.execute("RESET search_path")
            conn.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
