"""El emisor del staff tiene su propia clave: la API del registry no acepta credenciales de clientes."""

import pytest

from agent_core.domain import CredentialsInvalid
from testing.fakes.clock import FakeClock
from testing.fakes.identity import TestIdentityIssuer, TestStaffIssuer


def test_the_staff_verifier_rejects_a_credential_from_the_customer_issuer() -> None:
    clock = FakeClock()
    with pytest.raises(CredentialsInvalid):
        TestStaffIssuer(clock).verifier().verify(TestIdentityIssuer(clock).customer("cust-001"))


def test_the_customer_verifier_rejects_a_credential_from_the_staff_issuer() -> None:
    clock = FakeClock()
    with pytest.raises(CredentialsInvalid):
        TestIdentityIssuer(clock).verifier().verify(TestStaffIssuer(clock).admin())


def test_the_staff_personas_carry_the_expected_roles_and_actor() -> None:
    clock = FakeClock()
    issuer = TestStaffIssuer(clock)
    verifier = issuer.verifier()
    supervisor, admin, bot = (verifier.verify(t) for t in (issuer.supervisor(), issuer.admin(),
                                                          issuer.constructor_bot()))
    assert set(supervisor.roles) == {"constructor", "aprobador"} and supervisor.attrs["actor"] == "human"
    assert set(admin.roles) == {"constructor", "aprobador", "admin"} and admin.auth.level == "step_up"
    assert set(bot.roles) == {"constructor"} and "actor" not in bot.attrs
