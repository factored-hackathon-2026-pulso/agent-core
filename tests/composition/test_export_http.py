"""N-08 (§31.12): exportación paginada de runs, eventos de auditoría y eventos del registry."""

from dataclasses import replace

from fastapi.testclient import TestClient

from agent_core.api.app import create_app
from agent_core.composition.serve import build_api_deps
from agent_core.composition.serve_registry import RegistryApiPorts, build_registry_service_for_serve
from agent_core.registry.memory import InMemoryRegistryStore
from testing.engine_world import EngineWorld
from testing.fakes.identity import TestIdentityIssuer, TestStaffIssuer
from testing.fakes.storage import InMemoryAuditSink, InMemoryRunExport
from tests.composition.test_serve_app import make_ports
from tests.registry.helpers import AGENT

BODY = {"agent_id": AGENT, "origin": "manual", "title": "t"}


def _setup() -> tuple[TestClient, TestIdentityIssuer, TestStaffIssuer, EngineWorld]:
    world = EngineWorld()
    issuer = TestIdentityIssuer(world.clock)
    staff = TestStaffIssuer(world.clock)
    api = RegistryApiPorts(store=InMemoryRegistryStore(), staff_verifier=staff.verifier(),
                           eval_uow_factory=world.store.uow, eval_audit=InMemoryAuditSink(world.store))
    ports = replace(make_ports(world, issuer), run_export=InMemoryRunExport(world.store), registry_api=api)
    service = build_registry_service_for_serve(ports)
    app = create_app(build_api_deps(ports, registry_service=service))
    return TestClient(app, raise_server_exceptions=False), issuer, staff, world


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _start_runs(client: TestClient, issuer: TestIdentityIssuer, n: int) -> list[str]:
    ids = []
    for i in range(n):
        r = client.post("/v1/runs", json={"agent": "atencion"},
                        headers={**_bearer(issuer.customer(f"cust-00{i + 1}")), "Idempotency-Key": f"k-{i}"})
        assert r.status_code == 201, r.text
        ids.append(r.json()["run_id"])
    return ids


def test_runs_are_paged_by_run_seq_without_customer_data() -> None:
    client, issuer, staff, _ = _setup()
    run_ids = _start_runs(client, issuer, 3)
    head = _bearer(staff.exporter_bot())
    first = client.get("/v1/export/runs?limit=2", headers=head).json()
    assert [r["run_id"] for r in first["items"]] == run_ids[:2]
    assert first["next_after"] == first["items"][-1]["run_seq"]
    second = client.get(f"/v1/export/runs?after={first['next_after']}&limit=2", headers=head).json()
    assert [r["run_id"] for r in second["items"]] == run_ids[2:]
    done = client.get(f"/v1/export/runs?after={second['next_after']}", headers=head).json()
    assert done["items"] == [] and done["next_after"] == second["next_after"]
    row = first["items"][0]
    assert row["principal_type"] == "customer" and "slots" not in row and "principal" not in row


def test_events_are_paged_by_seq_and_continue_where_the_cursor_left_off() -> None:
    client, issuer, staff, world = _setup()
    (run_id,) = _start_runs(client, issuer, 1)
    head = _bearer(staff.exporter_bot())
    everything = client.get(f"/v1/export/runs/{run_id}/events?limit=500", headers=head).json()["items"]
    assert [e["seq"] for e in everything] == list(range(len(everything))) and len(everything) >= 2
    seen: list[int] = []
    after = -1
    while True:
        page = client.get(f"/v1/export/runs/{run_id}/events?after={after}&limit=1", headers=head).json()
        if not page["items"]:
            break
        seen += [e["seq"] for e in page["items"]]
        after = page["next_after"]
    assert seen == [e["seq"] for e in everything]
    assert world.store.events[run_id][0].hash == everything[0]["hash"]  # el hash de la cadena viaja


def test_registry_events_are_paged_in_order() -> None:
    client, _, staff, _ = _setup()
    for _ in range(2):
        ok = client.post("/v1/registry/proposals", json=BODY, headers=_bearer(staff.supervisor()))
        assert ok.status_code == 201, ok.text
    head = _bearer(staff.exporter_bot())
    one = client.get("/v1/export/registry-events?limit=1", headers=head).json()
    assert len(one["items"]) == 1 and one["next_after"] == 1
    rest = client.get(f"/v1/export/registry-events?after={one['next_after']}", headers=head).json()
    assert len(rest["items"]) == 1 and rest["next_after"] == 2
    assert one["items"][0]["type"] and one["items"][0]["actor"]


def test_only_the_staff_with_the_exporter_role_can_read() -> None:
    client, issuer, staff, _ = _setup()
    assert client.get("/v1/export/runs").status_code == 401
    assert client.get("/v1/export/runs", headers=_bearer(issuer.customer())).status_code == 401
    forbidden = client.get("/v1/export/runs", headers=_bearer(staff.constructor_bot()))
    assert forbidden.status_code == 403 and forbidden.json()["code"] == "forbidden_role"
    assert client.get("/v1/export/runs", headers=_bearer(staff.admin())).status_code == 200
    assert client.get("/v1/export/runs?limit=501", headers=_bearer(staff.exporter_bot())).status_code == 422


def test_without_the_registry_api_there_is_no_export() -> None:
    world = EngineWorld()
    issuer = TestIdentityIssuer(world.clock)
    client = TestClient(create_app(build_api_deps(make_ports(world, issuer))), raise_server_exceptions=False)
    assert client.get("/v1/export/runs", headers=_bearer(issuer.customer())).status_code == 404
