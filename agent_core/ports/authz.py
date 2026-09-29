from typing import Protocol

from agent_core.domain.base import Model
from agent_core.domain.entities import Agent
from agent_core.domain.identity import OnBehalfOf, Principal, SubjectRef


class AuthzDecision(Model):
    allowed: bool
    reason: str | None = None


class AuthzPort(Protocol):
    def authorize_agent(self, principal: Principal, agent: Agent,
                        subject: SubjectRef | None) -> AuthzDecision: ...

    def authorize_subject(self, principal: Principal, obo: OnBehalfOf | None,
                          subject: SubjectRef | None) -> AuthzDecision: ...

    def bind_params(self, principal: Principal, obo: OnBehalfOf | None,
                    subject: SubjectRef | None) -> dict[str, str]: ...

    def can_read_field(self, reader: Principal, obo: OnBehalfOf | None, field: str, purpose: str) -> bool: ...

    def reportable_attrs(self) -> frozenset[str]: ...
