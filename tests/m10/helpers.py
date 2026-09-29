"""Ayudantes de las pruebas de M10. Solo datos sintéticos."""

from collections.abc import Mapping

from agent_core.domain import Agent, OnBehalfOf, Principal, PrincipalType, SubjectRef
from agent_core.ports import AuthzDecision
from agent_core.views import DEFAULT_CATALOG, FieldClassifier, FieldRule, ViewService
from testing.fakes.clock import FakeClock
from testing.fakes.keys import FakeKeyProvider

# Catálogo sintético: lo que en producción publica la unidad 3 como FieldClassification.
CATALOG: Mapping[str, FieldRule] = {
    **DEFAULT_CATALOG,
    "amount": FieldRule(field_class="financial"),
    "currency": FieldRule(field_class="public"),
    "monto": FieldRule(field_class="financial"),
    "transaction_id": FieldRule(field_class="pii_direct", tag="tx"),
}


class HandoffAuthz:
    """Doble mínimo de `AuthzPort` para M10 (la `TableAuthz` real llega con M9).

    - `authorize_subject`: asesor con delegación a su nombre sobre ese subject; `service` con el scope
      `handoff:read`; nadie más.
    - `can_read_field`: solo para el propósito `handoff` y si el par `(lector, campo)` está concedido; el
      asesor además necesita su delegación."""

    def __init__(self, field_grants: set[tuple[str, str]] | None = None,
                 reportable: frozenset[str] = frozenset({"country"})) -> None:
        self._grants = field_grants or set()
        self._reportable = reportable

    def authorize_subject(self, principal: Principal, obo: OnBehalfOf | None,
                          subject: SubjectRef | None) -> AuthzDecision:
        if principal.type is PrincipalType.service:
            allowed = "handoff:read" in principal.scopes
        elif principal.type is PrincipalType.advisor:
            allowed = (obo is not None and obo.grantee == principal.key and subject is not None
                       and obo.subject == subject)
        else:
            allowed = False
        return AuthzDecision(allowed=allowed, reason=None if allowed else "sin_delegacion")

    def can_read_field(self, reader: Principal, obo: OnBehalfOf | None, field: str, purpose: str) -> bool:
        if purpose != "handoff" or reader.id is None:
            return False
        if reader.type is PrincipalType.advisor and (obo is None or obo.grantee.id != reader.id):
            return False
        return (reader.id, field) in self._grants

    def reportable_attrs(self) -> frozenset[str]:
        return self._reportable

    def authorize_agent(self, principal: Principal, agent: Agent,
                        subject: SubjectRef | None) -> AuthzDecision:
        raise NotImplementedError

    def bind_params(self, principal: Principal, obo: OnBehalfOf | None,
                    subject: SubjectRef | None) -> dict[str, str]:
        raise NotImplementedError


def make_views(authz: HandoffAuthz | None = None, keys: FakeKeyProvider | None = None) -> ViewService:
    return ViewService(keys or FakeKeyProvider.default(), authz or HandoffAuthz(), FakeClock(),
                       FieldClassifier(CATALOG))
