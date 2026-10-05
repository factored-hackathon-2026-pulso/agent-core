"""Reject carries an optional closed-vocabulary `reason_code`; the free-text reason is never exposed."""

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from agent_core.domain import CredentialsInvalid, Principal
from agent_core.registry.http import registry_extension
from agent_core.registry.models import REASON_CODES, Origin, ProposalState
from tests.registry.helpers import AGENT, bot, human, prompt_draft
from tests.registry.service_world import SUITE, World

PRINCIPALS: dict[str, Principal] = {"ana": human(), "bot": bot("constructor", "aprobador")}
SECRET = "call Juan Perez 3001234567 about this"
ANA = human()


def _client() -> tuple[TestClient, World]:
    w = World()
    app = FastAPI()

    def authenticate(request: Request, authorization: str | None) -> Principal:
        if authorization not in PRINCIPALS:
            raise CredentialsInvalid()
        return PRINCIPALS[authorization]

    registry_extension(w.service)(app, authenticate)
    return TestClient(app, raise_server_exceptions=False), w


def _evaluated(w: World) -> str:
    p = w.service.create_proposal(ANA, AGENT, Origin.manual, "t")
    w.service.put_draft(ANA, p.proposal_id, [prompt_draft(), SUITE], expected_rev=0)
    w.service.freeze(ANA, p.proposal_id)
    w.service.evaluate(ANA, p.proposal_id, "disputas-suite")
    return p.proposal_id


def _reject(c: TestClient, pid: str, **extra: str):  # type: ignore[no-untyped-def]
    return c.post(f"/v1/registry/proposals/{pid}/reject", json={"reason": SECRET, **extra},
                  headers={"Authorization": "ana"})


def _detail(c: TestClient, pid: str) -> dict:  # type: ignore[type-arg]
    return c.get(f"/v1/registry/proposals/{pid}", headers={"Authorization": "ana"}).json()


def test_vocabulary_is_closed() -> None:
    assert set(REASON_CODES) == {"insufficient_evidence", "wrong_target", "risk", "duplicate",
                                 "policy_conflict", "wording", "other"}


def test_reject_with_code_is_stored_and_in_the_event() -> None:
    w = World()
    pid = _evaluated(w)
    p = w.service.reject(ANA, pid, "no", reason_code="duplicate")
    assert p.state is ProposalState.draft
    ev = [e for e in w.service.list_events(0, 1000) if e.type == "rejected"][-1]
    assert (ev.reason_code, ev.proposal_id, ev.agent_id) == ("duplicate", pid, AGENT)
    assert "no" not in ev.model_dump_json().replace("reason_code", "")


def test_reject_without_code_is_unchanged() -> None:
    w = World()
    pid = _evaluated(w)
    w.service.reject(ANA, pid, "el tono es muy seco")
    ev = [e for e in w.service.list_events(0, 1000) if e.type == "rejected"][-1]
    assert ev.reason_code is None
    assert w.service.get_proposal(pid).last_decision is not None


def test_http_invalid_code_is_422_and_nothing_changes() -> None:
    c, w = _client()
    pid = _evaluated(w)
    assert _reject(c, pid, reason_code="because").status_code == 422
    assert _detail(c, pid)["proposal"]["state"] == "evaluated"


def test_http_reason_text_stays_mandatory() -> None:
    c, w = _client()
    pid = _evaluated(w)
    r = c.post(f"/v1/registry/proposals/{pid}/reject", json={"reason_code": "risk"},
               headers={"Authorization": "ana"})
    assert r.status_code == 422


def test_http_detail_shows_code_but_never_the_free_text() -> None:
    c, w = _client()
    pid = _evaluated(w)
    assert _reject(c, pid, reason_code="wrong_target").status_code == 200
    r = c.get(f"/v1/registry/proposals/{pid}", headers={"Authorization": "ana"})
    last = r.json()["last_decision"]
    assert last["decision"] == "rejected" and last["reason_code"] == "wrong_target"
    assert last["decided_by_role"] == "approver" and last["decided_at"]
    assert "Juan" not in r.text and "3001234567" not in r.text and "actor" not in last
    export = [e for e in w.service.list_events(0, 1000) if e.type == "rejected"]
    assert "Juan" not in export[-1].model_dump_json()


def test_detail_without_decision_has_null_last_decision() -> None:
    c, w = _client()
    pid = _evaluated(w)
    assert _detail(c, pid)["last_decision"] is None


def test_without_code_http_returns_null_code() -> None:
    c, w = _client()
    pid = _evaluated(w)
    assert _reject(c, pid).status_code == 200
    assert _detail(c, pid)["last_decision"]["reason_code"] is None
