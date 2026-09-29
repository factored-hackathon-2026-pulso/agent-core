"""Formato de los tokens de PII y máscaras (M7 §3.2, §3.3). Regex única para M6 y M8."""

import re

TAG_PATTERN = r"[a-z]{1,12}"
TOKEN_PATTERN = rf"⟦({TAG_PATTERN}):([1-9][0-9]*)⟧"
TOKEN_RE = re.compile(TOKEN_PATTERN)
MASK = "***"

_TAG_RE = re.compile(TAG_PATTERN)
# Los delimitadores que llegan dentro de los datos se sustituyen para que nadie falsifique un token.
_NEUTRAL = str.maketrans({"⟦": "⟪", "⟧": "⟫"})
# Solo los identificadores numéricos conservan sus últimos caracteres en `audit`.
_ID_TAGS = frozenset({"doc", "tel", "prod"})


def format_token(tag: str, n: int) -> str:
    if _TAG_RE.fullmatch(tag) is None or n < 1:
        raise ValueError("tag o número de token inválido")
    return f"⟦{tag}:{n}⟧"


def neutralize(text: str) -> str:
    return text.translate(_NEUTRAL)


def mask(value: str, tag: str) -> str:
    """`***` + últimos 4 (10+ caracteres) o 2 (6–9) solo para `doc`, `tel` y `prod`; el resto `***`."""
    compact = value.strip()
    if tag not in _ID_TAGS or len(compact) < 6:
        return MASK
    return MASK + compact[-4:] if len(compact) >= 10 else MASK + compact[-2:]
