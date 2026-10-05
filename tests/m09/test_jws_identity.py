"""`JwsIdentityVerifier` (adapters) y `TestIdentityIssuer` (testing/fakes): formato A de `raw_credential`.

JWS compacto `header.payload.firma`, Ed25519 (`alg: EdDSA`), `kid` en el header, `typ` distinto para
principal y delegación. El verificador solo valida la firma: la vigencia es de M9 con el `Clock`."""

import base64
import json
from datetime import timedelta
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from agent_core.adapters.jws_identity import JwsIdentityVerifier
from agent_core.domain import CredentialsInvalid, GrantCheckUnavailable
from testing.builders import NOW, advisor_with_delegation, principal
from testing.fakes.clock import FakeClock
from testing.fakes.identity import (
    DELEGATION_TYP,
    PRINCIPAL_TYP,
    TestIdentityIssuer,
    b64,
    sign_jws,
)


def issuer() -> TestIdentityIssuer:
    return TestIdentityIssuer(FakeClock())


def verifier(i: TestIdentityIssuer | None = None) -> JwsIdentityVerifier:
    return (i or issuer()).verifier()


def other_key() -> Ed25519PrivateKey:
    return Ed25519PrivateKey.from_private_bytes(bytes(range(32)))


def token(payload: Any, *, key: Ed25519PrivateKey | None = None, **header: Any) -> str:
    """Un JWS con el header y payload que se pidan (para armar tokens malformados a propósito)."""
    i = issuer()
    head = {"alg": "EdDSA", "kid": i.principal_kid, "typ": PRINCIPAL_TYP} | header
    head = {k: v for k, v in head.items() if v is not None}
    raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
    return sign_jws(head, raw, key or i.principal_key)


def payload_of(who: Any) -> dict[str, Any]:
    return json.loads(json.dumps(who.model_dump(mode="json")))


# --- camino feliz --------------------------------------------------------------------------------------


def test_principal_round_trips() -> None:
    i = issuer()
    who = principal()
    assert verifier(i).verify(i.issue(who)) == who


def test_delegation_round_trips() -> None:
    i = issuer()
    _, obo = advisor_with_delegation()
    assert verifier(i).verify_delegation(i.issue_delegation(obo)) == obo


def test_verify_does_not_check_expiry() -> None:  # la vigencia es de M9 con el Clock (puerto)
    i = issuer()
    old = principal(exp=NOW - timedelta(days=1))
    assert verifier(i).verify(i.issue(old)) == old


# --- firma y formato: todo falla cerrado ----------------------------------------------------------------


def test_tampered_payload_is_rejected() -> None:
    i = issuer()
    head, _, sig = i.issue(principal()).split(".")
    forged = b64(json.dumps(payload_of(principal(id="cust-999"))).encode())
    with pytest.raises(CredentialsInvalid):
        verifier(i).verify(f"{head}.{forged}.{sig}")


def test_tampered_signature_is_rejected() -> None:
    i = issuer()
    head, body, sig = i.issue(principal()).split(".")
    flipped = ("A" if sig[0] != "A" else "B") + sig[1:]
    with pytest.raises(CredentialsInvalid):
        verifier(i).verify(f"{head}.{body}.{flipped}")


def test_signature_by_another_key_is_rejected() -> None:
    with pytest.raises(CredentialsInvalid):
        verifier().verify(token(payload_of(principal()), key=other_key()))


def test_unknown_kid_is_rejected() -> None:
    with pytest.raises(CredentialsInvalid):
        verifier().verify(token(payload_of(principal()), kid="otra-clave"))


@pytest.mark.parametrize("alg", ["none", "HS256", "RS256", "eddsa", None])
def test_only_eddsa_is_accepted(alg: str | None) -> None:
    with pytest.raises(CredentialsInvalid):
        verifier().verify(token(payload_of(principal()), alg=alg))


def test_extra_header_fields_are_rejected() -> None:
    with pytest.raises(CredentialsInvalid):
        verifier().verify(token(payload_of(principal()), crit=["x"]))


def test_a_delegation_token_is_not_a_principal_and_vice_versa() -> None:
    i = issuer()
    _, obo = advisor_with_delegation()
    with pytest.raises(CredentialsInvalid):
        verifier(i).verify(i.issue_delegation(obo))
    with pytest.raises(CredentialsInvalid):
        verifier(i).verify_delegation(i.issue(principal()))


def test_typ_is_enforced_even_with_a_valid_signature() -> None:
    with pytest.raises(CredentialsInvalid):
        verifier().verify(token(payload_of(principal()), typ=DELEGATION_TYP))


@pytest.mark.parametrize(
    "raw",
    ["", " ", "a", "a.b", "a.b.c.d", "..", "a..c", "aaa.bbb.ccc", "é.é.é", "a b.c.d", "x" * 9000],
)
def test_malformed_tokens_are_rejected(raw: str) -> None:
    with pytest.raises(CredentialsInvalid):
        verifier().verify(raw)


