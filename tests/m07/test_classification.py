"""Clasificación de campos (M7 §3.1). T-M7-09 (parte de clasificación)."""

import pytest
from pydantic import ValidationError

from agent_core.views.classification import (
    DEFAULT_CATALOG,
    FieldClassifier,
    FieldRule,
    QuasiRule,
    field_name,
)


def test_default_catalog_is_exactly_the_general_spec() -> None:
    by_class: dict[str, set[str]] = {}
    for path, rule in DEFAULT_CATALOG.items():
        by_class.setdefault(rule.field_class, set()).add(path)
    assert by_class == {
        "pii_direct": {"first_name", "last_name", "document_number", "email", "mobile_phone",
                       "landline_phone", "address", "product_number", "ip_address"},
        "pii_quasi": {"date_of_birth", "postal_code", "latitude", "longitude"},
        "untrusted_text": {"complaints.description", "complaints.resolution", "call_transcripts.full_text",
                           "call_transcripts.customer_text", "call_transcripts.agent_text",
                           "satisfaction_surveys.open_comments"},
    }


def test_default_tags() -> None:
    tags = {path: rule.tag for path, rule in DEFAULT_CATALOG.items() if rule.field_class == "pii_direct"}
    assert tags == {
        "first_name": "name", "last_name": "name", "document_number": "doc", "email": "email",
        "mobile_phone": "tel", "landline_phone": "tel", "address": "addr", "product_number": "prod",
        "ip_address": "ip",
    }


def test_default_quasi_rule_is_drop() -> None:
    quasi = [rule for rule in DEFAULT_CATALOG.values() if rule.field_class == "pii_quasi"]
    assert quasi and all(rule.quasi == QuasiRule(op="drop") for rule in quasi)


def test_t_m7_09_unclassified_is_pii_direct() -> None:
    """T-M7-09: un campo sin clasificar se trata como pii_direct con el tag genérico."""
    classifier = FieldClassifier()
    assert classifier.lookup("transactions.merchant") is None
    assert classifier.classify("transactions.merchant") == "pii_direct"
    assert classifier.rule("transactions.merchant").tag == "pii"


def test_exact_path_wins_then_field_name() -> None:
    classifier = FieldClassifier({
        "email": FieldRule(field_class="pii_direct", tag="email"),
        "audit_log.email": FieldRule(field_class="public"),
    })
    assert classifier.classify("customers.email") == "pii_direct"
    assert classifier.classify("audit_log.email") == "public"
    assert classifier.classify("email") == "pii_direct"


def test_untrusted_default_only_matches_its_table() -> None:
    classifier = FieldClassifier()
    assert classifier.classify("complaints.description") == "untrusted_text"
    assert classifier.lookup("products.description") is None


def test_catalog_override_extends_default() -> None:
    classifier = FieldClassifier({**DEFAULT_CATALOG, "amount": FieldRule(field_class="financial")})
    assert classifier.classify("transactions.amount") == "financial"
    assert classifier.classify("customers.document_number") == "pii_direct"


def test_catalog_is_copied() -> None:
    catalog = {"amount": FieldRule(field_class="financial")}
    classifier = FieldClassifier(catalog)
    catalog["amount"] = FieldRule(field_class="public")
    assert classifier.classify("amount") == "financial"


def test_field_rule_validation() -> None:
    with pytest.raises(ValidationError):
        FieldRule(field_class="pii_direct", tag="Doc")
    with pytest.raises(ValidationError):
        FieldRule(field_class="pii_direct", tag="x" * 13)
    with pytest.raises(ValidationError):
        FieldRule(field_class="public", quasi=QuasiRule(op="age_bucket"))
    with pytest.raises(ValidationError):
        QuasiRule(op="age_bucket", width=0)
    rule = FieldRule(field_class="pii_quasi", quasi=QuasiRule(op="age_bucket", width=5))
    assert rule.quasi.width == 5


def test_field_name() -> None:
    assert field_name("customers.document_number") == "document_number"
    assert field_name("customers.address.city") == "city"
    assert field_name("email") == "email"
