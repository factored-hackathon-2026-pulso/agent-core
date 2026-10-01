from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from agent_core.domain import CredentialsInvalid, Principal
from agent_core.registry.http import registry_extension
from tests.registry.helpers import AGENT, bot, human, prompt_draft
from tests.registry.service_world import SUITE, World

PRINCIPALS: dict[str, Principal] = {"ana": human(), "bot": bot("constructor", "aprobador")}


def _client() -> tuple[TestClient, World]:
    w = World()
    app = FastAPI()

    def authenticate(request: Request, authorization: str | None) -> Principal:
        if authorization not in PRINCIPALS:
            raise CredentialsInvalid()
        return PRINCIPALS[authorization]

    registry_extension(w.service)(app, authenticate)
    return TestClient(app, raise_server_exceptions=False), w


def _h(who: str, key: str | None = None) -> dict[str, str]:
    return {"authorization": who, **({"idempotency-key": key} if key else {})}


def test_full_cycle_over_http() -> None:
    c, _ = _client()
    p = c.post("/v1/registry/proposals", json={"agent_id": AGENT, "origin": "manual", "title": "t"},
               headers=_h("ana"))
    assert p.status_code == 201
    pid = p.json()["proposal_id"]
    changes = [prompt_draft().model_dump(mode="json"), SUITE.model_dump(mode="json")]
    body = {"expected_rev": 0, "changes": changes}
    put = c.put(f"/v1/registry/proposals/{pid}/draft", json=body, headers=_h("ana"))
    assert put.status_code == 200
    frozen = c.post(f"/v1/registry/proposals/{pid}/freeze", headers=_h("ana"))
    h = frozen.json()["candidate_hash"]
    assert c.post(f"/v1/registry/proposals/{pid}/evaluate", json={"suite_id": "disputas-suite"},
                  headers=_h("ana")).json()["verdict"] == "pass"
    approve = c.post(f"/v1/registry/proposals/{pid}/approve", json={"candidate_hash": h}, headers=_h("ana"))
    assert approve.status_code == 200
    pub = c.post(f"/v1/registry/proposals/{pid}/publish", headers=_h("ana", "k1"))
    assert pub.status_code == 200
    rel = pub.json()["release_id"]
    assert c.get(f"/v1/registry/releases/rel-demo/diff/{rel}", headers=_h("ana")).status_code == 200


def test_bot_gets_forbidden_role_problem() -> None:  # T-REG-13 por HTTP
    c, _ = _client()
    r = c.post("/v1/registry/releases/rel-demo/revoke", json={"reason": "x"}, headers=_h("bot"))
    assert r.status_code == 403
    assert r.headers["content-type"].startswith("application/problem+json")
    assert r.json()["code"] == "forbidden_role"


def test_validation_failed_carries_violations() -> None:
    c, _ = _client()
    pid = c.post("/v1/registry/proposals", json={"agent_id": AGENT, "origin": "manual", "title": "t"},
                 headers=_h("ana")).json()["proposal_id"]
    body = {"expected_rev": 0, "changes": [prompt_draft(version="0.1.0").model_dump(mode="json")]}
    c.put(f"/v1/registry/proposals/{pid}/draft", json=body, headers=_h("ana"))
    r = c.post(f"/v1/registry/proposals/{pid}/freeze", headers=_h("ana"))
    assert r.status_code == 422 and r.json()["violations"][0]["rule"] == "REG-VERSION"


def test_publish_requires_idempotency_key_and_auth() -> None:
    c, _ = _client()
    assert c.post("/v1/registry/proposals/x/publish", headers=_h("ana")).status_code == 422
    assert c.get("/v1/registry/releases/rel-demo").status_code == 401


def test_invalid_proposal_fields_are_validation_failed_problem_not_500() -> None:  # revisión final I4
    c, _ = _client()
    for body in ({"agent_id": AGENT, "title": ""}, {"agent_id": AGENT, "title": "x" * 201},
                 {"agent_id": "Agente Malo", "title": "t"}):
        r = c.post("/v1/registry/proposals", json=body, headers=_h("ana"))
        assert r.status_code == 422, body
        assert r.json()["code"] == "validation_failed" and r.json()["violations"][0]["rule"] == "REG-PROPOSAL"


def test_get_entity_with_slash_in_id_and_version_query() -> None:  # T15-5
    c, _ = _client()
    r = c.get("/v1/registry/entities/template/t/acuse", headers=_h("ana"))
    assert r.status_code == 200, r.text
    assert r.json()["ref"] == {"kind": "template", "id": "t/acuse", "version": "1.0.0"}
    pinned = c.get("/v1/registry/entities/template/t/acuse?version=1.0.0", headers=_h("ana"))
    assert pinned.status_code == 200 and pinned.json()["content_hash"] == r.json()["content_hash"]
    assert c.get("/v1/registry/entities/template/t/acuse?version=9.9.9", headers=_h("ana")).status_code == 404
    assert c.get("/v1/registry/entities/template/t/nada", headers=_h("ana")).status_code == 404


def test_idempotency_key_longer_than_255_is_rejected() -> None:  # T15-3
    c, _ = _client()
    assert c.post("/v1/registry/proposals/x/publish", headers=_h("ana", "k" * 256)).status_code == 422
    assert c.post("/v1/registry/proposals/x/publish", headers=_h("ana", "k" * 255)).status_code == 404
