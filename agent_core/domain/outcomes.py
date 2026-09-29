"""Resultados, códigos de motivo y enums del turno (M0 §2.7)."""

import re
from collections.abc import Mapping
from enum import StrEnum
from types import MappingProxyType
from typing import Annotated, Literal

from pydantic import AfterValidator

Mode = Literal["conversational", "task"]


class Outcome(StrEnum):
    """Resultado final de un run (M0 §2.7)."""
    resolved = "resolved"
    abstained = "abstained"
    cancelled = "cancelled"
    clarify_exhausted = "clarify_exhausted"
    completed = "completed"
    failed = "failed"
    abandoned = "abandoned"
    escalated = "escalated"


DECLARABLE: Mapping[str, frozenset[Outcome]] = MappingProxyType(
    {
        "conversational": frozenset(
            {Outcome.resolved, Outcome.abstained, Outcome.cancelled, Outcome.clarify_exhausted}
        ),
        "task": frozenset({Outcome.completed, Outcome.failed}),
    }
)


def is_declarable(outcome: Outcome, mode: str) -> bool:
    """Única fuente de outcomes declarables por un `end` (M1 G0-14, M2). `abandoned`/`escalated` nunca."""
    return outcome in DECLARABLE.get(mode, frozenset())


class ReasonCode(StrEnum):
    """Códigos de motivo de escalamiento o cierre (M0 §2.7)."""
    low_confidence = "low_confidence"
    budget_exceeded = "budget_exceeded"
    tool_failure = "tool_failure"
    customer_request = "customer_request"
    verification_failed = "verification_failed"
    validation_failed = "validation_failed"
    release_revoked = "release_revoked"
    auth_insufficient = "auth_insufficient"


# fullmatch: en `re`, `$` admite un salto de línea final. Clases solo ASCII.
_REASON_PREFIX = re.compile(r"(rule|policy|interrupt):[a-z0-9][a-z0-9_/-]*")

_REASON_CODES = frozenset(code.value for code in ReasonCode)


def _check_reason(value: str) -> str:
    if value in _REASON_CODES or _REASON_PREFIX.fullmatch(value):
        return value
    raise ValueError(f"reason_code inválido: {value!r}")


ReasonCodeStr = Annotated[str, AfterValidator(_check_reason)]


class Awaiting(StrEnum):
    """Qué espera el run del usuario en el siguiente turno (M0 §2.7)."""
    none = "none"
    slot = "slot"
    confirmation = "confirmation"
    step_up = "step_up"
    input = "input"


class Command(StrEnum):
    """Comandos de Understand (spec general §4.4)."""

    start_flow = "start_flow"
    continue_ = "continue"
    affirm = "affirm"
    deny = "deny"
    clarify = "clarify"
    cancel = "cancel"
    handoff = "handoff"
    out_of_scope = "out_of_scope"
    interrupt = "interrupt"
