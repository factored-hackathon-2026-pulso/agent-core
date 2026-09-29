"""Comprobaciones del validador de M8: cada una es `check_x(draft, ctx) -> list[Failure]` (spec §9)."""

from collections.abc import Callable

from agent_core.response.types import Draft, Failure, ValidationContext

Check = Callable[[Draft, ValidationContext], list[Failure]]


def check_format(draft: Draft, ctx: ValidationContext) -> list[Failure]:
    """Comprobación 1: invariantes del `Draft` (el parseo de la salida del gateway es `parse_draft`)."""
    if not draft.text.strip():
        return [Failure(check="format", detail="texto vacío")]
    return []
