"""TokenVault en memoria (M7 §3.4). T-M7-03."""

import unicodedata

import pytest

from agent_core.views.vault import TokenVault
from testing.fakes.ids import FakeIds
from testing.fakes.keys import FakeKeyProvider


def _vault() -> TokenVault:
    return TokenVault("run-0001", FakeKeyProvider.default(), FakeIds())


def test_t_m7_03_same_value_same_token_distinct_values_distinct_tokens() -> None:
    """T-M7-03: el mismo valor da el mismo token en todo el run; valores distintos, tokens distintos."""
    vault = _vault()
    first = vault.tokenize("1023456789", "document_number", "doc")
    assert first == "⟦doc:1⟧"
    assert vault.tokenize("1023456789", "document_number", "doc") == first
    assert vault.tokenize("1098765432", "document_number", "doc") == "⟦doc:2⟧"
    assert vault.tokenize("3001234567", "mobile_phone", "tel") == "⟦tel:1⟧"
    assert len(vault) == 3


def test_same_value_in_other_field_gets_other_token() -> None:
    vault = _vault()
    assert vault.tokenize("Ana", "first_name", "name") != vault.tokenize("Ana", "last_name", "name")


def test_nfc_equivalent_values_share_token() -> None:
    vault = _vault()
    nfc = unicodedata.normalize("NFC", "José")
    nfd = unicodedata.normalize("NFD", "José")
    assert nfc != nfd
    assert vault.tokenize(nfc, "first_name", "name") == vault.tokenize(nfd, "first_name", "name")


def test_resolve_exists_lookup() -> None:
    vault = _vault()
    token = vault.tokenize("1023456789", "document_number", "doc")
    assert vault.resolve(token) == "1023456789"
    assert vault.exists(token)
    assert vault.resolve("⟦doc:9⟧") is None
    assert not vault.exists("⟦doc:9⟧")
    entry = vault.lookup(token)
    assert entry is not None
    expected = (token, "doc", "document_number", "1023456789")
    assert (entry.token, entry.tag, entry.field, entry.value) == expected
    assert vault.lookup("⟦doc:9⟧") is None


def test_repr_hides_values() -> None:
    vault = _vault()
    token = vault.tokenize("1023456789", "document_number", "doc")
    assert "1023456789" not in repr(vault)
    assert "1023456789" not in repr(vault.lookup(token))


def test_rejects_bad_tag() -> None:
    with pytest.raises(ValueError):
        _vault().tokenize("x", "field", "BAD")
