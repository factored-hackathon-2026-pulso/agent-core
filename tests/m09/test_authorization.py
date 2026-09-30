"""M9 F3: `RunAuthorizer` — agente, subject, versión y derivación del subject (spec §3.2, §3.3; ADR 0006)."""

from typing import Any

import pytest

from agent_core.api.authorization import RunAuthorizer
from agent_core.api.gate import Admitted
from agent_core.domain import (
    Agent,
    AgentSelector,
    EngineError,
    OnBehalfOf,
    Principal,
    ProblemCode,
    Release,
    RunInput,
    RunState,
    SubjectRef,
)
from testing.builders import advisor_with_delegation, principal, run_state
from testing.fakes.authz import TableAuthz
from testing.fakes.clock import FakeClock
from testing.fakes.ids import FakeIds
from testing.fakes.registry import InMemoryRegistry
from tests.m04.harness import agent_data
from tests.m09.helpers import RecordingDenials, RecordingSecurityLog, anonymous

CUSTOMER = SubjectRef(kind="customer", ref="cust-001")


class World:
    def __init__(self, **agent_over: Any) -> None:
        self.registry = InMemoryRegistry()
        self.denials = RecordingDenials()
        self.security = RecordingSecurityLog()
        self.add_agent("atencion", **agent_over)
        self.authorizer = RunAuthorizer(
            TableAuthz(), self.registry, FakeClock(), FakeIds(), self.denials, self.security
        )

    def add_agent(self, agent_id: str, **over: Any) -> Agent:
        agent = Agent.model_validate(agent_data(agent_id, **over))
        self.registry.add(agent)
        release = Release.model_validate(
            {
                "id": f"rel-{agent_id}",
                "status": "active",
                "entities": {"agent": {agent_id: "1.0.0"}},
                "language_detection": "lang@1.0.0",
            }
        )
        self.registry.add_release(release, agent_id, alias="prod")
        self.registry.add_release(release, agent_id, alias="canary")
        self.registry.add_release(release, agent_id, alias=None, version="1.0.0")
        return agent

    def new_run(
        self,
        who: Principal,
        obo: OnBehalfOf | None = None,
        *,
        subject: SubjectRef | None = None,
        agent: str = "atencion",
    ) -> RunInput:
        body = RunInput(agent=AgentSelector.model_validate(agent), subject=subject, idempotency_key="k-1")
        return self.authorizer.authorize_new_run(Admitted(who, obo), body, trace_id="t-1")

    def denied_new(self, who: Principal, obo: OnBehalfOf | None = None, **kw: Any) -> EngineError:
        with pytest.raises(EngineError) as info:
            self.new_run(who, obo, **kw)
        return info.value

    def existing(self, who: Principal, obo: OnBehalfOf | None, run: RunState) -> None:
        self.authorizer.authorize_existing_run(Admitted(who, obo), run, trace_id="t-1")


# --- subject derivado y IDOR ---------------------------------------------------------------------------


def test_customer_subject_is_derived_from_the_principal() -> None:
    w = World()
    assert w.new_run(principal()).subject == CUSTOMER


def test_customer_body_subject_of_someone_else_is_403_subject_forbidden() -> None:  # T-M9-01 / T-M9-03
    w = World()
    other = SubjectRef(kind="customer", ref="cust-999")
    err = w.denied_new(principal(), subject=other)
    assert err.code is ProblemCode.subject_forbidden and err.status == 403
    assert w.security.entries[-1]["reason"] == "subject_forbidden"


def test_customer_body_subject_equal_to_its_own_is_accepted() -> None:
    w = World()
    assert w.new_run(principal(), subject=CUSTOMER).subject == CUSTOMER


def test_agent_without_subject_kinds_gets_no_subject() -> None:
    w = World(subject_kinds=[])
    assert w.new_run(principal()).subject is None


def test_advisor_subject_comes_from_the_delegation() -> None:
    w = World(invocable_by=["advisor"])
    advisor, obo = advisor_with_delegation()
    assert w.new_run(advisor, obo).subject == obo.subject


def test_advisor_without_delegation_is_403_subject_forbidden() -> None:  # T-M9-02
    w = World(invocable_by=["advisor"])
    advisor, _ = advisor_with_delegation()
    assert w.denied_new(advisor, None).code is ProblemCode.subject_forbidden
    assert w.denied_new(advisor, None, subject=CUSTOMER).code is ProblemCode.subject_forbidden


def test_advisor_cannot_pick_another_subject_than_the_delegated() -> None:
    w = World(invocable_by=["advisor"])
    advisor, obo = advisor_with_delegation()
    other = SubjectRef(kind="customer", ref="cust-999")
    assert w.denied_new(advisor, obo, subject=other).code is ProblemCode.subject_forbidden


