"""PII en claro en un texto de salida (M7 §3.7), para el check 4 de M8."""

from decimal import Decimal

from tests.m07.helpers import make_service

FACTS = {
    "cliente": {"document_number": "1023456789", "first_name": "Ana María", "email": "ana@example.test",
                "mobile_phone": "+573001234567"},
    "transaccion": {"transaction_id": "tx-demo-1", "amount": Decimal("500.00"), "currency": "COP"},
}


def test_clean_text() -> None:
    text = "Tu cargo de 500.00 COP (⟦tx:1⟧) está en revisión, ⟦name:1⟧."
    assert make_service().find_clear_pii(text, FACTS) == []


def test_document_in_clear_even_with_separators() -> None:
    service = make_service()
    assert service.find_clear_pii("Tu documento 1023456789", FACTS) == ["cliente.document_number"]
    assert service.find_clear_pii("Tu documento 1.023.456.789", FACTS) == ["cliente.document_number"]


def test_phone_without_country_code() -> None:
    assert make_service().find_clear_pii("te llamamos al 300 123 4567", FACTS) == ["cliente.mobile_phone"]


def test_name_case_insensitive_with_word_boundaries() -> None:
    service = make_service()
    assert service.find_clear_pii("hola ana maría", FACTS) == ["cliente.first_name"]
    assert service.find_clear_pii("hola Ana Marías", FACTS) == []


def test_email_pattern_and_email_fact() -> None:
    service = make_service()
    assert service.find_clear_pii("escribe a otra@example.test", FACTS) == ["pattern:email"]
    assert service.find_clear_pii("escribe a ana@example.test", FACTS) == ["cliente.email", "pattern:email"]


def test_tokens_are_ignored() -> None:
    assert make_service().find_clear_pii("⟦doc:1⟧ ⟦email:1⟧", FACTS) == []


def test_unclassified_fact_counts_as_pii() -> None:
    facts = {"compra": {"merchant": "Tienda Sintética"}}
    assert make_service().find_clear_pii("compraste en tienda sintética", facts) == ["compra.merchant"]


def test_result_never_contains_values() -> None:
    found = make_service().find_clear_pii("1023456789 ana@example.test Ana María", FACTS)
    assert found
    assert all(value not in item for item in found for value in ("1023456789", "ana@example.test", "Ana"))


def test_dotted_key_is_classified_like_project() -> None:
    facts = {"cliente": {"document.number": "1023456789"}}
    assert make_service().find_clear_pii("doc 1023456789", facts) == ["cliente.document_number"]
