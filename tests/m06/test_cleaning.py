"""Limpieza previa a la detección de idioma (M6 §3.1.1). T-M6-07."""

import pytest

from agent_core.guards.cleaning import TOKEN_PATTERN_COPY, clean_for_language, count_letters


def test_t_m6_07_pii_tokens_and_amounts_are_ignored() -> None:
    """T-M6-07: la limpieza ignora tokens de PII y montos."""
    text = "Mi documento es ⟦doc:1⟧ y quiero disputar $1.500,00 COP con ⟦prod:12⟧"
    cleaned = clean_for_language(text)
    assert "⟦" not in cleaned and "doc" not in cleaned.split()
    assert "1.500" not in cleaned and "COP" not in cleaned and "$" not in cleaned
    assert cleaned == "Mi documento es y quiero disputar con"


def test_only_amount_is_empty() -> None:
    assert clean_for_language("$ 250.000") == ""
    assert count_letters(clean_for_language("250.000 COP")) == 0


def test_digits_urls_emails_and_emoji_are_removed() -> None:
    text = "Hola 😀 mira https://example.test/a?b=1 y escribe a ana@example.test 12345"
    assert clean_for_language(text) == "Hola mira y escribe a"


def test_untrusted_tags_are_removed_but_content_kept() -> None:
    text = '<datos_no_confiables fuente="complaints.description">no me cobraron</datos_no_confiables>'
    assert clean_for_language(text) == "no me cobraron"


def test_accents_are_preserved_and_nfd_is_normalized() -> None:
    decomposed = "cancelación".replace("ó", "ó")
    assert clean_for_language(decomposed) == "cancelación"
    assert count_letters("cancelación") == 11


@pytest.mark.parametrize(("text", "letters"), [("sí", 2), ("ok", 2), ("não", 3), ("", 0), ("12 34", 0)])
def test_count_letters(text: str, letters: int) -> None:
    assert count_letters(clean_for_language(text)) == letters


def test_token_pattern_copy_matches_m7() -> None:
    """La regex está duplicada (M6 no importa M7); esta prueba las ata."""
    from agent_core.views import TOKEN_PATTERN

    assert TOKEN_PATTERN_COPY == TOKEN_PATTERN
