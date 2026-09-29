"""Postgres de desarrollo (docker compose). Sin él, se omiten salvo AGENTCORE_REQUIRE_POSTGRES=1."""

import os
from collections.abc import Iterator
from typing import Any

import psycopg
import pytest

# Credencial de desarrollo local de docker-compose.yml (no es secreta). El rol de aplicación es de prueba.
ADMIN_DSN = os.environ.get("AGENTCORE_TEST_DATABASE_URL",
                           "postgresql://agentcore:agentcore-dev-only@127.0.0.1:5432/agentcore")
APP_ROLE, APP_PASSWORD = "agentcore_audit_app", "app-dev-only"
SCHEMA = "m11_test"


@pytest.fixture
def admin_conn() -> Iterator["psycopg.Connection[Any]"]:
    try:
        conn = psycopg.connect(ADMIN_DSN, autocommit=True, connect_timeout=3)
    except psycopg.OperationalError:
        if os.environ.get("AGENTCORE_REQUIRE_POSTGRES") == "1":
            raise
        pytest.skip("Postgres no disponible (docker compose up -d postgres)")
    with conn:
        conn.execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")
        conn.execute(f"CREATE SCHEMA {SCHEMA}")
        conn.execute(f"SET search_path TO {SCHEMA}")
        conn.execute(
            f"DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '{APP_ROLE}') THEN "
            f"CREATE ROLE {APP_ROLE} LOGIN PASSWORD '{APP_PASSWORD}'; END IF; END $$")
        conn.execute(f"GRANT USAGE ON SCHEMA {SCHEMA} TO {APP_ROLE}")
        yield conn
        conn.execute("RESET search_path")
        conn.execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")


@pytest.fixture
def app_conn(admin_conn: "psycopg.Connection[Any]") -> Iterator["psycopg.Connection[Any]"]:
    """Conexión con el rol de la aplicación: solo SELECT e INSERT sobre audit_events."""
    from agent_core.adapters.postgres_audit import apply_audit_schema

    apply_audit_schema(admin_conn, app_role=APP_ROLE)
    dsn = ADMIN_DSN.replace("agentcore:agentcore-dev-only", f"{APP_ROLE}:{APP_PASSWORD}")
    with psycopg.connect(dsn, autocommit=False, options=f"-c search_path={SCHEMA}") as conn:
        yield conn
