"""`agentcore migrate`: aplica los esquemas (idempotentes) del motor, la auditoría y el registry."""

import argparse
from typing import Any

import psycopg
import pytest

from agent_core.cli import main
from agent_core.composition.migrate import add_migrate_parser, run_migrate


class FakeCursor:
    def fetchone(self) -> tuple[str]:
        return ("public",)  # `SELECT current_schema()`


class FakeConn:
    def __init__(self, dsn: str, log: list["FakeConn"]) -> None:
        self.dsn = dsn
        self.sql: list[str] = []
        log.append(self)

    def __enter__(self) -> "FakeConn":
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def execute(self, query: Any, *params: Any) -> FakeCursor:
        self.sql.append(query if isinstance(query, str) else query.as_string())  # type: ignore[no-untyped-call]
        return FakeCursor()


def _args(*argv: str) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    add_migrate_parser(parser.add_subparsers(dest="command", required=True))
    return parser.parse_args(["migrate", *argv])


def _run(*argv: str, env: dict[str, str] | None = None) -> tuple[int, list[FakeConn]]:
    conns: list[FakeConn] = []
    code = run_migrate(_args(*argv), env or {}, connect=lambda dsn: FakeConn(dsn, conns))  # type: ignore[arg-type,return-value]
    return code, conns


def test_without_a_dsn_it_exits_2_and_names_the_variable(capsys: pytest.CaptureFixture[str]) -> None:
    code, conns = _run()

    assert code == 2 and not conns
    assert "AGENTCORE_REGISTRY_DSN" in capsys.readouterr().err


def test_it_applies_the_engine_audit_and_registry_schemas_to_the_main_database() -> None:
    code, conns = _run("--dsn", "postgresql://a/b")

    assert code == 0 and [c.dsn for c in conns] == ["postgresql://a/b"]
    script = "\n".join(conns[0].sql)
    assert "audit_events" in script and "reg_proposal_changes" in script


def test_the_dsn_can_come_from_the_environment() -> None:
    code, conns = _run(env={"AGENTCORE_REGISTRY_DSN": "postgresql://env/db"})

    assert code == 0 and [c.dsn for c in conns] == ["postgresql://env/db"]


def test_the_evaluation_database_gets_the_engine_schema_but_not_the_registry_one() -> None:
    code, conns = _run("--dsn", "postgresql://a/main", "--eval-dsn", "postgresql://a/eval")

    assert code == 0 and [c.dsn for c in conns] == ["postgresql://a/main", "postgresql://a/eval"]
    eval_script = "\n".join(conns[1].sql)
    assert "audit_events" in eval_script and "reg_proposal_changes" not in eval_script


def test_the_app_role_only_receives_grants() -> None:
    _, conns = _run("--dsn", "postgresql://a/b", "--app-role", "agentcore_app")

    grants = [s for s in conns[0].sql if "GRANT" in s and "agentcore_app" in s]
    assert grants


def test_a_postgres_failure_exits_1_without_leaking_the_dsn_or_the_driver_message(
        capsys: pytest.CaptureFixture[str]) -> None:
    def boom(dsn: str) -> Any:
        raise psycopg.OperationalError("connection failed: host=db.internal password=hunter2")

    code = run_migrate(_args("--dsn", "postgresql://u:hunter2@db.internal/x"), {}, connect=boom)

    err = capsys.readouterr().err
    assert code == 1 and "OperationalError" in err
    assert "hunter2" not in err and "db.internal" not in err


def test_the_subcommand_is_registered_in_the_cli(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as info:
        main(["migrate", "--help"])

    assert info.value.code == 0
    out = capsys.readouterr().out
    assert "--dsn" in out and "--eval-dsn" in out and "--app-role" in out
