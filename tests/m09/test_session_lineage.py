"""M9: `GET /v1/sessions/{session_id}/lineage` (T-TR-10, ADR 0021). Runs are seeded into the store."""

import json
from typing import Any

from testing.builders import NOW, principal, run_state
from tests.m09.test_api import World, problem

HASH = "ab" * 32


def seeded() -> World:
    w = World()
    w.add_agent("recepcion")
    w.add_agent("disputas")
    w.seed_run(
        run_id="run-0001",
        agent="recepcion@1.0.0",
        release="rel-recepcion",
        status="closed",
        outcome="transferred",
        closed_at=NOW,
        inactive_after=None,
    )
    with w.store.uow() as uow:
        uow.save_run(
            run_state(
                run_id="run-0002",
                agent="disputas@1.0.0",
                release="rel-disputas",
                origin={
                    "kind": "transfer",
                    "transfer_id": "transfer-0001",
                    "from_run_id": "run-0001",
                    "from_agent": "recepcion@1.0.0",
                    "from_release_id": "rel-recepcion",
                    "from_event_hash": HASH,
                    "depth": 1,
                },
            ),
            0,
        )
        uow.commit()
    return w


def test_owner_reads_the_session_lineage() -> None:  # T-TR-10
    w = seeded()
    resp = w.call("GET", "/v1/sessions/session-0001/lineage")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["session_id"] == "session-0001" and body["trace_id"]
    assert [r["agent"] for r in body["runs"]] == ["recepcion@1.0.0", "disputas@1.0.0"]
    assert [r["release"] for r in body["runs"]] == ["rel-recepcion", "rel-disputas"]
    assert body["runs"][0]["origin"] is None
    assert [r["status"] for r in body["runs"]] == ["closed", "open"]
    assert [r["outcome"] for r in body["runs"]] == ["transferred", None]
    assert body["runs"][1]["origin"] == {
        "transfer_id": "transfer-0001",
        "from_run_id": "run-0001",
        "from_agent": "recepcion@1.0.0",
        "from_release_id": "rel-recepcion",
    }
    assert set(body["runs"][0]) == {"run_id", "agent", "release", "status", "outcome", "origin"}


def test_the_lineage_never_publishes_the_integrity_hash() -> None:
    body = seeded().call("GET", "/v1/sessions/session-0001/lineage").text
    assert "from_event_hash" not in body and HASH not in body
    assert "principal" not in json.loads(body)["runs"][0]


def test_another_customer_cannot_read_it_and_it_is_recorded() -> None:
    w = seeded()
    w.verifier.register("tok-other", principal(id="cust-002"))
    resp = w.call("GET", "/v1/sessions/session-0001/lineage", "tok-other")
    body = problem(resp, 403, "principal_mismatch")
    assert body["code"] == "principal_mismatch"
    assert w.denials.calls


def test_unknown_session_is_404_after_a_valid_signature() -> None:
    w = World()
    problem(w.call("GET", "/v1/sessions/session-9999/lineage", "firma-falsa"), 401, "credentials_invalid")
    problem(w.call("GET", "/v1/sessions/session-9999/lineage"), 404, "not_found")


def test_an_advisor_is_not_the_session_owner_and_gets_principal_mismatch() -> None:
    # Same gate as post_turn: a session belongs to the principal that opened it. Advisors read per run.
    w = seeded()
    resp = w.call("GET", "/v1/sessions/session-0001/lineage", "tok-a", **{"X-On-Behalf-Of": "tok-d"})
    problem(resp, 403, "principal_mismatch")


def test_it_appears_in_the_openapi() -> None:
    w: Any = World()
    assert "/v1/sessions/{session_id}/lineage" in w.client.get("/openapi.json").json()["paths"]


def test_a_stale_run_of_another_principal_is_denied_by_the_per_run_read_check() -> None:
    w = World()
    other = principal(id="cust-002")
    w.seed_run(
        run_id="run-0001",
        principal=other,
        subject={"kind": "customer", "ref": "cust-002"},
        status="closed",
        outcome="transferred",
        closed_at=NOW,
        inactive_after=None,
    )
    with w.store.uow() as uow:
        uow.save_run(run_state(run_id="run-0002"), 0)  # open run, owned by the default customer
        uow.commit()
    problem(w.call("GET", "/v1/sessions/session-0001/lineage"), 403, "subject_forbidden")
    assert w.denials.calls[0][1][0].payload.reason == "subject_forbidden"
