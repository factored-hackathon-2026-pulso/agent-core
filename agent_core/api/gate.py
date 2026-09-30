"""`AccessGate` (M9 §3.1): credenciales, vigencia, delegación y coincidencia de principal, antes del turno."""

from collections.abc import Callable
from dataclasses import dataclass

from agent_core.api.denials import Denials
from agent_core.api.protocols import DenialRecorder, SecurityLog
from agent_core.domain import (
    CredentialsInvalid,
    EngineError,
    OnBehalfOf,
    Principal,
    ProblemCode,
    RunState,
)
from agent_core.ports import Clock, IdentityVerifier, IdSource

# Un principal anónimo no tiene `id`: su identidad de sesión viaja firmada en `attrs` (decisión de producto,
# M9 §3.1 chequeo 4). Sin ella no hay forma de distinguir a un anónimo de otro, así que se rechaza.
ANON_SESSION_ATTR = "anon_session"


@dataclass(frozen=True)
class Admitted:
    principal: Principal
    on_behalf_of: OnBehalfOf | None


def _blank(raw: str) -> bool:
    return not raw.strip()


def _same_principal(presented: Principal, snapshot: Principal) -> bool:
    if presented.key != snapshot.key:
        return False
    if presented.id is not None:
        return True
    return presented.attrs.get(ANON_SESSION_ATTR) == snapshot.attrs.get(ANON_SESSION_ATTR)


class AccessGate:
    def __init__(
        self,
        verifier: IdentityVerifier,
        clock: Clock,
        ids: IdSource,
        recorder: DenialRecorder,
        security: SecurityLog,
    ) -> None:
        self._verifier = verifier
        self._clock = clock
        self._security = security
        self._denials = Denials(clock, ids, recorder, security)

    def admit(
        self,
        raw_auth: str | None,
        raw_delegation: str | None,
        load_run: Callable[[], RunState | None],
        *,
        trace_id: str,
    ) -> Admitted:
        """Chequeos 1 a 4 del spec, en ese orden. `load_run` es perezoso: el estado no se toca hasta que la
        firma es válida. Falla con `EngineError`; nunca procesa el turno."""
        principal, obo = self._verify(raw_auth, raw_delegation, trace_id)
        run = load_run()
        now = self._clock.now()

        def deny(code: ProblemCode) -> EngineError:
            return self._denials.deny(code, run, principal.type, trace_id)

        if principal.exp <= now:
            raise deny(ProblemCode.principal_expired)
        if obo is not None:
            if obo.exp <= now or not self._verifier.grant_active(obo.grant_ref, now):
                raise deny(ProblemCode.delegation_expired)
            if obo.grantee != principal.key:
                raise deny(ProblemCode.delegation_mismatch)
        if run is not None and not _same_principal(principal, run.principal):
            raise deny(ProblemCode.principal_mismatch)
        return Admitted(principal, obo)

    def _verify(
        self, raw_auth: str | None, raw_delegation: str | None, trace_id: str
    ) -> tuple[Principal, OnBehalfOf | None]:
        """Chequeo 1: firma. Falla cerrado ante cualquier duda; solo log de seguridad, nada en la cadena."""
        try:
            if raw_auth is None or _blank(raw_auth):
                raise CredentialsInvalid("credencial ausente")
            principal = self._verifier.verify(raw_auth)
            if principal.id is None and not principal.attrs.get(ANON_SESSION_ATTR):
                raise CredentialsInvalid("anónimo sin sesión")
            obo = None
            if raw_delegation is not None:
                if _blank(raw_delegation):
                    raise CredentialsInvalid("delegación vacía")
                obo = self._verifier.verify_delegation(raw_delegation)
        except CredentialsInvalid:
            self._security.record(ProblemCode.credentials_invalid.value, None, trace_id)
            raise EngineError(ProblemCode.credentials_invalid) from None
        return principal, obo
