from typing import Protocol

from agent_core.domain.base import Model
from agent_core.domain.entities import Agent
from agent_core.domain.identity import OnBehalfOf, Principal, SubjectRef
from agent_core.domain.knowledge import KnowledgeView, Purpose


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

    def knowledge_view(self, principal: Principal, purpose: Purpose) -> KnowledgeView:
        """Qué páginas de conocimiento puede leer `principal` para `purpose` (M12, ADR 0015).

        Solo lo `public` y `approved` puede citarse al cliente, sea quien sea el que pregunta; `agent_only`
        solo se alcanza con `agent_guidance`; un cliente no tiene vista de asesor (audiencias vacías)."""
        ...

    def reportable_attrs(self) -> frozenset[str]: ...
