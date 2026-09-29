"""`validate`: ejecuta todas las comprobaciones en orden y reporta todas las fallas (spec §3.1). Pura."""

from collections.abc import Sequence

from agent_core.response.checks import CHECKS, Check
from agent_core.response.types import CheckId, Draft, Failure, ValidationContext, ValidationResult


def validate(draft: Draft, ctx: ValidationContext,
             checks: Sequence[tuple[CheckId, Check]] = CHECKS) -> ValidationResult:
    failures: list[Failure] = []
    for _, check in checks:
        failures.extend(check(draft, ctx))
    return ValidationResult(ok=not failures, failures=failures)
