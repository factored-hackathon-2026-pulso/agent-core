"""`/healthz` (liveness) y `/readyz` (readiness): rutas de operación, sin credencial y fuera de `/v1`."""

from collections.abc import Callable
from dataclasses import replace

from fastapi.testclient import TestClient

from agent_core.api.app import ApiDeps, create_app
from tests.m09.conftest import api_deps


def _client(*checks: tuple[str, Callable[[], bool]]) -> TestClient:
    deps, _ = api_deps()
    app = create_app(replace(deps, readiness=tuple(checks)))
    return TestClient(app, raise_server_exceptions=False)


def _boom() -> bool:
    raise RuntimeError("password=hunter2 host=db.internal")


def test_healthz_answers_without_credentials_even_when_a_dependency_is_down() -> None:
    client = _client(("postgres", lambda: False))

    response = client.get("/healthz")

    assert response.status_code == 200 and response.json() == {"status": "ok"}


def test_readyz_is_ready_when_every_check_passes() -> None:
    client = _client(("postgres", lambda: True), ("registry", lambda: True))

    response = client.get("/readyz")

    assert response.status_code == 200 and response.json() == {"status": "ready"}


def test_readyz_without_checks_is_ready() -> None:
    assert _client().get("/readyz").status_code == 200


def test_readyz_names_the_failed_checks_with_503() -> None:
    client = _client(("postgres", lambda: False), ("registry", lambda: True))

    response = client.get("/readyz")

    assert response.status_code == 503
    assert response.json() == {"status": "unavailable", "failed": ["postgres"]}


def test_a_check_that_raises_counts_as_failed_and_never_leaks_its_message() -> None:
    client = _client(("postgres", _boom))

    response = client.get("/readyz")

    assert response.status_code == 503
    assert response.json() == {"status": "unavailable", "failed": ["postgres"]}
    assert "hunter2" not in response.text and "db.internal" not in response.text


def test_probe_routes_are_not_part_of_the_v1_contract() -> None:
    paths = _client().get("/openapi.json").json()["paths"]

    assert "/healthz" not in paths and "/readyz" not in paths


def test_api_deps_readiness_defaults_to_no_checks() -> None:
    deps, _ = api_deps()

    assert isinstance(deps, ApiDeps) and deps.readiness == ()
