"""`build_api_deps`: el motor real sobre la API de M9 con un verificador JWS real (sin Postgres ni red)."""

from dataclasses import replace

from fastapi.testclient import TestClient

from agent_core.api.app import create_app
from agent_core.composition.serve import build_api_deps
from agent_core.composition.serve_ports import ServePorts
from testing.engine_world import EngineWorld
from testing.fakes.identity import TestIdentityIssuer
from testing.fakes.storage import InMemoryCostCounters


def make_ports(world: EngineWorld, issuer: TestIdentityIssuer, doubles: tuple[str, ...] = ()) -> ServePorts:
    d = world.deps
    return ServePorts(
        clock=d.clock, ids=d.ids, keys=d.keys, uow_factory=d.uow_factory, audit=d.audit,
        counters=InMemoryCostCounters(world.store), registry=d.registry, releases=d.releases,
        gateway=d.gateway, providers=d.providers, tools=d.tools, authz=d.authz, transcript=d.transcript,
        calibrations=d.calibrations, classifier=d.classifier, verifier=issuer.verifier(), doubles=doubles,
        directory=d.directory)


def _client(world: EngineWorld, issuer: TestIdentityIssuer) -> TestClient:
    return TestClient(create_app(build_api_deps(make_ports(world, issuer))), raise_server_exceptions=False)


def _bearer(issuer: TestIdentityIssuer) -> dict[str, str]:
    return {"Authorization": f"Bearer {issuer.customer()}"}


def test_a_run_and_a_turn_go_through_http_with_the_real_engine() -> None:
    world = EngineWorld()
    issuer = TestIdentityIssuer(world.clock)
    client = _client(world, issuer)
    created = client.post("/v1/runs", json={"agent": "atencion"},
                          headers={**_bearer(issuer), "Idempotency-Key": "k-1"})
    assert created.status_code == 201, created.text
    body = created.json()
    assert [m["text"] for m in body["first_turn"]["messages"]] == ["¿Qué cargo quieres disputar?"]

    world.understands("continue")
    world.matches()
    turn = client.post(f"/v1/sessions/{body['session_id']}/turns", headers=_bearer(issuer),
                       json={"text": "no reconozco un cargo de ciento veinte dólares", "channel": "web",
                             "client_turn_id": "c-1"})
    assert turn.status_code == 200, turn.text
    assert turn.json()["confirmation"] is not None  # el motor real pidió confirmar la acción


def test_the_transcript_is_served_by_the_wired_reader() -> None:
    world = EngineWorld()
    issuer = TestIdentityIssuer(world.clock)
    client = _client(world, issuer)
    run = client.post("/v1/runs", json={"agent": "atencion"},
                      headers={**_bearer(issuer), "Idempotency-Key": "k-2"}).json()
    got = client.get(f"/v1/runs/{run['run_id']}/transcript", headers=_bearer(issuer))
    assert got.status_code == 200, got.text  # SyntheticAuthz concede la lectura del dueño
    body = got.json()
    assert body["run_id"] == run["run_id"]
    assert "¿Qué cargo quieres disputar?" in [e["text"] for e in body["entries"]]


def test_a_bad_credential_is_rejected_before_the_engine() -> None:
    world = EngineWorld()
    issuer = TestIdentityIssuer(world.clock)
    client = _client(world, issuer)
    resp = client.post("/v1/runs", json={"agent": "atencion"},
                       headers={"Authorization": "Bearer no-es-un-jws", "Idempotency-Key": "k-3"})
    assert resp.status_code == 401


def test_build_api_deps_passes_the_directory_to_the_engine() -> None:
    from dataclasses import replace

    from agent_core.composition import DirectoryToolExecutor
    from agent_core.composition.serve import build_api_deps
    from testing.fakes.directory import InMemoryDirectory

    world = EngineWorld()
    issuer = TestIdentityIssuer(world.clock)
    ports = replace(make_ports(world, issuer), directory=InMemoryDirectory(world.registry))
    deps = build_api_deps(ports)
    assert isinstance(deps.turns._runtimes._tools, DirectoryToolExecutor)  # type: ignore[attr-defined]


def test_readyz_reports_the_readiness_checks_of_the_ports() -> None:
    world = EngineWorld()
    issuer = TestIdentityIssuer(world.clock)
    ports = replace(make_ports(world, issuer), readiness=(("postgres", lambda: False),))
    client = TestClient(create_app(build_api_deps(ports)), raise_server_exceptions=False)

    response = client.get("/readyz")

    assert response.status_code == 503 and response.json()["failed"] == ["postgres"]
    assert client.get("/healthz").status_code == 200
