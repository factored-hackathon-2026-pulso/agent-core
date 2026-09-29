"""Comprobaciones del validador de M8: cada una es `check_x(draft, ctx) -> list[Failure]` (spec §9)."""

import re
from collections.abc import Callable, Iterable

from agent_core.response.types import Draft, Failure, ValidationContext
from agent_core.views import TOKEN_PATTERN

TOKEN_RE = re.compile(TOKEN_PATTERN)

Check = Callable[[Draft, ValidationContext], list[Failure]]


def check_format(draft: Draft, ctx: ValidationContext) -> list[Failure]:
    """Comprobación 1: invariantes del `Draft` (el parseo de la salida del gateway es `parse_draft`)."""
    if not draft.text.strip():
        return [Failure(check="format", detail="texto vacío")]
    return []


def check_citations(draft: Draft, ctx: ValidationContext) -> list[Failure]:
    """Comprobación 2: cada cita existe (hechos o páginas) y está en `allowed`. Una falla por cita."""
    failures: list[Failure] = []
    for citation in draft.citations:
        if citation not in ctx.facts_model_view and citation not in ctx.pages_model_view:
            failures.append(Failure(check="citations", detail=f"{citation} cita_inexistente"))
        elif citation not in ctx.allowed:
            failures.append(Failure(check="citations", detail=f"{citation} cita_no_permitida"))
    return failures


def check_tokens_pii(draft: Draft, ctx: ValidationContext) -> list[Failure]:
    """Comprobación 4: todo token existe en el vault y no hay PII en claro (solo rutas, nunca valores)."""
    failures = [
        Failure(check="tokens_pii", detail=f"token desconocido: {token}")
        for token in _unique(match.group(0) for match in TOKEN_RE.finditer(draft.text))
        if not ctx.vault.exists(token)
    ]
    clear = ctx.find_clear_pii(draft.text)
    if clear:
        failures.append(Failure(check="tokens_pii", detail="PII en claro: " + ", ".join(clear)))
    return failures


def _unique(items: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(items))
