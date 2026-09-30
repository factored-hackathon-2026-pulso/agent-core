"""M9 F5: endpoints de `/v1` con `TestClient` (T-M9-01…14 salvo idempotencia, en test_idempotency.py)."""

from datetime import timedelta
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient

from agent_core.api.app import ApiDeps, create_app
from agent_core.api.limits import RateLimitConfig
from agent_core.domain import (
    Agent,
    Awaiting,
    ConfirmationPrompt,
    EngineError,
    Message,
    ProblemCode,
    Release,
    StepUpPrompt,
)
from testing.builders import NOW, advisor_with_delegation, principal, run_state
from testing.fakes.authz import TableAuthz
from testing.fakes.clock import FakeClock
from testing.fakes.ids import FakeIds
from testing.fakes.registry import InMemoryRegistry
from testing.fakes.storage import InMemoryCostCounters, InMemoryStore
from tests.m04.harness import agent_data
from tests.m09.helpers import (
    FakeHandoffs,
    FakeTranscripts,
    FakeTurns,
    RecordingDenials,
    RecordingSecurityLog,
    StubVerifier,
    anonymous,
    delegation,
    turn_result,
)

TURN = {"text": "hola", "channel": "web", "client_turn_id": "ct-1"}
ROUTES: list[tuple[str, str, dict[str, Any] | None]] = [
    ("POST", "/v1/runs", {"agent": "atencion"}),
    ("POST", "/v1/sessions/session-0001/turns", TURN),
    ("GET", "/v1/runs/run-0001", None),
    ("GET", "/v1/runs/run-0001/transcript", None),
    ("GET", "/v1/handoffs/h-1", None),
    ("POST", "/v1/handoffs/h-1/resolution", {"resolution_code": "resuelto", "handoff_quality": "useful"}),
]


class World:
    def __init__(
        self, limits: RateLimitConfig | None = None, verifier: Any = None, clock: FakeClock | None = None
    ) -> None:
        self.store = InMemoryStore()
        self.clock = clock or FakeClock()
        self.verifier = verifier or StubVerifier()
        self.turns = FakeTurns()
        self.handoffs = FakeHandoffs()
        self.transcripts = FakeTranscripts()
        self.denials = RecordingDenials()
        self.security = RecordingSecurityLog()
        self.registry = InMemoryRegistry()
        self.uow_opens = 0
        self.add_agent("atencion", invocable_by=["customer", "advisor", "service", "builder"])
        self.add_agent("faq", min_auth_level="anonymous", subject_kinds=[])
        deps = ApiDeps(
            verifier=self.verifier,
            authz=TableAuthz(),
            registry=self.registry,
            uow_factory=self._uow,
            counters=InMemoryCostCounters(self.store),
            clock=self.clock,
            ids=FakeIds(),
            turns=self.turns,
            handoffs=self.handoffs,
            transcripts=self.transcripts,
            denials=self.denials,
            security=self.security,
            limits=limits or RateLimitConfig(),
        )
        self.client = TestClient(create_app(deps), raise_server_exceptions=False)
        if isinstance(self.verifier, StubVerifier):
            self.verifier.register("tok-c", principal())
            advisor, obo = advisor_with_delegation()
            self.verifier.register("tok-a", advisor)
            self.verifier.register("tok-d", obo)

    def _uow(self):  # type: ignore[no-untyped-def]
        self.uow_opens += 1
        return self.store.uow()

    def add_agent(self, agent_id: str, **over: Any) -> None:
        self.registry.add(Agent.model_validate(agent_data(agent_id, **over)))
        release = Release.model_validate(
            {
                "id": f"rel-{agent_id}",
                "status": "active",
                "entities": {"agent": {agent_id: "1.0.0"}},
                "language_detection": "lang@1.0.0",
            }
        )
        self.registry.add_release(release, agent_id, alias="prod")
        self.registry.add_release(release, agent_id, alias=None, version="1.0.0")

    def seed_run(self, **over: Any) -> None:
        with self.store.uow() as uow:
            uow.save_run(run_state(**over), 0)
            uow.commit()
        self.uow_opens = 0

    def spend(self, times: int, cost: str = "0.10", who: str = "cust-001") -> None:
        key = principal(id=who).key
        with self.store.uow() as uow:
            for _ in range(times):
                uow.add_usage(key, Decimal(cost), self.clock.now())
            uow.commit()

    def call(self, method: str, path: str, token: str | None = "tok-c", body: Any = None, **headers: str):  # type: ignore[no-untyped-def]
        hdrs = dict(headers)
        if token is not None:
            hdrs.setdefault("Authorization", token)
        if method == "POST" and path == "/v1/runs":
            hdrs.setdefault("Idempotency-Key", "key-1")
        return self.client.request(method, path, json=body, headers=hdrs)

    def create_run(self, token: str | None = "tok-c", body: Any = None, **headers: str):  # type: ignore[no-untyped-def]
        return self.call(
            "POST", "/v1/runs", token, {"agent": "atencion"} if body is None else body, **headers
        )

    def turn(self, token: str | None = "tok-c", **headers: str):  # type: ignore[no-untyped-def]
        return self.call("POST", "/v1/sessions/session-0001/turns", token, TURN, **headers)


