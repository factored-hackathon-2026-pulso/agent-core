"""El `builder` del motor de mejora en modo `serve` (brief A5): escribe, congela, evalúa y lee; no decide.

La credencial es la del bot constructor del staff: `type=builder`, solo el rol `constructor`, sin `actor`.
Aprobar, rechazar, publicar, promover y revocar las deniega el servidor, aunque el token las reclame."""

import pytest
from fastapi.testclient import TestClient

from agent_core.api.app import create_app
from agent_core.composition.serve import build_api_deps
from agent_core.composition.serve_registry import build_registry_service_for_serve
from testing.engine_world import EngineWorld
from testing.fakes.identity import TestIdentityIssuer, TestStaffIssuer
from tests.composition.test_serve_registry import _ports
from tests.registry.helpers import AGENT

BODY = {"agent_id": AGENT, "origin": "manual", "title": "mejora del motor"}


@pytest.fixture
def served() -> tuple[TestClient, dict[str, str]]:
    world = EngineWorld()
    staff = TestStaffIssuer(world.clock)
    ports = _ports(world, TestIdentityIssuer(world.clock), staff)
    app = create_app(build_api_deps(ports, registry_service=build_registry_service_for_serve(ports)))
    heads = {name: {"Authorization": f"Bearer {token}"} for name, token in {
        "engine": staff.constructor_bot("engine-builder"), "supervisor": staff.supervisor()}.items()}
    return TestClient(app, raise_server_exceptions=False), heads  # type: ignore[return-value]


def _proposal(client: TestClient, heads: dict[str, str]) -> str:
    created = client.post("/v1/registry/proposals", json=BODY, headers=heads["engine"])  # type: ignore[arg-type]
    assert created.status_code == 201, created.text
    return str(created.json()["proposal_id"])


def test_the_engine_can_write_freeze_evaluate_and_read(served: tuple[TestClient, dict[str, str]]) -> None:
    client, heads = served
    h = heads["engine"]  # type: ignore[assignment]
    pid = _proposal(client, heads)
    calls = [
        ("put", f"/v1/registry/proposals/{pid}/draft", {"changes": [], "expected_rev": 0}),
        ("post", f"/v1/registry/proposals/{pid}/validate", None),
        ("post", f"/v1/registry/proposals/{pid}/freeze", None),
        ("post", f"/v1/registry/proposals/{pid}/reopen", None),
        ("post", f"/v1/registry/proposals/{pid}/evaluate", {"suite_id": "s"}),
        ("get", f"/v1/registry/proposals/{pid}", None),
        ("get", f"/v1/registry/aliases/{AGENT}/prod", None),
    ]
    for method, path, body in calls:
        r = getattr(client, method)(path, headers=h, **({} if body is None else {"json": body}))
        assert r.status_code != 403, (method, path, r.text)  # otras causas (409, 422...) no son de rol


@pytest.mark.parametrize(("method", "path", "body"), [
    ("post", "/v1/registry/proposals/{pid}/approve", {"candidate_hash": "h"}),
    ("post", "/v1/registry/proposals/{pid}/reject", {"reason": "no"}),
    ("post", "/v1/registry/proposals/{pid}/publish", None),
    ("post", "/v1/registry/aliases/" + AGENT + "/prod", {"release_id": "rel-demo"}),
    ("post", "/v1/registry/releases/rel-demo/revoke", {"reason": "x"}),
])
def test_the_engine_can_never_approve_publish_promote_or_revoke(
        served: tuple[TestClient, dict[str, str]], method: str, path: str,
        body: dict[str, str] | None) -> None:
    client, heads = served
    pid = _proposal(client, heads)
    extra = {"Idempotency-Key": "k-1"} if path.endswith("/publish") else {}
    r = getattr(client, method)(path.format(pid=pid), headers={**heads["engine"], **extra},  # type: ignore[dict-item]
                                **({} if body is None else {"json": body}))
    assert r.status_code == 403, r.text
    assert r.json()["code"] == "forbidden_role" or "forbidden" in r.text


def test_the_supervisor_is_not_blocked_by_the_same_gate(served: tuple[TestClient, dict[str, str]]) -> None:
    client, heads = served
    pid = _proposal(client, heads)
    r = client.post(f"/v1/registry/proposals/{pid}/approve", json={"candidate_hash": "h"},
                    headers=heads["supervisor"])  # type: ignore[arg-type]
    assert r.status_code != 403  # 409 (nothing frozen to approve): the role passed
