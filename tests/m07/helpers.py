"""Ayudantes de las pruebas de M7. Solo datos sintéticos."""

from collections.abc import Mapping

from agent_core.domain import Agent, OnBehalfOf, Principal, PrincipalType, SubjectRef
from agent_core.ports import AuthzDecision
from agent_core.views.classification import DEFAULT_CATALOG, FieldClassifier, FieldRule, QuasiRule
from agent_core.views.service import ViewService
from agent_core.views.vault import TokenVault
from testing.fakes.clock import FakeClock
from testing.fakes.ids import FakeIds
from testing.fakes.keys import FakeKeyProvider

# Catálogo sintético: lo que en producción publica la unidad 3 como FieldClassification.
CATALOG: Mapping[str, FieldRule] = {
    **DEFAULT_CATALOG,
    "amount": FieldRule(field_class="financial"),
    "currency": FieldRule(field_class="public"),
    "status": FieldRule(field_class="public"),
    "transaction_date": FieldRule(field_class="public"),
    "transaction_id": FieldRule(field_class="pii_direct", tag="tx"),
    "date_of_birth": FieldRule(field_class="pii_quasi", quasi=QuasiRule(op="age_bucket")),
}


class FieldAuthz:
    """Doble mínimo de AuthzPort para M7: solo `can_read_field` (TableAuthz llega con M9).

    Un asesor solo lee con una delegación vigente a su nombre."""

    def __init__(self, grants: set[tuple[str, str, str]]) -> None:
        self._grants = grants
        self.calls: list[tuple[str | None, str, str]] = []

    def can_read_field(self, reader: Principal, obo: OnBehalfOf | None, field: str, purpose: str) -> bool:
        self.calls.append((reader.id, field, purpose))
        if reader.id is None:
            return False
        if reader.type is PrincipalType.advisor and (obo is None or obo.grantee.id != reader.id):
            return False
        return (reader.id, field, purpose) in self._grants

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


def make_service(*, keys: FakeKeyProvider | None = None, authz: FieldAuthz | None = None,
                 catalog: Mapping[str, FieldRule] = CATALOG) -> ViewService:
    return ViewService(keys or FakeKeyProvider.default(), authz or FieldAuthz(set()), FakeClock(),
                       FieldClassifier(catalog))


def make_vault(*, keys: FakeKeyProvider | None = None, ids: FakeIds | None = None) -> TokenVault:
    return TokenVault("run-0001", keys or FakeKeyProvider.default(), ids or FakeIds())
