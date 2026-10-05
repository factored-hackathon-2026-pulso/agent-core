"""Modo de `agentcore serve` (brief A2.3): `AGENTCORE_ALLOW_DOUBLES`, alias heredado y modo en el arranque."""

import argparse
from typing import Any

import pytest

from agent_core.composition import serve as serve_module
from agent_core.composition.serve_ports import ServeConfigError, ServePorts, doubles_allowed
from testing.engine_world import EngineWorld
from testing.fakes.identity import TestIdentityIssuer
from tests.composition.test_serve_app import make_ports
from tests.composition.test_serve_ports import _real_args, _resolve

pytest_plugins = ["tests.support.otel"]

ALL_DOUBLES = {"tools", "authz", "transcript", "calibration", "classifier", "field-classifier",
               "grant-active", "identity"}


@pytest.mark.parametrize("env", [{"AGENTCORE_ALLOW_DOUBLES": "1"}, {"AGENTCORE_ALLOW_DEMO": "1"}])
def test_either_switch_allows_the_demo_doubles(env: dict[str, str]) -> None:
    assert doubles_allowed(env)
    assert set(_resolve(**env).doubles) == ALL_DOUBLES


@pytest.mark.parametrize("env", [{}, {"AGENTCORE_ALLOW_DOUBLES": "0"}, {"AGENTCORE_ALLOW_DOUBLES": "true"},
                                 {"AGENTCORE_ALLOW_DEMO": ""}])
def test_without_a_switch_equal_to_1_doubles_are_not_allowed(env: dict[str, str]) -> None:
    assert not doubles_allowed(env)


def test_the_refusal_names_the_new_switch() -> None:
    with pytest.raises(ServeConfigError) as info:
        _resolve("--tools", "testing.serve_demo:tools")
    text = " ".join(info.value.problems)
    assert "AGENTCORE_ALLOW_DOUBLES=1" in text and "AGENTCORE_ALLOW_DEMO" not in text


def test_with_the_switch_on_only_the_pieces_that_are_doubles_are_listed(tmp_path: object) -> None:
    ports = _resolve(*_real_args(tmp_path), AGENTCORE_ALLOW_DOUBLES="1")
    assert ports.doubles == ()  # every piece and the identity file are real


def test_a_single_double_among_real_pieces_is_the_only_one_listed(tmp_path: object) -> None:
    ports = _resolve(*_real_args(tmp_path, tools="testing.serve_demo:tools"), AGENTCORE_ALLOW_DOUBLES="1")
    assert ports.doubles == ("tools",)


def _start(ports: ServePorts, monkeypatch: pytest.MonkeyPatch, world: EngineWorld,
           env: dict[str, str]) -> int:
    monkeypatch.setattr(serve_module, "resolve_ports", lambda *a, **k: ports)
    monkeypatch.setattr(serve_module, "gateway_is_up", lambda url: True)
    return serve_module.run_serve(argparse.Namespace(host="h", port=1), clock=world.clock, ids=world.ids,
                                  env=env, serve=lambda app, **kw: None)


def _ports(world: EngineWorld, **over: Any) -> ServePorts:
    base = make_ports(world, TestIdentityIssuer(world.clock))
    return base.__class__(**{**base.__dict__, **over})


def test_startup_logs_production_mode_when_nothing_is_a_double(
        capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, root_logging: None) -> None:
    world = EngineWorld()
    assert _start(_ports(world, doubles=()), monkeypatch, world, {}) == 0
    err = capsys.readouterr().err
    assert '"message": "serve mode=production"' in err


def test_startup_logs_demo_mode_and_the_doubles(
        capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, root_logging: None) -> None:
    world = EngineWorld()
    ports = _ports(world, doubles=("tools", "authz"))
    assert _start(ports, monkeypatch, world, {"AGENTCORE_ALLOW_DOUBLES": "1"}) == 0
    err = capsys.readouterr().err
    assert '"message": "serve mode=demo doubles=tools,authz"' in err
    assert "DOBLES de demo" in err


def test_the_legacy_switch_still_starts_but_asks_to_migrate(
        capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, root_logging: None) -> None:
    world = EngineWorld()
    assert _start(_ports(world, doubles=("tools",)), monkeypatch, world, {"AGENTCORE_ALLOW_DEMO": "1"}) == 0
    err = capsys.readouterr().err
    assert "AGENTCORE_ALLOW_DEMO" in err and "AGENTCORE_ALLOW_DOUBLES" in err


def test_the_new_switch_alone_gives_no_migration_notice(
        capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, root_logging: None) -> None:
    world = EngineWorld()
    env = {"AGENTCORE_ALLOW_DOUBLES": "1"}
    assert _start(_ports(world, doubles=("tools",)), monkeypatch, world, env) == 0
    assert "AGENTCORE_ALLOW_DEMO" not in capsys.readouterr().err
