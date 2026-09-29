from typing import Protocol

from agent_core.domain.base import Model
from agent_core.domain.entities import Agent
from agent_core.domain.identity import OnBehalfOf, Principal, SubjectRef


class AuthzDecision(Model):
    """Decisión de autorización: permitida o no, con motivo (M0 §2.9)."""
    allowed: bool
    reason: str | None = None


class AuthzPort(Protocol):
    """Puerto de autorización de agentes, sujetos y campos (M0 §2.9)."""
    def authorize_agent(self, principal: Principal, agent: Agent,
                        subject: SubjectRef | None) -> AuthzDecision: ...

    def authorize_subject(self, principal: Principal, obo: OnBehalfOf | None,
                          subject: SubjectRef | None) -> AuthzDecision: ...

    def bind_params(self, principal: Principal, obo: OnBehalfOf | None,
                    subject: SubjectRef | None) -> dict[str, str]: ...

    def can_read_field(self, reader: Principal, obo: OnBehalfOf | None, field: str, purpose: str) -> bool: ...

    def reportable_attrs(self) -> frozenset[str]: ...