def test_padding_and_non_url_safe_base64_are_rejected() -> None:
    i = issuer()
    head, body, sig = i.issue(principal()).split(".")
    for bad in (f"{head}=.{body}.{sig}", f"{head}.{body}+.{sig}", f"{head}.{body}.{sig}/"):
        with pytest.raises(CredentialsInvalid):
            verifier(i).verify(bad)


@pytest.mark.parametrize(
    "payload",
    [
        b"no es json",
        b"[]",
        b'{"a": 1, "a": 2}',
        b"null",
        {"type": "customer"},
        {**payload_of(principal()), "extra": "x"},
        {**payload_of(principal()), "exp": "2026-09-28T12:00:00"},  # sin zona horaria
        {**payload_of(principal()), "id": None},  # anónimo por id pero con nivel de sesión
        {**payload_of(principal()), "type": "root"},
    ],
)
def test_signed_but_invalid_payloads_are_rejected(payload: Any) -> None:
    with pytest.raises(CredentialsInvalid):
        verifier().verify(token(payload))


def test_the_error_never_carries_the_credential() -> None:
    raw = "cabecera-secreta.cuerpo-secreto.firma-secreta"
    with pytest.raises(CredentialsInvalid) as info:
        verifier().verify(raw)
    assert "secreta" not in str(info.value) and "secreta" not in repr(info.value)


# --- rotación de claves --------------------------------------------------------------------------------


def test_rotation_by_kid() -> None:
    i = issuer()
    old_key, old_kid = i.principal_key, i.principal_kid
    new_key = other_key()
    both = JwsIdentityVerifier(
        principal_keys={old_kid: old_key.public_key(), "kid-2": new_key.public_key()},
        delegation_keys={i.delegation_kid: i.delegation_key.public_key()},
        grant_active=i.grant_active,
    )
    who = principal()
    assert both.verify(i.issue(who)) == who
    assert (
        both.verify(
            sign_jws(
                {"alg": "EdDSA", "kid": "kid-2", "typ": PRINCIPAL_TYP},
                json.dumps(payload_of(who)).encode(),
                new_key,
            )
        )
        == who
    )
    retired = JwsIdentityVerifier(
        principal_keys={"kid-2": new_key.public_key()},
        delegation_keys={},
        grant_active=i.grant_active,
    )
    with pytest.raises(CredentialsInvalid):
        retired.verify(i.issue(who))


# --- grant_active --------------------------------------------------------------------------------------


def test_grant_active_delegates_and_fails_closed() -> None:
    i = issuer()
    v = verifier(i)
    assert v.grant_active("grant-9", NOW) is True
    i.revoke("grant-9")
    assert v.grant_active("grant-9", NOW) is False

    def boom(grant_ref: str, now: Any) -> bool:
        raise RuntimeError("servicio de asignaciones caído")

    broken = JwsIdentityVerifier(principal_keys={}, delegation_keys={}, grant_active=boom)
    assert broken.grant_active("grant-9", NOW) is False

    def down(grant_ref: str, now: Any) -> bool:
        raise GrantCheckUnavailable("sin respuesta")

    unavailable = JwsIdentityVerifier(principal_keys={}, delegation_keys={}, grant_active=down)
    with pytest.raises(GrantCheckUnavailable):  # M9 la convierte en 503, no en "delegación vencida"
        unavailable.grant_active("grant-9", NOW)


# --- TestIdentityIssuer: la demo -----------------------------------------------------------------------


def test_issuer_emits_the_demo_principals() -> None:
    i = issuer()
    v = verifier(i)
    customer = v.verify(i.customer())
    assert (customer.type.value, customer.id, customer.auth.level.value) == (
        "customer",
        "cust-001",
        "session",
    )
    token_a, token_d = i.advisor()
    advisor, obo = v.verify(token_a), v.verify_delegation(token_d)
    assert advisor.type.value == "advisor" and obo.grantee == advisor.key
    anon = v.verify(i.anonymous("anon-7"))
    assert anon.id is None and anon.attrs["anon_session"] == "anon-7" and anon.auth.level.value == "anonymous"
    expired = v.verify(i.expired())
    assert expired.exp <= FakeClock().now()
    stepped = v.verify(i.stepped_up())
    assert stepped.auth.level.value == "step_up" and stepped.auth.simulated is True


def test_issuer_uses_the_injected_clock_and_is_deterministic() -> None:
    a, b = issuer(), issuer()
    assert a.customer() == b.customer()
    clock = FakeClock()
    later = TestIdentityIssuer(clock, ttl=timedelta(minutes=5))
    who = later.verifier().verify(later.customer())
    assert who.exp == NOW + timedelta(minutes=5) and who.auth.at == NOW


def test_signing_keys_are_distinct_and_labeled_as_test_keys() -> None:
    i = issuer()
    assert i.principal_kid.startswith("test-") and i.delegation_kid.startswith("test-")
    assert i.principal_key.public_key() != i.delegation_key.public_key()


def test_b64_is_unpadded_url_safe() -> None:
    assert b64(b"\xfb\xff\xfe") == "-__-"
    assert base64.urlsafe_b64decode("-__-") == b"\xfb\xff\xfe"
