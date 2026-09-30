"""M9 F2: `AccessGate` — credenciales, vigencia, delegación y coincidencia de principal (spec §3.1)."""

from datetime import timedelta

import pytest

from agent_core.api.gate import AccessGate
from agent_core.domain import AccessDenied, EngineError, ProblemCode, RunState
from testing.builders import NOW, advisor_with_delegation, principal, run_state
from testing.fakes.clock import FakeClock
from testing.fakes.ids import FakeIds
from tests.m09.helpers import (
    RecordingDenials,
    RecordingSecurityLog,
    StubVerifier,
    anonymous,
    delegation,
)


class World:
    def __init__(self) -> None:
        self.verifier = StubVerifier()
        self.clock = FakeClock()
        self.denials = RecordingDenials()
        self.security = RecordingSecurityLog()
        self.gate = AccessGate(self.verifier, self.clock, FakeIds(), self.denials, self.security)
        self.loads = 0

    def admit(self, auth: str | None, deleg: str | None = None, run: RunState | None = None):
        def load_run() -> RunState | None:
            self.loads += 1
            return run

        return self.gate.admit(auth, deleg, load_run, trace_id="trace-1")

    def denied(self, auth: str | None, deleg: str | None = None, run: RunState | None = None) -> EngineError:
        with pytest.raises(EngineError) as info:
            self.admit(auth, deleg, run)
        return info.value


def test_valid_credentials_are_admitted_without_side_effects() -> None:
    w = World()
    w.verifier.register("tok-c", principal())
    admitted = w.admit("tok-c", run=run_state())
    assert admitted.principal == principal()
    assert admitted.on_behalf_of is None
    assert w.denials.calls == [] and w.security.entries == []


def test_invalid_signature_is_401_and_touches_no_state() -> None:  # T-M9-06 (nivel puerta)
    w = World()
    err = w.denied("firma-falsa", run=run_state())
    assert err.code is ProblemCode.credentials_invalid and err.status == 401
    assert w.loads == 0  # ni siquiera se lee el run
    assert w.denials.calls == []  # nada en la cadena del run
    assert w.security.entries == [
        {"reason": "credentials_invalid", "principal_type": None, "trace_id": "trace-1"}
    ]


@pytest.mark.parametrize("raw", [None, "", "   "])
def test_missing_credential_is_credentials_invalid(raw: str | None) -> None:
    w = World()
    assert w.denied(raw).code is ProblemCode.credentials_invalid
    assert w.verifier.verify_calls == 0


def test_invalid_delegation_signature_is_credentials_invalid() -> None:
    w = World()
    advisor, _ = advisor_with_delegation()
    w.verifier.register("tok-a", advisor)
    assert w.denied("tok-a", "deleg-falsa").code is ProblemCode.credentials_invalid
    assert w.denials.calls == []


def test_expired_principal_is_401_and_logged_in_the_run_chain() -> None:  # T-M9-07 (nivel puerta)
    w = World()
    w.verifier.register("tok-c", principal(exp=NOW + timedelta(minutes=5)))
    w.clock.advance(timedelta(minutes=5))  # exp == now ya está vencido
    run = run_state()
    err = w.denied("tok-c", run=run)
    assert err.code is ProblemCode.principal_expired and err.status == 401
    ((run_id, events),) = w.denials.calls
    assert run_id == run.run_id
    (event,) = events
    assert isinstance(event, AccessDenied)
    assert event.payload.reason == "principal_expired"
    assert (event.run_id, event.release, event.session_id, event.ts) == (
        run.run_id,
        run.release,
        run.session_id,
        w.clock.now(),
    )
    assert w.security.entries[0]["reason"] == "principal_expired"


def test_expired_principal_without_run_goes_only_to_the_security_log() -> None:
    w = World()
    w.verifier.register("tok-c", principal(exp=NOW - timedelta(seconds=1)))
    assert w.denied("tok-c").code is ProblemCode.principal_expired
    assert w.denials.calls == []
    assert [e["reason"] for e in w.security.entries] == ["principal_expired"]


def test_expired_delegation_is_403() -> None:
    w = World()
    advisor, _ = advisor_with_delegation()
    w.verifier.register("tok-a", advisor)
    w.verifier.register("tok-d", delegation(exp=NOW - timedelta(seconds=1)))
    run = run_state(principal=advisor)
    err = w.denied("tok-a", "tok-d", run)
    assert err.code is ProblemCode.delegation_expired and err.status == 403
    assert w.denials.calls[0][1][0].payload.reason == "delegation_expired"


