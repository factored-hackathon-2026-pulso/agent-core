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
