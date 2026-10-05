"""`serve` migra al arrancar (en segundo plano) salvo `AGENTCORE_AUTO_MIGRATE=0`; `/readyz` lo vigila."""

import argparse
from typing import Any

import pytest

from agent_core.composition import schema_version as sv
from agent_core.composition import serve as serve_module
from agent_core.composition.serve import AUTO_MIGRATE_ENV
from agent_core.composition.serve_ports import ServePorts
from testing.engine_world import EngineWorld
from testing.fakes.identity import TestIdentityIssuer
from tests.composition.test_serve_app import make_ports
from tests.composition.test_serve_ports import _real_args, _resolve

pytest_plugins = ["tests.support.otel"]


def _start(ports: ServePorts, monkeypatch: pytest.MonkeyPatch, world: EngineWorld,
           env: dict[str, str]) -> int:
    monkeypatch.setattr(serve_module, "resolve_ports", lambda *a, **k: ports)
    monkeypatch.setattr(serve_module, "gateway_is_up", lambda url: True)
    return serve_module.run_serve(argparse.Namespace(host="h", port=1), clock=world.clock, ids=world.ids,
                                  env=env, serve=lambda app, **kw: None)


def _ports(world: EngineWorld, migrate: Any) -> ServePorts:
    base = make_ports(world, TestIdentityIssuer(world.clock))
    return base.__class__(**{**base.__dict__, "migrate": migrate})


def test_serve_registers_the_schema_readiness_check_and_a_migration(tmp_path: object) -> None:
    ports = _resolve(*_real_args(tmp_path), AGENTCORE_REGISTRY_DSN="postgresql://u:s@127.0.0.1:1/none")
    assert "schema" in dict(ports.readiness) and ports.migrate is not None
    assert dict(ports.readiness)["schema"]() is False  # nothing listens on port 1: fails closed, no raise


def test_startup_runs_the_migration_in_the_background(monkeypatch: pytest.MonkeyPatch,
                                                      root_logging: None) -> None:
    world, calls = EngineWorld(), []
    seen: list[bool] = []

    def migrate() -> list[str]:
        calls.append(1)
        return ["main"]

    def serve(app: Any, **kw: Any) -> None:
        seen.append(True)

    monkeypatch.setattr(serve_module, "resolve_ports", lambda *a, **k: _ports(world, migrate))
    monkeypatch.setattr(serve_module, "gateway_is_up", lambda url: True)
    code = serve_module.run_serve(argparse.Namespace(host="h", port=1), clock=world.clock, ids=world.ids,
                                  env={}, serve=serve)
    assert code == 0 and seen
    for _ in range(100):  # the thread is joined on exit, but it may not have been scheduled yet
        if calls:
            break
        sv.threading.Event().wait(0.01)
    assert calls == [1]


def test_auto_migrate_can_be_turned_off(monkeypatch: pytest.MonkeyPatch, root_logging: None) -> None:
    world, calls = EngineWorld(), []
    assert _start(_ports(world, lambda: calls.append(1) or []), monkeypatch, world,
                  {AUTO_MIGRATE_ENV: "0"}) == 0
    assert calls == []


def test_a_port_set_without_a_migration_starts_normally(monkeypatch: pytest.MonkeyPatch,
                                                        root_logging: None) -> None:
    world = EngineWorld()
    assert _start(_ports(world, None), monkeypatch, world, {}) == 0
