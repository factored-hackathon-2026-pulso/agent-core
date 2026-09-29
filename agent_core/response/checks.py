"""Comprobaciones del validador de M8: cada una es `check_x(draft, ctx) -> list[Failure]` (spec §9)."""

from collections.abc import Callable

from agent_core.response.types import Draft, Failure, ValidationContext

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
