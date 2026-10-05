"""Errores del motor (M0 §2.11).

`DomainError` son errores internos (no HTTP). `EngineError` lleva un `ProblemCode` estable que M9 traduce a
`application/problem+json`.
"""

from collections.abc import Mapping
from decimal import Decimal
from enum import StrEnum
from types import MappingProxyType


class DomainError(Exception):
    """Error interno del motor; no es un error HTTP."""


class InvalidRuntimeRef(DomainError):
    """Una referencia no exacta (rango o sin versión) llegó a runtime."""


class SchemaError(DomainError):
    """Un dato del registro no valida contra su modelo."""


class IllegalTransition(DomainError):
    """Transición no permitida en una máquina de estados (bug, no error de usuario)."""


class VersionConflict(DomainError):
    """`save_run` con una `state_version` que no es la vigente."""


class TurnInProgress(DomainError):
    """`acquire_turn` encontró un lease vigente de otro turno."""


class CredentialsInvalid(DomainError):
    """`IdentityVerifier`: la firma de la credencial no valida."""


class GrantCheckUnavailable(DomainError):
    """`IdentityVerifier.grant_active`: el servicio de asignaciones no pudo responder. El acceso sigue
    cerrado, pero no se afirma que la delegación venció (M9 responde `503 identity_unavailable`, ADR 0010)."""


class GatewayErrorKind(StrEnum):
    """Clases de falla del gateway de LLM (M0 §2.11)."""
    timeout = "timeout"
    unavailable = "unavailable"
    rate_limited = "rate_limited"
    invalid_output = "invalid_output"
    refused = "refused"


class GatewayError(DomainError):
    """`LLMGateway.generate` falló. Lleva el uso parcial que el proveedor informó (si lo hizo).

    El mensaje solo incluye `kind` y `model`: nunca contenido de prompts/salidas ni claves.
    """

    def __init__(
        self,
        kind: GatewayErrorKind,
        *,
        tokens_in: int | None = None,
        tokens_out: int | None = None,
        cost_usd: Decimal | None = None,
        model: str | None = None,
    ) -> None:
        super().__init__(f"gateway {kind.value}" + (f" ({model})" if model else ""))
        self.kind = kind
        self.tokens_in = tokens_in
        self.tokens_out = tokens_out
        self.cost_usd = cost_usd
        self.model = model


class ProblemCode(StrEnum):
    """Códigos de problema estables que expone la API (M0 §2.11)."""
    credentials_invalid = "credentials_invalid"
    principal_expired = "principal_expired"
    subject_forbidden = "subject_forbidden"
    agent_forbidden = "agent_forbidden"
    version_pin_forbidden = "version_pin_forbidden"
    delegation_expired = "delegation_expired"
    delegation_mismatch = "delegation_mismatch"
    principal_mismatch = "principal_mismatch"
    not_found = "not_found"
    turn_in_progress = "turn_in_progress"
    handoff_already_resolved = "handoff_already_resolved"
    idempotency_conflict = "idempotency_conflict"
    idempotency_in_progress = "idempotency_in_progress"
    run_closed = "run_closed"
    invalid_request = "invalid_request"
    rate_limited = "rate_limited"
    cost_budget_exceeded = "cost_budget_exceeded"
    internal_error = "internal_error"
    identity_unavailable = "identity_unavailable"


PROBLEM_STATUS: Mapping[ProblemCode, int] = MappingProxyType(
    {
        ProblemCode.credentials_invalid: 401,
        ProblemCode.principal_expired: 401,
        ProblemCode.subject_forbidden: 403,
        ProblemCode.agent_forbidden: 403,
        ProblemCode.version_pin_forbidden: 403,
        ProblemCode.delegation_expired: 403,
        ProblemCode.delegation_mismatch: 403,
        ProblemCode.principal_mismatch: 403,
        ProblemCode.not_found: 404,
        ProblemCode.turn_in_progress: 409,
        ProblemCode.handoff_already_resolved: 409,
        ProblemCode.idempotency_conflict: 409,
        ProblemCode.idempotency_in_progress: 409,
        ProblemCode.run_closed: 410,
        ProblemCode.invalid_request: 422,
        ProblemCode.rate_limited: 429,
        ProblemCode.cost_budget_exceeded: 429,
        ProblemCode.internal_error: 500,
        ProblemCode.identity_unavailable: 503,
    }
)


class EngineError(Exception):
    """Error con código HTTP estable. Solo lo lanzan los módulos; M9 lo traduce."""

    def __init__(self, code: ProblemCode, detail: str = "", *, retry_after: int | None = None) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail
        self.retry_after = retry_after  # segundos; sale como `Retry-After` (429 y 503)

    @property
    def status(self) -> int:
        return PROBLEM_STATUS[self.code]