def test_anonymous_gets_no_personal_subject() -> None:  # T-M9-05
    w = World(min_auth_level="anonymous", subject_kinds=[])
    assert w.new_run(anonymous()).subject is None
    assert w.denied_new(anonymous(), subject=CUSTOMER).code is ProblemCode.subject_forbidden


def test_anonymous_cannot_use_an_agent_that_needs_personal_data() -> None:  # T-M9-05
    w = World(min_auth_level="anonymous")  # subject_kinds=["customer"]
    err = w.denied_new(anonymous())
    assert err.code is ProblemCode.agent_forbidden


def test_service_subject_by_scope() -> None:
    w = World(invocable_by=["service"])
    svc = principal(type="service", id="svc-1", attrs={}, scopes=["subject:customer"])
    assert w.new_run(svc, subject=CUSTOMER).subject == CUSTOMER
    bare = principal(type="service", id="svc-2", attrs={}, scopes=[])
    assert w.denied_new(bare, subject=CUSTOMER).code is ProblemCode.subject_forbidden


# --- agente --------------------------------------------------------------------------------------------


def test_principal_type_not_invocable_is_agent_forbidden() -> None:
    w = World(invocable_by=["advisor"])
    err = w.denied_new(principal())
    assert err.code is ProblemCode.agent_forbidden and err.detail == "principal_type"


def test_insufficient_auth_level_is_agent_forbidden() -> None:
    w = World(min_auth_level="step_up")
    assert w.denied_new(principal()).code is ProblemCode.agent_forbidden


def test_unknown_agent_is_404() -> None:
    w = World()
    err = w.denied_new(principal(), agent="no-existe")
    assert err.code is ProblemCode.not_found and err.status == 404


# --- versión -------------------------------------------------------------------------------------------


@pytest.mark.parametrize("selector", ["atencion@1.0.0", "atencion@canary"])
@pytest.mark.parametrize("who", ["customer", "advisor"])
def test_customer_and_advisor_cannot_pin_a_version(who: str, selector: str) -> None:  # T-M9-04
    w = World(invocable_by=["customer", "advisor"])
    caller = principal() if who == "customer" else advisor_with_delegation()[0]
    obo = None if who == "customer" else advisor_with_delegation()[1]
    err = w.denied_new(caller, obo, agent=selector)
    assert err.code is ProblemCode.version_pin_forbidden and err.status == 403


def test_prod_alias_is_always_fine() -> None:
    w = World()
    w.new_run(principal(), agent="atencion@prod")


@pytest.mark.parametrize("who", ["service", "builder"])
def test_service_and_builder_can_pin(who: str) -> None:
    w = World(invocable_by=[who], subject_kinds=[])
    caller = principal(type=who, id=f"{who}-1", attrs={}, scopes=[])
    assert w.new_run(caller, agent="atencion@1.0.0").agent.version == "1.0.0"
    assert w.new_run(caller, agent="atencion@canary").agent.alias == "canary"


# --- runs existentes (cada turno) ----------------------------------------------------------------------


def test_existing_run_is_authorized_again_each_turn() -> None:
    w = World()
    w.existing(principal(), None, run_state())


def test_existing_run_with_a_foreign_subject_is_denied_and_recorded() -> None:  # T-M9-01
    w = World()
    run = run_state(subject={"kind": "customer", "ref": "cust-999"})
    with pytest.raises(EngineError) as info:
        w.existing(principal(), None, run)
    assert info.value.code is ProblemCode.subject_forbidden
    ((run_id, events),) = w.denials.calls
    assert run_id == run.run_id and events[0].payload.reason == "subject_forbidden"


def test_existing_run_whose_agent_is_no_longer_allowed_is_agent_forbidden_and_recorded() -> None:
    w = World(invocable_by=["advisor"])
    run = run_state()
    with pytest.raises(EngineError) as info:
        w.existing(principal(), None, run)
    assert info.value.code is ProblemCode.agent_forbidden
    assert w.denials.calls[0][1][0].payload.reason == "agent_forbidden"


def test_existing_advisor_run_needs_the_delegation_on_every_turn() -> None:
    w = World(invocable_by=["advisor"])
    advisor, obo = advisor_with_delegation()
    run = run_state(principal=advisor, on_behalf_of=obo)
    w.existing(advisor, obo, run)
    with pytest.raises(EngineError) as info:
        w.existing(advisor, None, run)
    assert info.value.code is ProblemCode.subject_forbidden
