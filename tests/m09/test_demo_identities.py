"""Script que emite las credenciales de la demo (`python -m testing.demo_identities`)."""

import json

import pytest

from agent_core.domain import CredentialsInvalid
from testing.demo_identities import main
from testing.fakes.clock import FakeClock
from testing.fakes.identity import TestIdentityIssuer

NAMES = {"customer", "advisor", "advisor_delegation", "anonymous", "expired", "stepped_up"}


def run(capsys: pytest.CaptureFixture[str], *argv: str) -> dict[str, str]:
    assert main(list(argv), clock=FakeClock()) == 0
    return json.loads(capsys.readouterr().out)  # type: ignore[no-any-return]


def test_prints_json_with_every_demo_credential(capsys: pytest.CaptureFixture[str]) -> None:
    out = run(capsys)
    assert NAMES <= set(out)
    assert "TEST" in out["_note"]  # etiquetado: claves de prueba, no de producción


def test_the_credentials_verify_with_the_issuer_verifier(capsys: pytest.CaptureFixture[str]) -> None:
    out = run(capsys)
    v = TestIdentityIssuer(FakeClock()).verifier()
    assert v.verify(out["customer"]).id == "cust-001"
    assert v.verify_delegation(out["advisor_delegation"]).grantee == v.verify(out["advisor"]).key
    assert v.verify(out["anonymous"]).id is None
    assert v.verify(out["stepped_up"]).auth.simulated is True
    assert v.verify(out["expired"]).exp <= FakeClock().now()


def test_ids_can_be_chosen(capsys: pytest.CaptureFixture[str]) -> None:
    out = run(capsys, "--customer", "cust-042", "--anon-session", "anon-x")
    v = TestIdentityIssuer(FakeClock()).verifier()
    assert v.verify(out["customer"]).id == "cust-042"
    assert v.verify(out["anonymous"]).attrs["anon_session"] == "anon-x"


def test_a_foreign_key_does_not_verify(capsys: pytest.CaptureFixture[str]) -> None:
    out = run(capsys)
    head, body, sig = out["customer"].split(".")
    with pytest.raises(CredentialsInvalid):
        TestIdentityIssuer(FakeClock()).verifier().verify(f"{head}.{body}.{sig[::-1]}")
