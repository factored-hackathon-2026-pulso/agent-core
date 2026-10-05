"""Operación de `serve` (brief A3): topes de carga, plazo de apagado y variables obligatorias."""

import argparse
from typing import Any

import pytest
from fastapi.testclient import TestClient

from agent_core.api.app import create_app
from agent_core.composition import serve as serve_module
from agent_core.composition.serve import build_api_deps, ops_from_env
from agent_core.composition.serve_ports import ServeConfigError
from testing.engine_world import EngineWorld
from testing.fakes.identity import TestIdentityIssuer
from tests.composition.test_serve_app import make_ports
from tests.composition.test_serve_ports import _real_args, _resolve

pytest_plugins = ["tests.support.otel"]


def test_ops_defaults() -> None:
    ops = ops_from_env({})
    assert (ops.max_inflight, ops.worker_threads, ops.shutdown_grace_s) == (0, 40, 25.0)


def test_ops_read_the_environment() -> None:
    ops = ops_from_env({"AGENTCORE_MAX_INFLIGHT": "50", "AGENTCORE_WORKER_THREADS": "80",
                        "AGENTCORE_SHUTDOWN_GRACE_SECONDS": "10"})
    assert (ops.max_inflight, ops.worker_threads, ops.shutdown_grace_s) == (50, 80, 10.0)


@pytest.mark.parametrize("name,value", [("AGENTCORE_MAX_INFLIGHT", "-1"), ("AGENTCORE_MAX_INFLIGHT", "x"),
                                        ("AGENTCORE_WORKER_THREADS", "0"),
                                        ("AGENTCORE_SHUTDOWN_GRACE_SECONDS", "-2")])
def test_invalid_ops_values_are_a_named_error(name: str, value: str) -> None:
    with pytest.raises(ValueError, match=name):
        ops_from_env({name: value})


def test_serve_starts_uvicorn_with_the_shutdown_grace(monkeypatch: pytest.MonkeyPatch,
                                                      root_logging: None) -> None:
    world = EngineWorld()
    ports = make_ports(world, TestIdentityIssuer(world.clock))
    monkeypatch.setattr(serve_module, "resolve_ports", lambda *a, **k: ports)
    monkeypatch.setattr(serve_module, "gateway_is_up", lambda url: True)
    started: dict[str, Any] = {}
    code = serve_module.run_serve(argparse.Namespace(host="h", port=1), clock=world.clock, ids=world.ids,
                                  env={"AGENTCORE_SHUTDOWN_GRACE_SECONDS": "7"},
                                  serve=lambda app, **kw: started.update(kw))
    assert code == 0 and started["timeout_graceful_shutdown"] == 7


def test_an_invalid_ops_value_stops_the_startup_with_exit_2(capsys: pytest.CaptureFixture[str],
                                                            root_logging: None) -> None:
    world = EngineWorld()
    code = serve_module.run_serve(argparse.Namespace(host="h", port=1), clock=world.clock, ids=world.ids,
                                  env={"AGENTCORE_MAX_INFLIGHT": "-3"}, serve=lambda app, **kw: None)
    assert code == 2 and "AGENTCORE_MAX_INFLIGHT" in capsys.readouterr().err


def test_the_app_accepts_the_inflight_cap_and_still_answers() -> None:
    world = EngineWorld()
    ports = make_ports(world, TestIdentityIssuer(world.clock))
    client = TestClient(create_app(build_api_deps(ports, max_inflight=5)), raise_server_exceptions=False)
    assert client.get("/healthz").status_code == 200


def test_in_production_mode_a_missing_jev_key_stops_the_startup(tmp_path: object) -> None:
    with pytest.raises(ServeConfigError) as info:
        _resolve(*_real_args(tmp_path), AGENTCORE_JEV_API_KEY="")
    assert "AGENTCORE_JEV_API_KEY" in " ".join(info.value.problems)


def test_with_the_key_production_mode_resolves(tmp_path: object) -> None:
    assert _resolve(*_real_args(tmp_path), AGENTCORE_JEV_API_KEY="k").doubles == ()


def test_demo_mode_does_not_need_the_jev_key() -> None:
    assert _resolve(AGENTCORE_ALLOW_DOUBLES="1", AGENTCORE_JEV_API_KEY="").doubles


def test_the_worker_thread_limit_is_really_applied_at_startup() -> None:
    from anyio import to_thread
    from fastapi import FastAPI

    app = FastAPI()
    serve_module.install_worker_threads(app, 7)
    seen: list[float] = []

    @app.get("/limit")
    async def limit() -> dict[str, float]:
        seen.append(to_thread.current_default_thread_limiter().total_tokens)
        return {"n": seen[-1]}

    with TestClient(app) as client:  # entering the context runs the startup handlers
        assert client.get("/limit").json() == {"n": 7}


def test_without_an_explicit_cap_the_inflight_limit_follows_the_connection_pool() -> None:
    """A turn holds a connection and asks for another: more concurrent turns than half the pool deadlock on
    `PoolTimeout` (seen with 20 simultaneous runs on a pool of 10)."""
    assert ops_from_env({"AGENTCORE_DB_POOL_MAX": "10"}).max_inflight == 5
    assert ops_from_env({"AGENTCORE_DB_POOL_MAX": "1"}).max_inflight == 1
    assert ops_from_env({"AGENTCORE_DB_POOL_MAX": "10", "AGENTCORE_MAX_INFLIGHT": "7"}).max_inflight == 7
    assert ops_from_env({}).max_inflight == 0  # no pool: one connection per operation, nothing to exhaust
