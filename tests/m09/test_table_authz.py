"""`TableAuthz` (testing/fakes): la tabla de ADR 0006 / spec general §4.2, como `AuthzPort` de prueba."""

import pytest

from agent_core.domain import Agent, SubjectRef
from testing.builders import advisor_with_delegation, principal
from testing.fakes.authz import TableAuthz
from tests.m04.harness import agent_data
from tests.m09.helpers import anonymous, delegation

authz = TableAuthz()
CUSTOMER = SubjectRef(kind="customer", ref="cust-001")


def agent(**over: object) -> Agent:
    return Agent.model_validate(agent_data(**over))


# --- authorize_agent -----------------------------------------------------------------------------------


def test_agent_allows_declared_principal_type_subject_kind_and_level() -> None:
    assert authz.authorize_agent(principal(), agent(), CUSTOMER).allowed


def test_agent_denies_principal_type_not_in_invocable_by() -> None:
    advisor, _ = advisor_with_delegation()
    decision = authz.authorize_agent(advisor, agent(), CUSTOMER)
    assert not decision.allowed and decision.reason == "principal_type"


def test_agent_denies_subject_kind_not_declared() -> None:
    decision = authz.authorize_agent(principal(), agent(), SubjectRef(kind="case", ref="c-1"))
    assert not decision.allowed and decision.reason == "subject_kind"


def test_agent_denies_insufficient_auth_level() -> None:
    weak = anonymous()
    decision = authz.authorize_agent(weak, agent(subject_kinds=[]), None)
    assert not decision.allowed and decision.reason == "auth_level"
    public = agent(subject_kinds=[], min_auth_level="anonymous")
    assert authz.authorize_agent(weak, public, None).allowed


def test_agent_requiring_a_subject_kind_denies_a_missing_subject() -> None:
    decision = authz.authorize_agent(principal(), agent(), None)
    assert not decision.allowed and decision.reason == "subject_required"


# --- authorize_subject ---------------------------------------------------------------------------------


def test_customer_only_its_own_subject() -> None:
    assert authz.authorize_subject(principal(), None, CUSTOMER).allowed
    other = SubjectRef(kind="customer", ref="cust-999")
    assert not authz.authorize_subject(principal(), None, other).allowed
    assert authz.authorize_subject(principal(), None, None).allowed


def test_anonymous_never_gets_a_subject() -> None:
    assert not authz.authorize_subject(anonymous(), None, CUSTOMER).allowed
    assert authz.authorize_subject(anonymous(), None, None).allowed


def test_advisor_needs_a_delegation_for_that_subject() -> None:
    advisor, obo = advisor_with_delegation()
    assert authz.authorize_subject(advisor, obo, CUSTOMER).allowed
    assert not authz.authorize_subject(advisor, None, CUSTOMER).allowed
    other = SubjectRef(kind="customer", ref="cust-999")
    assert not authz.authorize_subject(advisor, obo, other).allowed
    stolen = delegation(grantee_id="adv-8")
    assert not authz.authorize_subject(advisor, stolen, CUSTOMER).allowed


@pytest.mark.parametrize("who", ["service", "builder"])
def test_service_and_builder_by_scope(who: str) -> None:
    scoped = principal(type=who, id=f"{who}-1", attrs={}, scopes=["subject:customer"])
    assert authz.authorize_subject(scoped, None, CUSTOMER).allowed == (who == "service")
    bare = principal(type=who, id=f"{who}-1", attrs={}, scopes=[])
    assert not authz.authorize_subject(bare, None, CUSTOMER).allowed
    wildcard = principal(type="service", id="svc-1", attrs={}, scopes=["subject:*"])
    assert authz.authorize_subject(wildcard, None, CUSTOMER).allowed


# --- bind_params / campos ------------------------------------------------------------------------------


def test_bind_params_come_only_from_the_principal_or_the_delegation() -> None:
    assert authz.bind_params(principal(), None, CUSTOMER) == {"subject_ref": "cust-001"}
    advisor, obo = advisor_with_delegation()
    assert authz.bind_params(advisor, obo, CUSTOMER) == {"subject_ref": "cust-001"}
    assert authz.bind_params(anonymous(), None, None) == {}
    builder = principal(type="builder", id="b-1", attrs={})
    assert authz.bind_params(builder, None, None) == {"builder_id": "b-1"}


def test_fields_are_denied_unless_granted() -> None:
    assert not authz.can_read_field(principal(), None, "amount", "transcript_read")
    granted = TableAuthz(field_grants={("amount", "transcript_read")})
    assert granted.can_read_field(principal(), None, "amount", "transcript_read")
    assert not granted.can_read_field(anonymous(), None, "amount", "transcript_read")


def test_reportable_attrs_are_configurable() -> None:
    assert TableAuthz(reportable=frozenset({"country"})).reportable_attrs() == {"country"}
