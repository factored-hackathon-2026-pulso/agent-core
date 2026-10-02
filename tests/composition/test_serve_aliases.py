"""Aviso al arrancar de los perfiles de `prod` cuyo alias de LLM no está configurado (gateway §5)."""

import argparse
from datetime import date
from decimal import Decimal
from typing import Any

import pytest

from agent_core.adapters.llm import EndpointConfig
from agent_core.composition import serve as serve_module
from agent_core.composition.serve import model_alias_warnings
from agent_core.composition.serve_ports import add_serve_parser
from agent_core.domain import EntityKind, EntityRef, ModelPrice, ModelProfile, Release
from testing.engine_world import EngineWorld
from testing.fakes.clock import FakeClock
from testing.fakes.identity import TestIdentityIssuer
from testing.fakes.registry import InMemoryRegistry
from tests.composition.test_serve_app import make_ports

pytest_plugins = ["tests.support.otel"]


def _profile(pid: str, alias: str) -> ModelProfile:
    return ModelProfile(
        id=pid, version="1.0.0", endpoint_alias=alias, model="vendor/m", temperature=Decimal("0.2"),
        max_tokens=100, price=ModelPrice(input_per_mtok=Decimal("1"), output_per_mtok=Decimal("2"),
                                         source="prueba", as_of=date(2026, 9, 30)))


def _registry(*profiles: ModelProfile) -> InMemoryRegistry:
    registry = InMemoryRegistry()
    registry.add(*profiles)
    release = Release(
        id="rel-1", status="active", language_detection=EntityRef(id="nolang", version="1.0.0"),
        entities={EntityKind.model_profile: {p.id: p.version for p in profiles}})
    registry.add_release(release, "atencion", alias="prod")
    return registry


ENDPOINTS = {"openrouter": EndpointConfig("openrouter", "https://x/v1", "OR_KEY")}


def _warn(registry: InMemoryRegistry, agents: tuple[str, ...], env: dict[str, str]) -> list[str]:
    return model_alias_warnings(registry, agents, ENDPOINTS, env, FakeClock())


def test_a_configured_alias_with_a_key_gives_no_warning() -> None:
    assert _warn(_registry(_profile("p1", "openrouter")), ("atencion",), {"OR_KEY": "k"}) == []


def test_an_alias_without_endpoint_is_named_with_its_profile() -> None:
    (w,) = _warn(_registry(_profile("p1", "otro")), ("atencion",), {"OR_KEY": "k"})
    assert "otro" in w and "p1@1.0.0" in w and "atencion" in w


def test_an_empty_key_variable_is_a_warning_that_names_the_alias_not_the_variable() -> None:
    (w,) = _warn(_registry(_profile("p1", "openrouter")), ("atencion",), {"OR_KEY": "  "})
    assert "openrouter" in w and "OR_KEY" not in w  # the env variable name is not logged (4f7743d)


def test_an_agent_without_prod_release_is_a_warning() -> None:
    (w,) = _warn(_registry(_profile("p1", "openrouter")), ("fantasma",), {"OR_KEY": "k"})
    assert "fantasma" in w


def test_each_missing_alias_is_reported_once_per_profile() -> None:
    ws = _warn(_registry(_profile("p1", "a"), _profile("p2", "b")), ("atencion",), {})
    assert len(ws) == 2


def test_no_agents_means_no_check() -> None:
    assert _warn(_registry(_profile("p1", "otro")), (), {}) == []


def test_serve_accepts_agents_and_prints_warnings_at_startup(
        capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, root_logging: None) -> None:
    parser = argparse.ArgumentParser()
    add_serve_parser(parser.add_subparsers(dest="command", required=True))
    args = parser.parse_args(["serve", "--agents", "atencion,otro"])
    assert args.agents == "atencion,otro"

    world = EngineWorld()
    base = make_ports(world, TestIdentityIssuer(world.clock))
    ports = base.__class__(**{**base.__dict__, "registry": _registry(_profile("p1", "sinendpoint")),
                              "agents": ("atencion",)})
    monkeypatch.setattr(serve_module, "resolve_ports", lambda *a, **k: ports)
    started: dict[str, Any] = {}
    code = serve_module.run_serve(argparse.Namespace(host="h", port=1), clock=world.clock, ids=world.ids,
                                  env={}, serve=lambda app, **kw: started.update(ok=True))
    err = capsys.readouterr().err
    assert code == 0 and started  # el aviso no bloquea el arranque
    assert "AVISO" in err and "sinendpoint" in err