def problem(resp, status: int, code: str) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    assert resp.status_code == status, resp.text
    assert resp.headers["content-type"].startswith("application/problem+json")
    body = resp.json()
    assert body["code"] == code and body["trace_id"]
    return body  # type: ignore[no-any-return]


# --- camino feliz --------------------------------------------------------------------------------------


def test_create_run_returns_201_and_derives_the_subject_on_the_server() -> None:
    w = World()
    resp = w.create_run()
    assert resp.status_code == 201
    body = resp.json()
    assert (body["run_id"], body["session_id"], body["release"], body["status"]) == (
        "run-0001",
        "session-0001",
        "rel-atencion",
        "open",
    )
    assert body["trace_id"]
    ((name, who, obo, run_input),) = w.turns.calls
    assert name == "start_run" and who == principal() and obo is None
    assert run_input.subject.kind == "customer" and run_input.subject.ref == "cust-001"
    assert run_input.idempotency_key == "key-1"


def test_post_turn_returns_the_turn_result() -> None:
    w = World()
    w.seed_run()
    resp = w.turn()
    assert resp.status_code == 200
    body = resp.json()
    assert body["messages"] == [{"kind": "generated", "text": "hola", "locale": "es"}]
    assert (body["awaiting"], body["status"], body["locale"]) == ("none", "open", "es")
    assert body["run_id"] == "run-0001" and body["trace_id"]
    ((name, who, _, turn),) = w.turns.calls
    assert name == "handle_turn" and who == principal()
    assert (turn.session_id, turn.client_turn_id, turn.channel) == ("session-0001", "ct-1", "web")


def test_confirmation_is_published_with_action_summary_and_without_internals() -> None:
    w = World()
    w.seed_run()
    w.turns.turn_result = turn_result(
        awaiting=Awaiting.confirmation,
        confirmation=ConfirmationPrompt(
            action_id="action-0001",
            token="tok-confirm",
            expires_at=NOW + timedelta(minutes=5),
            summary=Message(kind="template", text="¿Confirmas?", locale="es"),
        ),
    )
    confirmation = w.turn().json()["confirmation"]
    assert set(confirmation) == {"action_summary", "token", "expires_at"}
    assert confirmation["action_summary"] == "¿Confirmas?"
    assert "action-0001" not in str(confirmation)


def test_step_up_is_returned_in_the_body() -> None:  # T-M9-14
    w = World()
    w.seed_run()
    w.turns.turn_result = turn_result(
        awaiting=Awaiting.step_up, step_up=StepUpPrompt(required_level="step_up", reason="transferencia")
    )
    resp = w.turn()
    assert resp.status_code == 200
    body = resp.json()
    assert body["awaiting"] == "step_up"
    assert body["step_up"] == {"required_level": "step_up", "reason": "transferencia", "simulated": True}


