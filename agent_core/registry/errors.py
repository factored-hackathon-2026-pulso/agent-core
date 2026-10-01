"""Errores tipados del registry (spec §7.4). No se añaden a `ProblemCode` de M0."""

from collections.abc import Mapping
from enum import StrEnum
from types import MappingProxyType

from agent_core.domain import JsonValue


class RegistryErrorCode(StrEnum):
    validation_failed = "validation_failed"
    gate_failed = "gate_failed"
    proposal_stale = "proposal_stale"
    candidate_changed = "candidate_changed"
    illegal_transition = "illegal_transition"
    forbidden_role = "forbidden_role"
    step_up_required = "step_up_required"
    integrity_error = "integrity_error"
    not_found = "not_found"


HTTP_STATUS: Mapping[RegistryErrorCode, int] = MappingProxyType({
    RegistryErrorCode.validation_failed: 422,
    RegistryErrorCode.gate_failed: 409,
    RegistryErrorCode.proposal_stale: 409,
    RegistryErrorCode.candidate_changed: 409,
    RegistryErrorCode.illegal_transition: 409,
    RegistryErrorCode.forbidden_role: 403,
    RegistryErrorCode.step_up_required: 403,
    RegistryErrorCode.integrity_error: 500,
    RegistryErrorCode.not_found: 404,
})


class RegistryError(Exception):
    """`detail` es texto para personas, sin datos de clientes. `payload` viaja en el cuerpo del error."""

    def __init__(self, code: RegistryErrorCode, detail: str = "", payload: JsonValue = None) -> None:
        super().__init__(f"{code.value}: {detail}")
        self.code = code
        self.detail = detail
        self.payload = payload


class IntegrityError(RegistryError):
    """El contenido leído no coincide con su hash: nunca se sirve."""

    def __init__(self, detail: str = "") -> None:
        super().__init__(RegistryErrorCode.integrity_error, detail)
