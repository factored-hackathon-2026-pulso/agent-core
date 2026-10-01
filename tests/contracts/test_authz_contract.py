"""Contrato de `AuthzPort` (ADR 0006, ADR 0019): lo que toda implementación debe cumplir, la de la unidad 3
incluida, contra `TableAuthz`. Dos reglas de seguridad de los agentes internos:

- un `builder` nunca obtiene datos de clientes (ni subject, ni campos, ni parámetros vinculados de cliente);
- un `advisor` solo actúa sobre el subject de su delegación, y solo si él es el `grantee`.

Cada regla tiene su sanidad negativa: un doble permisivo debe hacer fallar el chequeo."""

from datetime import timedelta

import pytest

from agent_core.domain import KnowledgeView, OnBehalfOf, Principal, Purpose, SubjectRef
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


def _admin(**over: object) -> Principal:
    base: dict[str, object] = {
        "type": "builder", "id": "root", "attrs": {"actor": "human"}, "scopes": ["subject:*"],
        "roles": ["constructor", "aprobador", "admin"], "auth": {"level": "step_up", "at": NOW}}
    return principal(**(base | over))


def check_only_the_platform_admin_reaches_customer_data(authz: AuthzPort) -> None:
    assert authz.authorize_subject(_admin(), None, CUSTOMER).allowed
    assert authz.can_read_field(_admin(), None, FIELD, PURPOSE)
    assert authz.bind_params(_admin(), None, CUSTOMER)["subject_ref"] == CUSTOMER.ref
    supervisor = _admin(roles=["constructor", "aprobador"])
    bot = _admin(roles=["constructor"], attrs={})
    for other in (supervisor, bot):
        assert not authz.authorize_subject(other, None, CUSTOMER).allowed
        assert not authz.can_read_field(other, None, FIELD, PURPOSE)
        assert "subject_ref" not in authz.bind_params(other, None, CUSTOMER)


def check_the_admin_needs_a_human_step_up_and_a_scope(authz: AuthzPort) -> None:
    weak_auth = _admin(auth={"level": "session", "at": NOW})
    not_human = _admin(attrs={})
    no_scope = _admin(scopes=[])
    for admin in (weak_auth, not_human, no_scope):
        assert not authz.authorize_subject(admin, None, CUSTOMER).allowed


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


PUBLIC_ONLY = KnowledgeView(audiences=frozenset({"public"}), approved_only=True)
EVERYTHING = KnowledgeView(audiences=frozenset({"public", "internal", "agent_only"}), approved_only=False)


def _purposes() -> tuple[Purpose, ...]:
    return ("customer_answer", "advisor_view", "agent_guidance")


def check_customer_answer_is_public_and_approved_for_every_principal(authz: AuthzPort) -> None:
    """M12 §3.1: solo lo `public` y `approved` puede citarse al cliente, sea quien sea el que pregunta."""
    advisor, _ = advisor_with_delegation()
    for reader in (principal(), advisor, _builder(), _admin()):
        assert authz.knowledge_view(reader, "customer_answer") == PUBLIC_ONLY


def check_advisor_view_never_reaches_a_customer(authz: AuthzPort) -> None:
    advisor, _ = advisor_with_delegation()
    assert authz.knowledge_view(principal(), "advisor_view").audiences == frozenset()
    view = authz.knowledge_view(advisor, "advisor_view")
    assert view.audiences == frozenset({"public", "internal"})


def check_agent_only_is_reachable_only_through_agent_guidance(authz: AuthzPort) -> None:
    advisor, _ = advisor_with_delegation()
    for reader in (principal(), advisor, _builder()):
        for purpose in ("customer_answer", "advisor_view"):
            assert "agent_only" not in authz.knowledge_view(reader, purpose).audiences
        assert "agent_only" in authz.knowledge_view(reader, "agent_guidance").audiences


def check_knowledge_view_is_deterministic_and_total(authz: AuthzPort) -> None:
    for purpose in _purposes():
        assert authz.knowledge_view(principal(), purpose) == authz.knowledge_view(principal(), purpose)


CHECKS = [
    check_builder_never_gets_a_customer_subject,
    check_builder_never_reads_fields,
    check_builder_binds_no_customer_reference,
    check_advisor_only_on_the_delegated_subject,
    check_advisor_needs_to_be_the_grantee,
    check_advisor_params_come_from_the_delegation,
    check_customer_only_its_own_subject,
    check_denials_carry_no_subject_reference,
    check_only_the_platform_admin_reaches_customer_data,
    check_the_admin_needs_a_human_step_up_and_a_scope,
    check_customer_answer_is_public_and_approved_for_every_principal,
    check_advisor_view_never_reaches_a_customer,
    check_agent_only_is_reachable_only_through_agent_guidance,
    check_knowledge_view_is_deterministic_and_total,
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

    def knowledge_view(self, principal: Principal, purpose: Purpose) -> KnowledgeView:
        return EVERYTHING

    def reportable_attrs(self) -> frozenset[str]:
        return frozenset()


@pytest.mark.parametrize("check", CHECKS[:6] + CHECKS[7:-1], ids=lambda c: c.__name__)
def test_a_permissive_authz_fails_the_contract(check: object) -> None:
    with pytest.raises(AssertionError):
        check(_Permissive())  # type: ignore[operator,arg-type]
