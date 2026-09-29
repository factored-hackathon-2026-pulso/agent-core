"""Formato del token y máscaras de `audit` (M7 §3.2, §3.3)."""

import pytest

from agent_core.views.tokens import MASK, TOKEN_RE, format_token, mask, neutralize


def test_format_and_regex() -> None:
    assert format_token("doc", 1) == "⟦doc:1⟧"
    assert TOKEN_RE.fullmatch("⟦tx:3⟧")
    assert TOKEN_RE.fullmatch("⟦doc:0⟧") is None
    assert TOKEN_RE.fullmatch("⟦Doc:1⟧") is None
    assert TOKEN_RE.fullmatch("[doc:1]") is None


def test_format_rejects_bad_tag_or_number() -> None:
    with pytest.raises(ValueError):
        format_token("DOC", 1)
    with pytest.raises(ValueError):
        format_token("doc", 0)


def test_neutralize_breaks_forged_tokens() -> None:
    assert neutralize("x ⟦doc:1⟧ y") == "x ⟪doc:1⟫ y"
    assert TOKEN_RE.search(neutralize("⟦doc:1⟧")) is None


def test_mask() -> None:
    assert mask("1023456789", "doc") == "***6789"
    assert mask("1234567", "doc") == "***67"
    assert mask("12345", "doc") == MASK
    assert mask("+573001234567", "tel") == "***4567"
    assert mask("Ana", "name") == MASK
    assert mask("ana@example.test", "email") == MASK
    assert mask("tx-demo-1", "tx") == MASK
