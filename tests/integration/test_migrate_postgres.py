"""`agentcore migrate` sobre Postgres real: crea el esquema completo y se puede repetir."""

import os

import psycopg
import pytest

from agent_core.cli import main
from tests.support.pg import ADMIN_DSN

pytestmark = pytest.mark.integration

SCHEMA = "migrate_test"
EXPECTED = {"runs", "audit_events", "reg_proposal_changes"}


def _tables(conn: "psycopg.Connection[tuple[str]]") -> set[str]:
    rows = conn.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = %s", (SCHEMA,))
    return {row[0] for row in rows}


def test_migrate_creates_every_table_and_can_run_twice(monkeypatch: pytest.MonkeyPatch) -> None:
    try:
        admin = psycopg.connect(ADMIN_DSN, autocommit=True, connect_timeout=3)
    except psycopg.OperationalError:
        if os.environ.get("AGENTCORE_REQUIRE_POSTGRES") == "1":
            raise
        pytest.skip("Postgres no disponible (docker compose up -d postgres)")
    with admin:
        admin.execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")
        admin.execute(f"CREATE SCHEMA {SCHEMA}")
        try:
            separator = "&" if "?" in ADMIN_DSN else "?"
            monkeypatch.setenv("AGENTCORE_REGISTRY_DSN",
                               f"{ADMIN_DSN}{separator}options=-c%20search_path%3D{SCHEMA}")

            assert main(["migrate"]) == 0
            assert EXPECTED <= _tables(admin)
            assert main(["migrate"]) == 0  # idempotente: una base ya migrada no falla
            assert EXPECTED <= _tables(admin)
        finally:
            admin.execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")