def test_decimals_survive_the_request_body() -> None:  # regla dura 4: todo JSON entra con `loads`
    w = World()
    resp = w.create_run(body={"agent": "atencion", "input": {"monto": 1.10, "n": 2}})
    assert resp.status_code == 201
    sent = w.turns.calls[0][3].input
    assert sent == {"monto": Decimal("1.10"), "n": 2} and isinstance(sent["monto"], Decimal)


@pytest.mark.parametrize("raw", ['{"agent":"atencion","input":{"a":NaN}}', '{"agent":"a","agent":"b"}', "{"])
def test_malformed_or_ambiguous_json_is_422(raw: str) -> None:
    w = World()
    resp = w.client.post(
        "/v1/runs",
        content=raw,
        headers={"Authorization": "tok-c", "Idempotency-Key": "k", "Content-Type": "application/json"},
    )
    problem(resp, 422, "invalid_request")
    assert w.turns.calls == []


def test_idempotency_key_is_required_on_create_run() -> None:
    w = World()
    resp = w.client.post("/v1/runs", json={"agent": "atencion"}, headers={"Authorization": "tok-c"})
    problem(resp, 422, "invalid_request")


# --- T-M9-01/02/03/04/05: autorización e IDOR ----------------------------------------------------------


def test_foreign_subject_on_create_run_is_403_subject_forbidden() -> None:  # T-M9-01
    w = World()
    resp = w.create_run(body={"agent": "atencion", "subject": {"kind": "customer", "ref": "cust-999"}})
    problem(resp, 403, "subject_forbidden")
    assert w.turns.calls == []


def test_foreign_subject_on_an_existing_run_is_403_and_recorded() -> None:  # T-M9-01
    w = World()
    w.seed_run(subject={"kind": "customer", "ref": "cust-999"})
    problem(w.turn(), 403, "subject_forbidden")
    assert w.turns.calls == []
    assert w.denials.calls[0][1][0].payload.reason == "subject_forbidden"


def test_advisor_without_or_with_expired_delegation_is_403() -> None:  # T-M9-02
    w = World(limits=RateLimitConfig())
    problem(w.create_run("tok-a"), 403, "subject_forbidden")
    w.verifier.register("tok-old", delegation(exp=NOW - timedelta(seconds=1)))
    problem(w.create_run("tok-a", **{"X-On-Behalf-Of": "tok-old"}), 403, "delegation_expired")
    assert w.turns.calls == []


def test_advisor_with_a_valid_delegation_starts_a_run_for_the_delegated_subject() -> None:
    w = World()
    assert w.create_run("tok-a", **{"X-On-Behalf-Of": "tok-d"}).status_code == 201
    ((_, _, obo, run_input),) = w.turns.calls
    assert obo is not None and run_input.subject.ref == "cust-001"


def test_a_customer_id_in_the_body_never_replaces_the_principal() -> None:  # T-M9-03
    w = World()
    problem(w.create_run(body={"agent": "atencion", "customer_id": "cust-999"}), 422, "invalid_request")
    assert w.turns.calls == []
    w.create_run(body={"agent": "atencion", "subject": {"kind": "customer", "ref": "cust-001"}})
    assert w.turns.calls[0][3].subject.ref == "cust-001"


@pytest.mark.parametrize("selector", ["atencion@1.0.0", "atencion@canary"])
@pytest.mark.parametrize("token", ["tok-c", "tok-a"])
def test_customer_and_advisor_cannot_pin_a_version(token: str, selector: str) -> None:  # T-M9-04
    w = World()
    headers = {"X-On-Behalf-Of": "tok-d"} if token == "tok-a" else {}
    problem(w.create_run(token, body={"agent": selector}, **headers), 403, "version_pin_forbidden")
    assert w.turns.calls == []


def test_service_can_pin_a_version() -> None:
    w = World()
    w.verifier.register("tok-s", principal(type="service", id="svc-1", attrs={}, scopes=["subject:customer"]))
    body = {"agent": "atencion@1.0.0", "subject": {"kind": "customer", "ref": "cust-001"}}
    assert w.create_run("tok-s", body=body).status_code == 201


