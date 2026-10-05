"""Subcomando `agentcore serve`: ayuda, protección de demo y arranque de uvicorn (inyectado)."""

import argparse
from typing import Any

import pytest

from agent_core.cli import main
from agent_core.composition import serve as serve_module
from testing.engine_world import EngineWorld
from testing.fakes.identity import TestIdentityIssuer
from tests.composition.test_serve_app import make_ports

pytest_plugins = ["tests.support.otel"]


def test_serve_help_lists_the_piece_options(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as info:
        main(["serve", "--help"])
    assert info.value.code == 0
    out = capsys.readouterr().out
    for option in ("--host", "--port", "--dsn", "--identity-keys", "--tools", "--authz", "--transcript"):
        assert option in out


def test_serve_without_demo_and_without_pieces_exits_2_and_names_them(
        capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, root_logging: None,
        no_otel_env: None) -> None:
    monkeypatch.delenv("AGENTCORE_ALLOW_DEMO", raising=False)
    monkeypatch.delenv("AGENTCORE_ALLOW_DOUBLES", raising=False)
    code = main(["serve", "--dsn", "postgresql://x/y"])
    err = capsys.readouterr().err
    assert code == 2 and "AGENTCORE_IDENTITY_KEYS_FILE" in err


def test_run_serve_prints_the_doubles_and_starts_uvicorn(
        capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, root_logging: None) -> None:
    world = EngineWorld()
    issuer = TestIdentityIssuer(world.clock)
    monkeypatch.setattr(serve_module, "resolve_ports",
                        lambda *a, **k: make_ports(world, issuer, doubles=("tools", "identity")))
    started: dict[str, Any] = {}

    def fake_uvicorn(app: object, **kwargs: Any) -> None:
        started["app"] = app
        started.update(kwargs)

    code = serve_module.run_serve(argparse.Namespace(host="127.0.0.1", port=8123), clock=world.clock,
                                  ids=world.ids, env={}, serve=fake_uvicorn)
    err = capsys.readouterr().err
    assert code == 0
    assert started["host"] == "127.0.0.1" and started["port"] == 8123 and started["app"] is not None
    assert "DOBLES" in err and "tools" in err and "identity" in err


def test_run_serve_with_real_pieces_prints_no_warning(
        capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, root_logging: None) -> None:
    world = EngineWorld()
    monkeypatch.setattr(serve_module, "resolve_ports",
                        lambda *a, **k: make_ports(world, TestIdentityIssuer(world.clock)))
    code = serve_module.run_serve(argparse.Namespace(host="h", port=1), clock=world.clock, ids=world.ids,
                                  env={}, serve=lambda app, **kw: None)
    assert code == 0 and "DOBLES" not in capsys.readouterr().err
