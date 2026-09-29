"""Comprobaciones del validador de M8: cada una es `check_x(draft, ctx) -> list[Failure]` (spec §9)."""

import re
from collections.abc import Callable, Iterable

from agent_core.guards import detect_language
from agent_core.response.check_numbers import check_numbers
from agent_core.response.types import CheckId, Draft, Failure, ValidationContext
from agent_core.views import TOKEN_PATTERN

TOKEN_RE = re.compile(TOKEN_PATTERN)

Check = Callable[[Draft, ValidationContext], list[Failure]]


def check_format(draft: Draft, ctx: ValidationContext) -> list[Failure]:
    """Comprobación 1: invariantes del `Draft` (el parseo de la salida del gateway es `parse_draft`)."""
    if not draft.text.strip():
        return [Failure(check="format", detail="texto vacío")]
    return []


PII_DETAIL_PREFIX = "PII en claro"


def check_citations(draft: Draft, ctx: ValidationContext) -> list[Failure]:
    """Comprobación 2: cada cita existe (hechos o páginas) y está en `allowed`. Una falla por cita.

    El detalle lleva la posición de la cita (1-based), nunca su texto: lo escribe el modelo."""
    failures: list[Failure] = []
    for position, citation in enumerate(draft.citations, start=1):
        if citation not in ctx.facts_model_view and citation not in ctx.pages_model_view:
            failures.append(Failure(check="citations", detail=f"cita {position}: cita_inexistente"))
        elif citation not in ctx.allowed:
            failures.append(Failure(check="citations", detail=f"cita {position}: cita_no_permitida"))
    return failures


def check_tokens_pii(draft: Draft, ctx: ValidationContext) -> list[Failure]:
    """Comprobación 4: todo token existe en el vault y no hay PII en claro (solo rutas, nunca valores).

    El detalle de un token desconocido lleva su posición entre los tokens del texto, no el token."""
    failures = [
        Failure(check="tokens_pii", detail=f"token desconocido en la posición {position}")
        for position, token in enumerate(_unique(m.group(0) for m in TOKEN_RE.finditer(draft.text)), start=1)
        if not ctx.vault.exists(token)
    ]
    clear = ctx.find_clear_pii(draft.text)
    if clear:
        failures.append(Failure(check="tokens_pii", detail=f"{PII_DETAIL_PREFIX}: " + ", ".join(clear)))
    return failures


def _unique(items: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(items))


def check_language(draft: Draft, ctx: ValidationContext) -> list[Failure]:
    """Comprobación 5: el idioma de mayor puntaje es el `locale` (`short`/`undetermined` no rechazan)."""
    decision = detect_language(draft.text, ctx.lang_cfg, ctx.lang_thresholds, list(ctx.supported_locales),
                               ctx.locale)
    if decision.decision in ("short", "undetermined") or not decision.top2:
        return []
    detected = decision.top2[0][0]
    if detected == ctx.locale:
        return []
    return [Failure(check="language", detail=f"idioma esperado {ctx.locale}, detectado {detected}")]


CHECKS: tuple[tuple[CheckId, Check], ...] = (
    ("format", check_format),
    ("citations", check_citations),
    ("numbers", check_numbers),
    ("tokens_pii", check_tokens_pii),
    ("language", check_language),
)