def test_anonymous_cannot_reach_personal_data() -> None:  # T-M9-05
    w = World()
    w.verifier.register("tok-anon", anonymous("anon-1"))
    problem(w.create_run("tok-anon"), 403, "agent_forbidden")  # atencion exige subject customer
    problem(
        w.create_run("tok-anon", body={"agent": "faq", "subject": {"kind": "customer", "ref": "cust-001"}}),
        403,
        "subject_forbidden",
    )
    assert w.create_run("tok-anon", body={"agent": "faq"}).status_code == 201
    assert w.turns.calls[0][3].subject is None


def test_unknown_agent_is_404() -> None:
    problem(World().create_run(body={"agent": "no-existe"}), 404, "not_found")


# --- lecturas: run, transcript, IDOR -------------------------------------------------------------------


def test_get_run_summary_for_the_owner() -> None:
    w = World()
    w.seed_run()
    resp = w.call("GET", "/v1/runs/run-0001")
    assert resp.status_code == 200
    assert resp.json() == {
        "run_id": "run-0001",
        "status": "open",
        "outcome": None,
        "locale": "es",
        "awaiting": "none",
        "handoff_ref": None,
        "trace_id": resp.json()["trace_id"],
    }


def test_get_run_denies_another_customer_and_records_it() -> None:  # IDOR
    w = World()
    w.seed_run()
    w.verifier.register("tok-other", principal(id="cust-002"))
    problem(w.call("GET", "/v1/runs/run-0001", "tok-other"), 403, "subject_forbidden")
    assert w.denials.calls[0][1][0].payload.reason == "subject_forbidden"


def test_get_run_allows_an_advisor_with_a_delegation_for_the_subject() -> None:
    w = World()
    w.seed_run()
    assert w.call("GET", "/v1/runs/run-0001", "tok-a", **{"X-On-Behalf-Of": "tok-d"}).status_code == 200
    w.verifier.register("tok-d2", delegation(subject_ref="cust-777", grant_ref="grant-2"))
    problem(
        w.call("GET", "/v1/runs/run-0001", "tok-a", **{"X-On-Behalf-Of": "tok-d2"}), 403, "subject_forbidden"
    )


def test_a_run_without_subject_is_readable_only_by_its_owner() -> None:  # IDOR de anónimos
    w = World()
    mine = anonymous("anon-1")
    w.seed_run(principal=mine, subject=None)
    w.verifier.register("tok-1", mine)
    w.verifier.register("tok-2", anonymous("anon-2"))
    assert w.call("GET", "/v1/runs/run-0001", "tok-1").status_code == 200
    problem(w.call("GET", "/v1/runs/run-0001", "tok-2"), 403, "subject_forbidden")
    problem(
        w.call("GET", "/v1/runs/run-0001", "tok-c"), 403, "subject_forbidden"
    )  # ni un customer con sesión


def test_get_unknown_run_is_404() -> None:
    problem(World().call("GET", "/v1/runs/run-9999"), 404, "not_found")


def test_transcript_for_the_owner_is_rendered_by_the_reader() -> None:
    w = World()
    w.seed_run()
    resp = w.call("GET", "/v1/runs/run-0001/transcript")
    assert resp.status_code == 200
    body = resp.json()
    assert body["run_id"] == "run-0001" and body["trace_id"]
    assert [e["role"] for e in body["entries"]] == ["user", "assistant"]
    assert set(body["entries"][0]) == {"turn_id", "role", "text", "reason", "unknown_tokens"}
    ((run_id, reader, obo),) = w.transcripts.calls
    assert (run_id, reader, obo) == ("run-0001", principal(), None)


def test_transcript_of_someone_else_never_reaches_the_reader() -> None:  # IDOR
    w = World()
    w.seed_run(subject={"kind": "customer", "ref": "cust-999"}, principal=principal(id="cust-999"))
    problem(w.call("GET", "/v1/runs/run-0001/transcript"), 403, "subject_forbidden")
    assert w.transcripts.calls == []


