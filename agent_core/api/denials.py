"""Registro de un rechazo: log de seguridad siempre; `access_denied` en la cadena solo si el run existe."""

from agent_core.api.protocols import DenialRecorder, SecurityLog
from agent_core.domain import (
    AccessDenied,
    AccessDeniedPayload,
    AccessDeniedReason,
    EngineError,
    PrincipalType,
    ProblemCode,
    RunState,
)
from agent_core.ports import Clock, IdKind, IdSource

# Códigos que la spec de M9 (§6) registra como `access_denied`. `credentials_invalid`, `version_pin_forbidden`
# y los rechazos sin run existente van solo al log de seguridad.
_CHAIN_REASON = {
    ProblemCode.principal_expired: AccessDeniedReason.principal_expired,
    ProblemCode.delegation_expired: AccessDeniedReason.delegation_expired,
    ProblemCode.delegation_mismatch: AccessDeniedReason.delegation_mismatch,
    ProblemCode.principal_mismatch: AccessDeniedReason.principal_mismatch,
    ProblemCode.subject_forbidden: AccessDeniedReason.subject_forbidden,
    ProblemCode.agent_forbidden: AccessDeniedReason.agent_forbidden,
}


class Denials:
    def __init__(self, clock: Clock, ids: IdSource, recorder: DenialRecorder, security: SecurityLog) -> None:
        self._clock = clock
        self._ids = ids
        self._recorder = recorder
        self._security = security

    def deny(
        self,
        code: ProblemCode,
        run: RunState | None,
        principal_type: PrincipalType | None,
        trace_id: str,
        detail: str = "",
    ) -> EngineError:
        who = principal_type.value if principal_type is not None else None
        self._security.record(code.value, who, trace_id)
        reason = _CHAIN_REASON.get(code)
        if run is not None and reason is not None:
            event = AccessDenied(
                event_id=self._ids.new_id(IdKind.event),
                run_id=run.run_id,
                session_id=run.session_id,
                release=run.release,
                ts=self._clock.now(),
                payload=AccessDeniedPayload(reason=reason),
            )
            try:
                self._recorder.append_standalone(run.run_id, [event])
            except Exception:  # la denegación no depende de que la auditoría esté disponible
                self._security.record("audit_write_failed", who, trace_id)
        return EngineError(code, detail)
