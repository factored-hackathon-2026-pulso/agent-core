"""`agentcore sweep`: misma DSN que `serve`/`migrate`; registry en Postgres sin directorio local."""

from typing import Any

import pytest

from agent_core.cli import build_sweeper, main
from agent_core.registry import PostgresRegistry
from agent_core.turn import SweepReport


class FakeSweeper:
    def __init__(self) -> None:
        self.calls = 0

    def sweep(self, now: Any) -> SweepReport:
        self.calls += 1
        return SweepReport(evaluated=3, abandoned=2, skipped=1)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AGENTCORE_REGISTRY_DSN", raising=False)
    monkeypatch.delenv("AGENTCORE_DATABASE_URL", raising=False)


def test_without_a_dsn_it_exits_2_and_names_the_serve_variable(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["sweep", "--once"]) == 2
    assert "AGENTCORE_REGISTRY_DSN" in capsys.readouterr().err


def test_a_fake_sweeper_runs_without_registry_dir_and_reports(capsys: pytest.CaptureFixture[str]) -> None:
    fake = FakeSweeper()

    assert main(["sweep", "--once"], sweeper=fake) == 0
    assert fake.calls == 1
    assert "evaluated=3 abandoned=2 skipped=1" in capsys.readouterr().out


def test_the_serve_dsn_variable_is_enough_to_compose_the_sweeper(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENTCORE_REGISTRY_DSN", "postgresql://u:p@db/agentcore")
    built: list[tuple[str, Any]] = []

    def fake_build(dsn: str, registry_root: Any = None) -> FakeSweeper:
        built.append((dsn, registry_root))
        return FakeSweeper()

    monkeypatch.setattr("agent_core.cli.build_sweeper", fake_build)

    assert main(["sweep", "--once"]) == 0
    assert built == [("postgresql://u:p@db/agentcore", None)]


def test_the_legacy_variable_still_works(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENTCORE_DATABASE_URL", "postgresql://legacy/db")
    built: list[str] = []
    monkeypatch.setattr("agent_core.cli.build_sweeper",
                        lambda dsn, registry_root=None: built.append(dsn) or FakeSweeper())

    assert main(["sweep", "--once"]) == 0
    assert built == ["postgresql://legacy/db"]


def test_without_a_local_directory_the_registry_is_the_postgres_one() -> None:
    sweeper = build_sweeper("postgresql://u:p@localhost:1/none")  # compone sin conectar

    assert isinstance(sweeper._registry, PostgresRegistry)