def test_transcript_run_not_found_from_the_reader_is_404() -> None:
    w = World()
    w.seed_run()
    w.transcripts.error = LookupError("run-0001")
    problem(w.call("GET", "/v1/runs/run-0001/transcript"), 404, "not_found")


def test_handoff_get_and_resolution_are_delegated_with_the_reader() -> None:
    w = World()
    resp = w.call("GET", "/v1/handoffs/h-1", "tok-a", **{"X-On-Behalf-Of": "tok-d"})
    assert resp.status_code == 200 and resp.json()["handoff_ref"] == "h-1" and resp.json()["trace_id"]
    resp = w.call(
        "POST",
        "/v1/handoffs/h-1/resolution",
        "tok-a",
        {"resolution_code": "resuelto", "handoff_quality": "useful", "notes": "ok"},
        **{"X-On-Behalf-Of": "tok-d"},
    )
    assert resp.status_code == 200
    assert resp.json()["handoff_ref"] == "h-1" and resp.json()["trace_id"]
    kind, (ref, reader, code, quality, notes, obo) = w.handoffs.calls[1]
    assert (kind, ref, code, quality, notes) == ("resolve", "h-1", "resuelto", "useful", "ok")
    assert reader == advisor_with_delegation()[0] and obo is not None


def test_handoff_errors_are_translated() -> None:
    w = World()
    w.handoffs.error = EngineError(ProblemCode.handoff_already_resolved)
    problem(w.call("GET", "/v1/handoffs/h-1"), 409, "handoff_already_resolved")
    w.handoffs.error = EngineError(ProblemCode.subject_forbidden, "sin_delegacion")
    problem(w.call("GET", "/v1/handoffs/h-1"), 403, "subject_forbidden")


def test_invalid_resolution_quality_is_422() -> None:
    w = World()
    body = {"resolution_code": "resuelto", "handoff_quality": "regular"}
    problem(w.call("POST", "/v1/handoffs/h-1/resolution", "tok-a", body), 422, "invalid_request")
    assert w.handoffs.calls == []


# --- T-M9-06/07/08/09/10: credenciales -----------------------------------------------------------------


@pytest.mark.parametrize(("method", "path", "body"), ROUTES)
def test_invalid_signature_is_401_and_touches_nothing(method: str, path: str, body: Any) -> None:  # T-M9-06
    w = World()
    w.seed_run()
    resp = w.call(method, path, "firma-falsa", body)
    problem(resp, 401, "credentials_invalid")
    assert (w.turns.calls, w.transcripts.calls, w.handoffs.calls) == ([], [], [])
    assert w.denials.calls == []  # nada en la cadena de ningún run
    assert w.uow_opens == 0  # ni siquiera se leyó el estado
    assert [e["reason"] for e in w.security.entries] == ["credentials_invalid"]


@pytest.mark.parametrize(("method", "path", "body"), ROUTES)
def test_missing_authorization_is_401(method: str, path: str, body: Any) -> None:
    w = World()
    problem(w.call(method, path, None, body), 401, "credentials_invalid")
    assert w.turns.calls == []


def test_expired_principal_is_401_and_the_renewed_retry_is_processed_once() -> None:  # T-M9-07
    w = World()
    w.seed_run()
    w.verifier.register("tok-old", principal(exp=NOW + timedelta(minutes=1)))
    w.clock.advance(timedelta(minutes=2))
    problem(w.turn("tok-old"), 401, "principal_expired")
    assert w.turns.calls == []
    assert w.denials.calls[0][1][0].payload.reason == "principal_expired"
    w.verifier.register("tok-new", principal(exp=NOW + timedelta(hours=2)))
    assert w.turn("tok-new").status_code == 200
    ((_, _, _, turn),) = w.turns.calls
    assert turn.client_turn_id == "ct-1"


