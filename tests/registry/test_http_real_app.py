"""La extensión del registry montada en el `create_app` REAL de M9 (revisión final I6b)."""

from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from agent_core.api.app import create_app
from agent_core.registry.http import registry_extension
from testing.builders import principal
from tests.m09.conftest import api_deps
from tests.m09.helpers import StubVerifier
from tests.registry.helpers import AGENT, bot, human
from tests.registry.service_world import World

ANON = principal(type="customer", id=None, attrs={"anon_session": "sesion-anonima-1"},
                 auth={"level": "anonymous", "at": principal().auth.at})


def _client() -> tuple[TestClient, dict[str, str]]:
    world = World()
    deps, _ = api_deps()
    verifier = StubVerifier()
    tokens = {"ana": verifier.register("tok-ana", human()),
              "bot": verifier.register("tok-bot", bot("constructor", "aprobador")),
              "anon": verifier.register("tok-anon", ANON)}
    app = create_app(replace(deps, verifier=verifier, extensions=(registry_extension(world.service),)))
    return TestClient(app, raise_server_exceptions=False), tokens


def _auth(token: str | None) -> dict[str, str]:
    return {} if token is None else {"authorization": token}


WRITES = [
    ("post", "/v1/registry/proposals", {"agent_id": AGENT, "origin": "manual", "title": "t"}),
    ("post", "/v1/registry/releases/rel-demo/revoke", {"reason": "x"}),
    ("post", f"/v1/registry/aliases/{AGENT}/prod", {"release_id": "rel-demo"}),
    ("post", "/v1/registry/proposals/x/approve", {"candidate_hash": "h"}),
    ("post", "/v1/registry/proposals/x/freeze", None),
]


@pytest.mark.parametrize(("method", "path", "body"), WRITES)
def test_missing_or_unknown_credential_is_401_from_m9(
        method: str, path: str, body: dict[str, str] | None) -> None:
    client, _ = _client()
    for headers in ({}, {"authorization": "no-registrado"}, {"authorization": "  "}):
        r = getattr(client, method)(path, json=body, headers=headers)
        assert r.status_code == 401, (headers, r.text)
        assert r.json()["code"] == "credentials_invalid"


def test_reads_without_credential_are_401() -> None:
    client, _ = _client()
    assert client.get("/v1/registry/releases/rel-demo").status_code == 401
    assert client.get("/v1/registry/entities/template/t/acuse").status_code == 401


def test_bot_with_approver_role_gets_403_on_approver_writes_but_can_build() -> None:
    client, tokens = _client()
    headers = _auth(tokens["bot"])
    made = client.post("/v1/registry/proposals", json=WRITES[0][2], headers=headers)
    assert made.status_code == 201  # rol constructor: permitido
    for method, path, body in WRITES[1:4]:
        r = getattr(client, method)(path, json=body, headers=headers)
        assert r.status_code == 403, (path, r.text)
        assert r.json()["code"] == "forbidden_role"
    pub = client.post("/v1/registry/proposals/x/publish", headers={**headers, "idempotency-key": "k"})
    assert pub.status_code == 403


@pytest.mark.parametrize(("method", "path", "body"), WRITES)
def test_anonymous_principal_without_id_gets_403_on_every_write(
        method: str, path: str, body: dict[str, str] | None) -> None:
    client, tokens = _client()
    r = getattr(client, method)(path, json=body, headers=_auth(tokens["anon"]))
    assert r.status_code == 403, r.text
    assert r.json()["code"] == "forbidden_role"
    headers = {**_auth(tokens["anon"]), "idempotency-key": "k"}
    pub = client.post("/v1/registry/proposals/x/publish", headers=headers)
    assert pub.status_code == 403


def test_human_with_roles_passes_authentication_and_authorization() -> None:
    client, tokens = _client()
    headers = _auth(tokens["ana"])
    assert client.post("/v1/registry/proposals", json=WRITES[0][2], headers=headers).status_code == 201
    assert client.get("/v1/registry/releases/rel-demo", headers=headers).status_code == 200
    # autorizada pero la release no existe: 404 del registry, no 403
    assert client.post("/v1/registry/releases/rel-x/revoke", json={"reason": "x"}, headers=headers
                       ).status_code == 404

