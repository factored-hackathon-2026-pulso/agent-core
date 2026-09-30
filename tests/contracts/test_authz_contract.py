"""Contrato de `AuthzPort` (ADR 0006, ADR 0019): lo que toda implementación debe cumplir, la de la unidad 3
incluida, contra `TableAuthz`. Dos reglas de seguridad de los agentes internos:

- un `builder` nunca obtiene datos de clientes (ni subject, ni campos, ni parámetros vinculados de cliente);
- un `advisor` solo actúa sobre el subject de su delegación, y solo si él es el `grantee`.

Cada regla tiene su sanidad negativa: un doble permisivo debe hacer fallar el chequeo."""

from datetime import timedelta

import pytest

from agent_core.domain import OnBehalfOf, Principal, SubjectRef
from agent_core.ports import AuthzDecision, AuthzPort
from testing.builders import NOW, advisor_with_delegation, principal
from testing.fakes.authz import TableAuthz

CUSTOMER = SubjectRef(kind="customer", ref="cust-001")
OTHER_CUSTOMER = SubjectRef(kind="customer", ref="cust-999")
FIELD, PURPOSE = "amount", "transcript_read"


def _builder(**over: object) -> Principal:
    return principal(type="builder", id="b-1", attrs={}, roles=["constructor"], scopes=["subject:*"], **over)


def _delegation(grantee_id: str = "adv-7", subject: SubjectRef = CUSTOMER) -> OnBehalfOf:
    return OnBehalfOf.model_validate({
        "subject": subject.model_dump(), "grant_ref": "grant-9",
        "grantee": {"type": "advisor", "id": grantee_id}, "scopes": ["read"], "exp": NOW + timedelta(hours=1),
    })


def check_builder_never_gets_a_customer_subject(authz: AuthzPort) -> None:
    assert not authz.authorize_subject(_builder(), None, CUSTOMER).allowed
    assert not authz.authorize_subject(_builder(), None, OTHER_CUSTOMER).allowed


def check_builder_never_reads_fields(authz: AuthzPort) -> None:
    assert not authz.can_read_field(_builder(), None, FIELD, PURPOSE)


def check_builder_binds_no_customer_reference(authz: AuthzPort) -> None:
    bound = authz.bind_params(_builder(), None, CUSTOMER)
    assert "subject_ref" not in bound
    assert CUSTOMER.ref not in bound.values()


def check_advisor_only_on_the_delegated_subject(authz: AuthzPort) -> None:
    advisor, obo = advisor_with_delegation()
    assert authz.authorize_subject(advisor, obo, CUSTOMER).allowed
    assert not authz.authorize_subject(advisor, obo, OTHER_CUSTOMER).allowed
    assert not authz.authorize_subject(advisor, None, CUSTOMER).allowed


def check_advisor_needs_to_be_the_grantee(authz: AuthzPort) -> None:
    advisor, _ = advisor_with_delegation()
    assert not authz.authorize_subject(advisor, _delegation(grantee_id="adv-8"), CUSTOMER).allowed


def check_advisor_params_come_from_the_delegation(authz: AuthzPort) -> None:
    advisor, obo = advisor_with_delegation()
    assert authz.bind_params(advisor, obo, OTHER_CUSTOMER) == {"subject_ref": CUSTOMER.ref}
    assert authz.bind_params(advisor, None, CUSTOMER) == {}


def check_customer_only_its_own_subject(authz: AuthzPort) -> None:
    assert authz.authorize_subject(principal(), None, CUSTOMER).allowed
    assert not authz.authorize_subject(principal(), None, OTHER_CUSTOMER).allowed


def check_denials_carry_no_subject_reference(authz: AuthzPort) -> None:
    advisor, _ = advisor_with_delegation()
    for decision in (
        authz.authorize_subject(_builder(), None, OTHER_CUSTOMER),
        authz.authorize_subject(advisor, None, OTHER_CUSTOMER),
        authz.authorize_subject(principal(), None, OTHER_CUSTOMER),
    ):
        assert not decision.allowed
        assert OTHER_CUSTOMER.ref not in (decision.reason or "")


CHECKS = [
    check_builder_never_gets_a_customer_subject,
    check_builder_never_reads_fields,
    check_builder_binds_no_customer_reference,
    check_advisor_only_on_the_delegated_subject,
    check_advisor_needs_to_be_the_grantee,
    check_advisor_params_come_from_the_delegation,
    check_customer_only_its_own_subject,
    check_denials_carry_no_subject_reference,
]


@pytest.fixture
def authz() -> AuthzPort:
    # Con la concesión más amplia posible: la regla del builder no depende de que nadie le niegue el campo.
    return TableAuthz(field_grants={(FIELD, PURPOSE)})


@pytest.mark.parametrize("check", CHECKS, ids=lambda c: c.__name__)
def test_authz_contract(authz: AuthzPort, check: object) -> None:
    check(authz)  # type: ignore[operator]


# --- sanidad negativa: el contrato debe poder fallar -----------------------------------------------------


class _Permissive:
    """Doble que autoriza todo, con el subject y los campos que se le pidan."""

    def authorize_agent(
        self, principal: Principal, agent: object, subject: SubjectRef | None
    ) -> AuthzDecision:
        return AuthzDecision(allowed=True)

    def authorize_subject(self, principal: Principal, obo: OnBehalfOf | None,
                          subject: SubjectRef | None) -> AuthzDecision:
        return AuthzDecision(allowed=True)

    def bind_params(self, principal: Principal, obo: OnBehalfOf | None,
                    subject: SubjectRef | None) -> dict[str, str]:
        return {} if subject is None else {"subject_ref": subject.ref}

    def can_read_field(self, reader: Principal, obo: OnBehalfOf | None, field: str, purpose: str) -> bool:
        return True

    def reportable_attrs(self) -> frozenset[str]:
        return frozenset()


@pytest.mark.parametrize("check", CHECKS[:6] + CHECKS[7:], ids=lambda c: c.__name__)
def test_a_permissive_authz_fails_the_contract(check: object) -> None:
    with pytest.raises(AssertionError):
        check(_Permissive())  # type: ignore[operator,arg-type]