def test_revoked_grant_is_delegation_expired() -> None:  # T-M9-08 (nivel puerta)
    w = World()
    advisor, obo = advisor_with_delegation()
    w.verifier.register("tok-a", advisor)
    w.verifier.register("tok-d", obo)
    w.verifier.revoked.add(obo.grant_ref)
    assert w.denied("tok-a", "tok-d", run_state(principal=advisor)).code is ProblemCode.delegation_expired


def test_delegation_of_another_advisor_is_delegation_mismatch() -> None:
    w = World()
    other = principal(type="advisor", id="adv-8", attrs={})
    w.verifier.register("tok-o", other)
    w.verifier.register("tok-d", delegation(grantee_id="adv-7"))
    err = w.denied("tok-o", "tok-d", run_state(principal=other))
    assert err.code is ProblemCode.delegation_mismatch and err.status == 403
    assert w.denials.calls[0][1][0].payload.reason == "delegation_mismatch"


def test_delegation_presented_by_a_customer_is_delegation_mismatch() -> None:
    w = World()
    w.verifier.register("tok-c", principal())
    w.verifier.register("tok-d", delegation())
    assert w.denied("tok-c", "tok-d").code is ProblemCode.delegation_mismatch


def test_valid_delegation_is_admitted() -> None:
    w = World()
    advisor, obo = advisor_with_delegation()
    w.verifier.register("tok-a", advisor)
    w.verifier.register("tok-d", obo)
    admitted = w.admit("tok-a", "tok-d")
    assert admitted.on_behalf_of == obo


def test_other_advisor_with_valid_delegation_on_same_subject_is_principal_mismatch() -> None:  # T-M9-09
    w = World()
    advisor_a, _ = advisor_with_delegation()
    advisor_b = principal(type="advisor", id="adv-8", attrs={})
    w.verifier.register("tok-b", advisor_b)
    w.verifier.register("tok-db", delegation(grantee_id="adv-8", grant_ref="grant-10"))
    err = w.denied("tok-b", "tok-db", run_state(principal=advisor_a))
    assert err.code is ProblemCode.principal_mismatch and err.status == 403
    assert w.denials.calls[0][1][0].payload.reason == "principal_mismatch"


def test_different_principal_type_with_same_id_is_principal_mismatch() -> None:
    w = World()
    w.verifier.register("tok-s", principal(type="service", id="cust-001", attrs={}))
    assert w.denied("tok-s", run=run_state()).code is ProblemCode.principal_mismatch


def test_renewed_or_stepped_up_principal_keeps_the_run() -> None:
    w = World()
    stepped = principal(auth={"level": "step_up", "at": NOW}, exp=NOW + timedelta(hours=2))
    w.verifier.register("tok-new", stepped)
    assert w.admit("tok-new", run=run_state()).principal == stepped


def test_anonymous_needs_a_session_id() -> None:
    w = World()
    w.verifier.register("tok-anon", anonymous(attrs={}))
    assert w.denied("tok-anon").code is ProblemCode.credentials_invalid
    assert w.security.entries[0]["reason"] == "credentials_invalid"


def test_anonymous_can_only_reopen_its_own_run() -> None:  # IDOR entre anónimos
    w = World()
    mine = anonymous("anon-1")
    w.verifier.register("tok-1", mine)
    w.verifier.register("tok-2", anonymous("anon-2"))
    run = run_state(principal=mine, subject=None)
    assert w.admit("tok-1", run=run).principal == mine
    err = w.denied("tok-2", run=run)
    assert err.code is ProblemCode.principal_mismatch


def test_a_failing_audit_write_still_denies() -> None:
    w = World()
    w.denials.fail = True
    w.verifier.register("tok-c", principal(exp=NOW))
    assert w.denied("tok-c", run=run_state()).code is ProblemCode.principal_expired
    reasons = [e["reason"] for e in w.security.entries]
    assert reasons == ["principal_expired", "audit_write_failed"]


def test_security_log_never_carries_the_credential() -> None:
    w = World()
    w.denied("token-super-secreto")
    assert "token-super-secreto" not in repr(w.security.entries)


@pytest.mark.parametrize("raw", ["Bearer tok-c", "bearer tok-c", "BEARER   tok-c"])
def test_bearer_scheme_is_accepted_and_stripped(raw: str) -> None:
    w = World()
    w.verifier.register("tok-c", principal())
    assert w.admit(raw).principal == principal()


@pytest.mark.parametrize("raw", ["Bearer", "Bearer ", "Bearer    ", "Basic tok-c"])
def test_bearer_without_a_token_or_another_scheme_is_credentials_invalid(raw: str) -> None:
    w = World()
    w.verifier.register("tok-c", principal())
    assert w.denied(raw).code is ProblemCode.credentials_invalid
