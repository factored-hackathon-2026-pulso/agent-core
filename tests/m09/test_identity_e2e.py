"""La API con el verificador real (`JwsIdentityVerifier`) y las credenciales de `TestIdentityIssuer`."""

import json
from datetime import timedelta

import pytest

from testing.fakes.clock import FakeClock
from testing.fakes.identity import TestIdentityIssuer
from tests.m09.test_api import World, problem


class Real(World):
    def __init__(self) -> None:
        clock = FakeClock()  # el reloj de la API es el del emisor
        self.issuer = TestIdentityIssuer(clock)
        super().__init__(verifier=self.issuer.verifier(), clock=clock)


def bearer(token: str) -> str:
    return f"Bearer {token}"


def test_customer_credential_opens_a_run() -> None:
    w = Real()
    assert w.create_run(bearer(w.issuer.customer())).status_code == 201
    assert w.turns.calls[0][1].id == "cust-001"


def test_advisor_credential_and_delegation_open_a_run_for_the_delegated_customer() -> None:
    w = Real()
    principal_token, delegation_token = w.issuer.advisor()
    resp = w.create_run(bearer(principal_token), **{"X-On-Behalf-Of": delegation_token})
    assert resp.status_code == 201
    assert w.turns.calls[0][3].subject.ref == "cust-001"


def test_anonymous_credential_reaches_only_public_agents() -> None:
    w = Real()
    anon = bearer(w.issuer.anonymous("anon-9"))
    assert w.create_run(anon, body={"agent": "faq"}).status_code == 201
    problem(w.create_run(anon), 403, "agent_forbidden")


def test_expired_credential_is_401_principal_expired() -> None:
    w = Real()
    problem(w.create_run(bearer(w.issuer.expired())), 401, "principal_expired")
    assert w.turns.calls == []


def test_credential_expires_with_the_clock() -> None:
    w = Real()
    token = bearer(w.issuer.customer())
    assert w.create_run(token).status_code == 201
    w.clock.advance(timedelta(hours=1))
    problem(w.create_run(token), 401, "principal_expired")


def test_step_up_credential_is_a_valid_elevated_principal() -> None:
    w = Real()
    resp = w.create_run(bearer(w.issuer.stepped_up()))
    assert resp.status_code == 201
    assert w.turns.calls[0][1].auth.simulated is True


def test_revoked_delegation_is_delegation_expired() -> None:
    w = Real()
    principal_token, delegation_token = w.issuer.advisor()
    w.issuer.revoke("grant-adv-7-cust-001")
    resp = w.create_run(bearer(principal_token), **{"X-On-Behalf-Of": delegation_token})
    problem(resp, 403, "delegation_expired")


def test_forged_and_swapped_credentials_are_401() -> None:
    w = Real()
    good = w.issuer.customer()
    head, body, sig = good.split(".")
    tampered = f"{head}.{body[:-2]}AA.{sig}"
    principal_token, delegation_token = w.issuer.advisor()
    for bad in (tampered, delegation_token, "no-es-un-jws"):
        problem(w.create_run(bearer(bad)), 401, "credentials_invalid")
    # una delegación firmada no vale como principal, ni un principal como delegación
    problem(
        w.create_run(bearer(principal_token), **{"X-On-Behalf-Of": principal_token}),
        401,
        "credentials_invalid",
    )
    assert w.turns.calls == []


def test_bare_token_without_bearer_still_works() -> None:
    w = Real()
    assert w.create_run(w.issuer.customer()).status_code == 201


def test_the_issued_tokens_carry_no_secrets_only_principal_fields() -> None:
    w = Real()
    payload = w.issuer.customer().split(".")[1]
    from agent_core.adapters.jws_identity import b64url_decode

    assert set(json.loads(b64url_decode(payload))) == {
        "type",
        "id",
        "roles",
        "scopes",
        "attrs",
        "auth",
        "exp",
    }


@pytest.mark.parametrize("scheme", ["Bearer", "bearer"])
def test_scheme_case_is_ignored(scheme: str) -> None:
    w = Real()
    assert w.create_run(f"{scheme} {w.issuer.customer()}").status_code == 201
