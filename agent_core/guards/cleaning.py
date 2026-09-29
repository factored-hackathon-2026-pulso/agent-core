"""Limpieza del texto antes de detectar el idioma (M6 §3.1.1): fuera dígitos, montos, URLs, emojis,
tokens de PII (formato de M7) y etiquetas `<datos_no_confiables>`. Quedan letras, marcas, puntuación y
espacios."""

import re
import unicodedata

# M6 no importa M7 (contrato `guards` de .importlinter): el formato del token de M7 §3.2 se duplica aquí
# y `tests/m06/test_cleaning.py::test_token_pattern_copy_matches_m7` verifica que no diverja.
TOKEN_PATTERN_COPY = r"⟦([a-z]{1,12}):([1-9][0-9]*)⟧"

_TOKEN = re.compile(TOKEN_PATTERN_COPY)
_UNTRUSTED_TAG = re.compile(r"</?datos_no_confiables\b[^>]*>", re.IGNORECASE)
_URL = re.compile(r"(?:https?://|www\.)\S+", re.IGNORECASE)
_EMAIL = re.compile(r"\S+@\S+\.\S+")
_AMOUNT = re.compile(r"[$€£]\s*\d[\d.,\s]*|\b\d[\d.,]*\s*[$€£]")
_CURRENCY_CODE = re.compile(r"\b(?:COP|MXN|ARS|USD|EUR|BRL)\b")
_KEEP_CATEGORIES = ("L", "M", "P", "Z")


def clean_for_language(text: str) -> str:
    """Devuelve el texto sin ruido, con espacios colapsados. Puede quedar vacío."""
    cleaned = unicodedata.normalize("NFC", text)
    for pattern in (_UNTRUSTED_TAG, _TOKEN, _URL, _EMAIL, _AMOUNT, _CURRENCY_CODE):
        cleaned = pattern.sub(" ", cleaned)
    kept = "".join(
        ch if unicodedata.category(ch).startswith(_KEEP_CATEGORIES) or ch.isspace() else " "
        for ch in cleaned
    )
    return " ".join(kept.split())


def count_letters(cleaned: str) -> int:
    """Cuenta las letras en el texto limpio."""
    return sum(1 for ch in cleaned if ch.isalpha())