def test_revoked_delegation_is_403_delegation_expired() -> None:  # T-M9-08
    w = World()
    w.verifier.revoked.add("grant-9")
    problem(w.create_run("tok-a", **{"X-On-Behalf-Of": "tok-d"}), 403, "delegation_expired")
    assert w.turns.calls == []


def test_other_advisor_with_a_valid_delegation_on_the_same_subject_is_principal_mismatch() -> None:  # T-M9-09
    w = World()
    advisor, obo = advisor_with_delegation()
    w.seed_run(principal=advisor, on_behalf_of=obo)
    w.verifier.register("tok-b", principal(type="advisor", id="adv-8", attrs={}))
    w.verifier.register("tok-db", delegation(grantee_id="adv-8", grant_ref="grant-10"))
    problem(w.turn("tok-b", **{"X-On-Behalf-Of": "tok-db"}), 403, "principal_mismatch")
    assert w.turns.calls == []
    assert w.denials.calls[0][1][0].payload.reason == "principal_mismatch"


def test_unknown_session_is_404_only_after_the_signature_is_valid() -> None:
    w = World()
    problem(w.turn("firma-falsa"), 401, "credentials_invalid")
    problem(w.turn("tok-c"), 404, "not_found")


def test_rate_excess_with_invalid_signature_does_not_consume_the_impersonated_quota() -> None:  # T-M9-10
    w = World(limits=RateLimitConfig(max_hits=2))
    w.seed_run()
    w.spend(2)  # el principal real ya está en su límite
    before = list(w.store.usage[principal().key])
    for _ in range(5):
        problem(w.turn("firma-falsa"), 401, "credentials_invalid")  # la firma se valida ANTES que el límite
    assert w.store.usage[principal().key] == before
    problem(w.turn("tok-c"), 429, "rate_limited")  # y su cuota sigue exactamente donde estaba
    assert w.store.usage[principal().key] == before


# --- T-M9-12: límites ----------------------------------------------------------------------------------


def test_rate_and_cost_excess_are_429_and_never_reach_the_engine() -> None:  # T-M9-12
    w = World(limits=RateLimitConfig(max_hits=2, daily_budget_usd=Decimal("1.00")))
    w.seed_run()
    w.spend(2)
    problem(w.turn(), 429, "rate_limited")
    problem(w.create_run(), 429, "rate_limited")
    w.clock.advance(timedelta(seconds=61))
    assert w.turn().status_code == 200
    w2 = World(limits=RateLimitConfig(max_hits=99, daily_budget_usd=Decimal("1.00")))
    w2.seed_run()
    w2.spend(1, cost="1.00")
    problem(w2.turn(), 429, "cost_budget_exceeded")
    assert w2.turns.calls == []


# --- T-M9-13: trace_id y problem+json ------------------------------------------------------------------


@pytest.mark.parametrize(("method", "path", "body"), ROUTES)
def test_every_success_response_carries_a_trace_id(method: str, path: str, body: Any) -> None:  # T-M9-13
    w = World()
    w.seed_run()
    resp = w.call(method, path, "tok-c", body)
    if path.startswith("/v1/handoffs"):
        resp = w.call(method, path, "tok-a", body, **{"X-On-Behalf-Of": "tok-d"})
    assert resp.status_code in (200, 201), resp.text
    assert resp.json()["trace_id"]


@pytest.mark.parametrize(("method", "path", "body"), ROUTES)
def test_every_error_is_problem_json_with_a_trace_id(method: str, path: str, body: Any) -> None:  # T-M9-13
    w = World()
    problem(w.call(method, path, "firma-falsa", body), 401, "credentials_invalid")


def test_engine_errors_are_translated_with_their_status() -> None:
    w = World()
    w.seed_run()
    for code, status in [
        (ProblemCode.turn_in_progress, 409),
        (ProblemCode.run_closed, 410),
        (ProblemCode.internal_error, 500),
    ]:
        w.turns.error = EngineError(code)
        problem(w.turn(), status, code.value)
    w.turns.error = RuntimeError("secreto-interno")
    resp = w.turn()
    problem(resp, 500, "internal_error")
    assert "secreto-interno" not in resp.text
