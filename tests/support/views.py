"""Ayuda compartida entre módulos: vistas sintéticas de M7 sin importar las pruebas de otro módulo.

Solo datos sintéticos. Quien necesite una variante distinta usa su propio helper."""

from collections.abc import Mapping
from decimal import Decimal

from agent_core.domain import Agent, JsonValue, OnBehalfOf, Principal, SubjectRef
from agent_core.ports import AuthzDecision
from agent_core.views import TokenVault, ViewService
from agent_core.views.classification import DEFAULT_CATALOG, FieldClassifier, FieldRule
from testing.fakes.clock import FakeClock
from testing.fakes.ids import FakeIds
from testing.fakes.keys import FakeKeyProvider

_CATALOG: Mapping[str, FieldRule] = {
    **DEFAULT_CATALOG,
    "amount": FieldRule(field_class="financial"),
    "currency": FieldRule(field_class="public"),
}


class _DenyAllFields:
    """Doble mínimo de `AuthzPort` para proyectar: nadie lee campos protegidos."""

    def can_read_field(self, reader: Principal, obo: OnBehalfOf | None, field: str, purpose: str) -> bool:
        return False

    def authorize_agent(self, principal: Principal, agent: Agent,
                        subject: SubjectRef | None) -> AuthzDecision:
        raise NotImplementedError

    def authorize_subject(self, principal: Principal, obo: OnBehalfOf | None,
                          subject: SubjectRef | None) -> AuthzDecision:
        raise NotImplementedError

    def bind_params(self, principal: Principal, obo: OnBehalfOf | None,
                    subject: SubjectRef | None) -> dict[str, str]:
        raise NotImplementedError

    def reportable_attrs(self) -> frozenset[str]:
        raise NotImplementedError


def synthetic_views(row: Mapping[str, str | Decimal], table: str = "transactions"
                    ) -> tuple[JsonValue, JsonValue]:
    """`(model, full)` de una fila sintética: la vista `model` lleva tokens y la `full` los valores."""
    keys = FakeKeyProvider.default()
    service = ViewService(keys, _DenyAllFields(), FakeClock(), FieldClassifier(_CATALOG))
    vault = TokenVault("run-0001", keys, FakeIds())
    projected = service.project([dict(row)], table, [], vault)
    return projected.model, projected.full
