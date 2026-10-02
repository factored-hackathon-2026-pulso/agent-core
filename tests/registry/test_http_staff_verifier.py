"""`registry_extension(service, verifier=...)`: la API del registry solo verifica las claves del staff."""

from dataclasses import replace
from datetime import timedelta

from fastapi.testclient import TestClient

from agent_core.api.app import create_app
from agent_core.registry.http import registry_extension
from testing.fakes.identity import TestIdentityIssuer, TestStaffIssuer
from tests.m09.conftest import api_deps
from tests.registry.helpers import AGENT
from tests.registry.service_world import World

BODY = {"agent_id": AGENT, "origin": "manual", "title": "t"}


def _client() -> tuple[TestClient, TestStaffIssuer, TestIdentityIssuer]:
    return _client_with_world()[0:3]


def _client_with_world() -> tuple[TestClient, TestStaffIssuer, TestIdentityIssuer, World]:
    world = World()
    deps, _ = api_deps()
    staff, customers = TestStaffIssuer(world.clock), TestIdentityIssuer(world.clock)
    app = create_app(replace(deps, verifier=customers.verifier(),
                             extensions=(registry_extension(world.service, staff.verifier(), world.clock),)))
    return TestClient(app, raise_server_exceptions=False), staff, customers, world


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_a_staff_credential_operates_the_registry() -> None:
    client, staff, _ = _client()
    r = client.post("/v1/registry/proposals", json=BODY, headers=_bearer(staff.supervisor()))
    assert r.status_code == 201, r.text


def test_a_customer_credential_does_not_even_verify() -> None:
    client, _, customers = _client()
    r = client.post("/v1/registry/proposals", json=BODY, headers=_bearer(customers.customer()))
    assert r.status_code == 401 and r.json()["code"] == "credentials_invalid"


def test_a_builder_principal_signed_with_the_customer_key_is_rejected() -> None:
    client, _, customers = _client()
    forged = TestStaffIssuer(customers._clock)  # type: ignore[attr-defined]
    forged.principal_key = customers.principal_key
    forged.principal_kid = customers.principal_kid
    r = client.post("/v1/registry/proposals", json=BODY, headers=_bearer(forged.supervisor()))
    assert r.status_code == 401


def test_missing_credential_is_401_and_the_customer_api_still_uses_its_own_verifier() -> None:
    client, staff, _ = _client()
    assert client.post("/v1/registry/proposals", json=BODY).status_code == 401
    # una credencial de staff no sirve en la API de clientes (su verificador no la conoce)
    r = client.post("/v1/runs", json={"agent": "atencion"},
                    headers={**_bearer(staff.supervisor()), "Idempotency-Key": "k"})
    assert r.status_code == 401


def test_an_expired_staff_credential_is_rejected_on_every_route() -> None:
    client, staff, _, world = _client_with_world()
    token = staff.constructor_bot()
    world.clock.advance(timedelta(hours=1, seconds=1))
    for r in (client.post("/v1/registry/proposals", json=BODY, headers=_bearer(token)),
              client.get("/v1/registry/proposals/p-1", headers=_bearer(token))):
        assert r.status_code == 401 and r.json()["code"] == "principal_expired", r.text


def test_a_staff_credential_expiring_exactly_now_is_rejected() -> None:
    client, staff, _, world = _client_with_world()
    token = staff.supervisor()
    world.clock.advance(timedelta(hours=1))  # exp <= now, igual que la puerta de M9
    r = client.post("/v1/registry/proposals", json=BODY, headers=_bearer(token))
    assert r.status_code == 401 and r.json()["code"] == "principal_expired"


def test_a_staff_verifier_without_a_clock_fails_closed() -> None:
    world = World()
    staff = TestStaffIssuer(world.clock)
    try:
        registry_extension(world.service, staff.verifier())
    except ValueError:
        return
    raise AssertionError("un verificador sin reloj no puede comprobar la vigencia")
