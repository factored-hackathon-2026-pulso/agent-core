"""`/healthz` (liveness) y `/readyz` (readiness): rutas de operación, sin credencial y fuera de `/v1`."""

from collections.abc import Callable
from dataclasses import replace

from fastapi.testclient import TestClient
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from agent_core.api.app import ApiDeps, create_app
from tests.m09.conftest import api_deps

pytest_plugins = ["tests.support.otel"]


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

    assert response.status_code == 200 and response.json()["status"] == "ready"


def test_readyz_without_checks_is_ready() -> None:
    assert _client().get("/readyz").status_code == 200


def test_readyz_names_the_failed_checks_with_503() -> None:
    client = _client(("postgres", lambda: False), ("registry", lambda: True))

    response = client.get("/readyz")

    assert response.status_code == 503
    assert response.json()["status"] == "unavailable" and response.json()["failed"] == ["postgres"]


def test_a_check_that_raises_counts_as_failed_and_never_leaks_its_message() -> None:
    client = _client(("postgres", _boom))

    response = client.get("/readyz")

    assert response.status_code == 503
    assert response.json()["status"] == "unavailable" and response.json()["failed"] == ["postgres"]
    assert "hunter2" not in response.text and "db.internal" not in response.text


def test_probe_routes_are_not_part_of_the_v1_contract() -> None:
    paths = _client().get("/openapi.json").json()["paths"]

    assert "/healthz" not in paths and "/readyz" not in paths


def test_api_deps_readiness_defaults_to_no_checks() -> None:
    deps, _ = api_deps()

    assert isinstance(deps, ApiDeps) and deps.readiness == ()


def test_probes_open_no_request_span_even_when_not_ready(otel: InMemorySpanExporter) -> None:
    client = _client(("postgres", lambda: False))

    assert client.get("/healthz").status_code == 200
    assert client.get("/readyz").status_code == 503

    assert [s for s in otel.get_finished_spans() if s.name == "agentcore.api.request"] == []


# --- A3: estado por dependencia, dependencias opcionales y plazo -----------------------------------------


def _client_with(*checks: tuple[str, Callable[[], bool]], optional: frozenset[str] = frozenset(),
                 timeout_s: float = 1.0) -> TestClient:
    deps, _ = api_deps()
    app = create_app(replace(deps, readiness=tuple(checks), optional_checks=optional,
                             readiness_timeout_s=timeout_s))
    return TestClient(app, raise_server_exceptions=False)


def test_readyz_reports_the_status_of_every_dependency() -> None:
    body = _client_with(("postgres", lambda: True), ("keys", lambda: True)).get("/readyz").json()

    assert body == {"status": "ready", "checks": {"postgres": "ok", "keys": "ok"}}


def test_an_optional_dependency_that_fails_is_reported_but_does_not_block() -> None:
    client = _client_with(("postgres", lambda: True), ("tool_service", lambda: False),
                          optional=frozenset({"tool_service"}))

    response = client.get("/readyz")

    assert response.status_code == 200
    assert response.json() == {"status": "ready", "checks": {"postgres": "ok", "tool_service": "fail"},
                               "degraded": ["tool_service"]}


def test_a_required_failure_is_503_and_the_optional_one_is_still_listed() -> None:
    client = _client_with(("postgres", lambda: False), ("tool_service", lambda: False),
                          optional=frozenset({"tool_service"}))

    response = client.get("/readyz")

    assert response.status_code == 503
    assert response.json() == {"status": "unavailable", "failed": ["postgres"],
                               "checks": {"postgres": "fail", "tool_service": "fail"},
                               "degraded": ["tool_service"]}


def test_a_check_that_does_not_finish_in_time_fails_and_the_probe_still_answers_fast() -> None:
    import threading

    release = threading.Event()

    def hangs() -> bool:
        release.wait(5)
        return True

    client = _client_with(("postgres", hangs), ("keys", lambda: True), timeout_s=0.1)
    response = client.get("/readyz")  # answers while `hangs` is still blocked: it did not wait for it
    still_blocked = not release.is_set()
    release.set()

    assert still_blocked
    assert response.status_code == 503 and response.json()["checks"] == {"postgres": "fail", "keys": "ok"}


def test_checks_run_concurrently() -> None:
    import threading

    both = threading.Barrier(2, timeout=2)

    def meets() -> bool:
        both.wait()
        return True

    response = _client_with(("a", meets), ("b", meets), timeout_s=3.0).get("/readyz")

    assert response.status_code == 200
