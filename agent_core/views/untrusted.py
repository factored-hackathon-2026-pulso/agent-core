"""Envoltura de `untrusted_text` para la vista `model` (M7 §3.2, ADR 0008 R6)."""

import html
import re
import unicodedata

from agent_core.views.detector import detect
from agent_core.views.tokens import neutralize
from agent_core.views.vault import TokenVault

TAG = "datos_no_confiables"
_FAKE_TAG_RE = re.compile(r"<(\s*/?\s*datos_no_confiables)", re.IGNORECASE)


def escape_tags(text: str) -> str:
    """Toda apertura o cierre de la etiqueta dentro del texto pasa a `&lt;…`."""
    return _FAKE_TAG_RE.sub(r"&lt;\1", text)


def tokenize_free_text(text: str, vault: TokenVault) -> str:
    """NFKC, neutraliza `⟦`/`⟧`, escapa etiquetas falsas y cambia la PII detectada por tokens del vault."""
    # NFKC primero: convierte dígitos y signos de ancho completo antes de escapar y detectar.
    clean = escape_tags(neutralize(unicodedata.normalize("NFKC", text)))
    parts: list[str] = []
    cursor = 0
    for hit in detect(clean):
        parts.append(clean[cursor:hit.start])
        parts.append(vault.tokenize(hit.value, hit.field, hit.tag))
        cursor = hit.end
    parts.append(clean[cursor:])
    return "".join(parts)


def wrap_untrusted(text: str, source: str, vault: TokenVault) -> str:
    fuente = html.escape(neutralize(source), quote=True)
    return f'<{TAG} fuente="{fuente}">{tokenize_free_text(text, vault)}</{TAG}>'
