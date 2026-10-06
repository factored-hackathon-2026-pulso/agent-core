"""Startup warnings of `agentcore serve`: agents without `prod` release and the LLM gateway (non-blocking)."""

import argparse
from typing import Any

import pytest

from agent_core.composition import serve as serve_module
from agent_core.composition.serve import release_warnings
from agent_core.composition.serve_ports import add_serve_parser
from agent_core.domain import EntityKind, EntityRef, Release
from testing.engine_world import EngineWorld
from testing.fakes.clock import FakeClock
from testing.fakes.identity import TestIdentityIssuer
from testing.fakes.registry import InMemoryRegistry
from tests.composition.test_serve_app import make_ports

pytest_plugins = ["tests.support.otel"]


def _registry(*agents: str) -> InMemoryRegistry:
    registry = InMemoryRegistry()
    for agent in agents:
        release = Release(
            id=f"rel-{agent}", status="active", language_detection=EntityRef(id="nolang", version="1.0.0"),
            entities={EntityKind.model_profile: {}})
        registry.add_release(release, agent, alias="prod")
    return registry


def test_an_agent_with_a_prod_release_gives_no_warning() -> None:
    assert release_warnings(_registry("atencion"), ("atencion",), FakeClock()) == []


def test_an_agent_without_prod_release_is_a_warning() -> None:
    (w,) = release_warnings(_registry("atencion"), ("fantasma",), FakeClock())
    assert "fantasma" in w


def test_each_missing_agent_is_reported_once() -> None:
    assert len(release_warnings(_registry(), ("a", "b"), FakeClock())) == 2


def test_no_agents_means_no_check() -> None:
    assert release_warnings(_registry(), (), FakeClock()) == []


def test_serve_accepts_agents_and_prints_warnings_at_startup(
        capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, root_logging: None) -> None:
    parser = argparse.ArgumentParser()
    add_serve_parser(parser.add_subparsers(dest="command", required=True))
    args = parser.parse_args(["serve", "--agents", "atencion,otro"])
    assert args.agents == "atencion,otro"

    world = EngineWorld()
    base = make_ports(world, TestIdentityIssuer(world.clock))
    ports = base.__class__(**{**base.__dict__, "registry": _registry(), "agents": ("sinrelease",)})
    monkeypatch.setattr(serve_module, "resolve_ports", lambda *a, **k: ports)
    started: dict[str, Any] = {}
    code = serve_module.run_serve(argparse.Namespace(host="h", port=1), clock=world.clock, ids=world.ids,
                                  env={}, serve=lambda app, **kw: started.update(ok=True))
    err = capsys.readouterr().err
    assert code == 0 and started  # a warning never blocks startup
    assert "AVISO" in err and "sinrelease" in err


def _run_with(ports: Any, monkeypatch: pytest.MonkeyPatch, world: EngineWorld,
              probe: Any = None) -> int:
    monkeypatch.setattr(serve_module, "resolve_ports", lambda *a, **k: ports)
    if probe is not None:
        monkeypatch.setattr(serve_module, "gateway_is_up", probe)
    return serve_module.run_serve(argparse.Namespace(host="h", port=1), clock=world.clock, ids=world.ids,
                                  env={}, serve=lambda app, **kw: None)


def test_a_missing_llm_gateway_is_announced_at_startup(
        capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, root_logging: None) -> None:
    world = EngineWorld()
    base = make_ports(world, TestIdentityIssuer(world.clock))
    ports = base.__class__(**{**base.__dict__, "llm_gateway_url": None})
    assert _run_with(ports, monkeypatch, world) == 0
    err = capsys.readouterr().err
    assert "AVISO" in err and "AGENTCORE_LLM_GATEWAY_URL" in err


def test_an_unreachable_llm_gateway_is_a_warning_that_never_prints_the_url_credentials(
        capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, root_logging: None) -> None:
    world = EngineWorld()
    base = make_ports(world, TestIdentityIssuer(world.clock))
    ports = base.__class__(**{**base.__dict__, "llm_gateway_url": "https://user:S3CRET@gw.test"})
    assert _run_with(ports, monkeypatch, world, probe=lambda url: False) == 0
    err = capsys.readouterr().err
    assert "AVISO" in err and "no responde" in err and "S3CRET" not in err


def test_a_reachable_llm_gateway_gives_no_gateway_warning(
        capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, root_logging: None) -> None:
    world = EngineWorld()
    base = make_ports(world, TestIdentityIssuer(world.clock))
    ports = base.__class__(**{**base.__dict__, "llm_gateway_url": "https://gw.test"})
    assert _run_with(ports, monkeypatch, world, probe=lambda url: True) == 0
    assert "gateway" not in capsys.readouterr().err.lower()


class _DownRegistry(InMemoryRegistry):
    def resolve_release(self, *a: Any, **k: Any) -> Any:
        raise TimeoutError("couldn't get a connection after 30.00 sec")


def test_serve_starts_even_if_the_release_check_cannot_reach_the_database(
        capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, root_logging: None) -> None:
    """With Postgres down at boot the process must still answer `/healthz` (and `/readyz` 503): the startup
    check of the `prod` releases is a warning, never a reason not to start."""
    world = EngineWorld()
    base = make_ports(world, TestIdentityIssuer(world.clock))
    ports = base.__class__(**{**base.__dict__, "registry": _DownRegistry(), "agents": ("atencion",)})
    assert _run_with(ports, monkeypatch, world) == 0
    err = capsys.readouterr().err
    assert "AVISO" in err and "atencion" in err and "TimeoutError" in err


def test_a_database_that_is_down_is_not_even_queried_for_the_release_check(
        capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, root_logging: None) -> None:
    world = EngineWorld()
    base = make_ports(world, TestIdentityIssuer(world.clock))
    asked: list[str] = []

    class Spy(_DownRegistry):
        def resolve_release(self, *a: Any, **k: Any) -> Any:
            asked.append("asked")
            return super().resolve_release(*a, **k)

    ports = base.__class__(**{**base.__dict__, "registry": Spy(), "agents": ("atencion",),
                              "readiness": (("postgres", lambda: False),)})
    assert _run_with(ports, monkeypatch, world) == 0
    assert asked == [] and "no responde" in capsys.readouterr().err


def test_pool_warnings_flag_an_inflight_cap_the_pool_cannot_serve() -> None:
    from agent_core.composition.serve import pool_warnings

    assert pool_warnings(0, 0) == []  # sin pool: una conexión por operación
    assert pool_warnings(40, 20) == []  # un turno retiene 2 conexiones: 2 x 20 <= 40
    (w,) = pool_warnings(6, 40)
    assert "AGENTCORE_DB_POOL_MAX" in w and "PoolTimeout" in w
    (w2,) = pool_warnings(10, 0)  # con pool y sin tope de peticiones simultáneas
    assert "AGENTCORE_MAX_INFLIGHT" in w2
