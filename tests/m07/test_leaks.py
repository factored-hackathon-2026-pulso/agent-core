"""Fugas hacia proveedores externos (M7 §4, §8). T-M7-01."""

from decimal import Decimal

from hypothesis import given, settings
from hypothesis import strategies as st

from testing.capture import RequestCapture
from testing.fakes.keys import FakeKeyProvider
from tests.m07.helpers import make_service, make_vault

PII_FIELDS = ("document_number", "first_name", "email", "mobile_phone", "merchant")

documents = st.integers(min_value=10**9, max_value=10**10 - 1).map(str)
names = st.from_regex(r"[A-Z][a-z]{3,8}Sint", fullmatch=True)
emails = st.from_regex(r"[a-z]{3,8}\.[a-z]{3,8}@example\.test", fullmatch=True)
phones = st.integers(min_value=3_000_000_000, max_value=3_509_999_999).map(str)
rows = st.fixed_dictionaries({
    "document_number": documents, "first_name": names, "email": emails, "mobile_phone": phones,
    "merchant": names, "amount": st.decimals(min_value=0, max_value=10**6, places=2),
    "currency": st.sampled_from(["COP", "MXN", "ARS"]),
})


def test_capture_reports_leaks() -> None:
    capture = RequestCapture()
    capture.record({"inputs": {"document_number": "1023456789"}})
    capture.record("sin datos")
    assert capture.leaks(["nada", "1023456789"]) == [(0, 1)]


def test_capture_skips_empty_values() -> None:
    capture = RequestCapture()
    capture.record("cualquier request")
    assert capture.leaks(["", "   ", "\t "]) == []


def test_capture_leaks_never_return_the_value() -> None:
    capture = RequestCapture()
    capture.record("secreto-sintetico-123")
    found = capture.leaks(["secreto-sintetico-123", "secreto-sintetico-123"])
    assert found == [(0, 0), (0, 1)]
    assert "secreto" not in repr(found)


@settings(max_examples=60, deadline=None)
@given(batch=st.lists(rows, min_size=1, max_size=5), complaint_doc=documents, complaint_email=emails)
def test_t_m7_01_no_clear_pii_in_captured_requests(
    batch: list[dict[str, object]], complaint_doc: str, complaint_email: str
) -> None:
    """T-M7-01: ningún request capturado hacia JEV o el LLM contiene pii_direct en claro (y audit tampoco)."""
    keys = FakeKeyProvider.default()
    service, vault = make_service(keys=keys), make_vault(keys=keys)
    complaint = f"Reclamo: mi documento {complaint_doc} y mi correo {complaint_email}"
    transactions = service.project(batch, "transactions", [], vault)  # type: ignore[arg-type]
    pqr = service.project({"description": complaint}, "complaints", [], vault)

    outbound = RequestCapture()
    outbound.record({"gateway": "llm", "inputs": {"transacciones": transactions.model, "pqr": pqr.model}})
    outbound.record({"provider": "jev", "inputs": transactions.model})
    clear = [str(row[field]) for row in batch for field in PII_FIELDS] + [complaint_doc, complaint_email]
    assert outbound.leaks(clear) == []  # posiciones, nunca valores

    audit = RequestCapture()
    audit.record(transactions.audit)
    audit.record(pqr.audit)
    assert audit.leaks(clear) == []
    audit_rows = transactions.audit
    assert isinstance(audit_rows, list)
    assert all(isinstance(row, dict) and isinstance(row["amount"], Decimal) for row in audit_rows)
