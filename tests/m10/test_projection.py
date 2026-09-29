"""Proyección del paquete: `audit` para lo persistido, vista del lector para `get`."""

from decimal import Decimal

from agent_core.handoff.projection import Projector
from testing.builders import advisor_with_delegation, principal
from testing.fakes.ids import FakeIds
from testing.fakes.keys import FakeKeyProvider
from tests.m10.helpers import HandoffAuthz, make_views

FULL = {"document_number": "1023456789", "first_name": "Ana", "amount": Decimal("120.50")}


def _projector(authz: HandoffAuthz | None = None) -> Projector:
    keys = FakeKeyProvider.default()
    return Projector(make_views(authz, keys), keys, FakeIds())


def test_audit_masks_pii_and_keeps_financial_values() -> None:
    out = _projector().audit("run-0001", FULL, "cliente")
    assert out == {"document_number": "***6789", "first_name": "***", "amount": Decimal("120.50")}


def test_unclassified_scalar_is_masked() -> None:
    assert _projector().audit("run-0001", "1023456789", "documento") == "***"


def test_for_reader_shows_only_granted_fields() -> None:
    advisor, obo = advisor_with_delegation()
    projector = _projector(HandoffAuthz({("adv-7", "document_number")}))
    out = projector.for_reader("run-0001", FULL, "cliente", advisor, obo)
    assert out == {"document_number": "1023456789", "first_name": "***", "amount": Decimal("120.50")}


def test_for_reader_without_grants_equals_the_masked_view() -> None:
    out = _projector().for_reader("run-0001", FULL, "cliente", principal(), None)
    assert out == {"document_number": "***6789", "first_name": "***", "amount": Decimal("120.50")}


def test_for_reader_handles_nested_and_scalar_values() -> None:
    advisor, obo = advisor_with_delegation()
    projector = _projector(HandoffAuthz({("adv-7", "email")}))
    nested = {"contacts": [{"email": "ana@example.test"}, {"email": "beto@example.test"}], "note": None}
    assert projector.for_reader("run-0001", nested, "cliente", advisor, obo) == nested
