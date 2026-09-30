"""`RunAuthorizer` (M9 §3.2, §3.3): versión, agente y subject de un run nuevo o de cada turno.

El subject nunca sale del body ni del modelo: para `customer` y `advisor` lo deriva el servidor
(`principal.id` / `on_behalf_of.subject`); si el body trae otro, es `403 subject_forbidden`. La decisión de
fondo es del `AuthzPort` (unidad 3); aquí solo se decide qué subject se le pregunta."""

from agent_core.api.denials import Denials
from agent_core.api.gate import Admitted
from agent_core.api.protocols import DenialRecorder, SecurityLog
from agent_core.domain import (
    Agent,
    AuthLevel,
    EngineError,
    EntityKind,
    EntityRef,
    OnBehalfOf,
    Principal,
    PrincipalType,
    ProblemCode,
    RunInput,
    RunState,
    SubjectRef,
)
from agent_core.ports import AuthzPort, Clock, IdSource, RegistryPort

_PIN_ALLOWED = {PrincipalType.service, PrincipalType.builder}


class _SubjectRejected(Exception):
    """El body pide un subject distinto del que el servidor deriva para ese principal."""


def _pins_version(run_input: RunInput) -> bool:
    selector = run_input.agent
    return selector.version is not None or selector.alias not in (None, "prod")


class RunAuthorizer:
    def __init__(
        self,
        authz: AuthzPort,
        registry: RegistryPort,
        clock: Clock,
        ids: IdSource,
        recorder: DenialRecorder,
        security: SecurityLog,
    ) -> None:
        self._authz = authz
        self._registry = registry
        self._denials = Denials(clock, ids, recorder, security)

    def authorize_new_run(self, admitted: Admitted, run_input: RunInput, *, trace_id: str) -> RunInput:
        """Devuelve el `RunInput` con el subject derivado por el servidor, o falla con `EngineError`."""
        principal, obo = admitted.principal, admitted.on_behalf_of

        def deny(code: ProblemCode, detail: str = "") -> EngineError:
            return self._denials.deny(code, None, principal.type, trace_id, detail)

        if _pins_version(run_input) and principal.type not in _PIN_ALLOWED:
            raise deny(ProblemCode.version_pin_forbidden)
        agent = self._resolve_agent(run_input, principal)
        try:
            subject = self._derive_subject(principal, obo, agent, run_input.subject)
        except _SubjectRejected:
            raise deny(ProblemCode.subject_forbidden) from None
        decision = self._authz.authorize_agent(principal, agent, subject)
        if not decision.allowed:
            raise deny(ProblemCode.agent_forbidden, decision.reason or "")
        decision = self._authz.authorize_subject(principal, obo, subject)
        if not decision.allowed:
            raise deny(ProblemCode.subject_forbidden, decision.reason or "")
        return run_input.model_copy(update={"subject": subject})

    def authorize_existing_run(self, admitted: Admitted, run: RunState, *, trace_id: str) -> None:
        """Se repite en cada turno (ADR 0006); un rechazo queda en la cadena del run como `access_denied`."""
        principal, obo = admitted.principal, admitted.on_behalf_of
        agent = self._registry.get(run.agent, Agent)
        decision = self._authz.authorize_agent(principal, agent, run.subject)
        if not decision.allowed:
            raise self._denials.deny(
                ProblemCode.agent_forbidden, run, principal.type, trace_id, decision.reason or ""
            )
        decision = self._authz.authorize_subject(principal, obo, run.subject)
        if not decision.allowed:
            raise self._denials.deny(
                ProblemCode.subject_forbidden, run, principal.type, trace_id, decision.reason or ""
            )

    def _resolve_agent(self, run_input: RunInput, principal: Principal) -> Agent:
        try:
            release = self._registry.resolve_release(run_input.agent, principal)
        except LookupError:  # el puerto no define la excepción de un agente o alias inexistente
            raise EngineError(ProblemCode.not_found, "agente") from None
        version = release.entities.get(EntityKind.agent, {}).get(run_input.agent.id)
        if version is None:
            raise EngineError(ProblemCode.not_found, "agente")
        try:
            return self._registry.get(EntityRef(id=run_input.agent.id, version=version), Agent)
        except LookupError:
            raise EngineError(ProblemCode.not_found, "agente") from None

    @staticmethod
    def _derive_subject(
        principal: Principal, obo: OnBehalfOf | None, agent: Agent, requested: SubjectRef | None
    ) -> SubjectRef | None:
        """El subject que se autoriza; `_SubjectRejected` si el body pide uno distinto del derivado."""
        derived: SubjectRef | None
        match principal.type:
            case PrincipalType.customer:
                anonymous = principal.auth.level is AuthLevel.anonymous or principal.id is None
                derived = (
                    SubjectRef(kind="customer", ref=principal.id)
                    if agent.subject_kinds and not anonymous and principal.id is not None
                    else None
                )
            case PrincipalType.advisor:
                derived = obo.subject if obo is not None and agent.subject_kinds else None
                if obo is None and agent.subject_kinds:
                    raise _SubjectRejected  # un asesor sin delegación no tiene subject que pedir
            case _:
                return requested  # service y builder: el subject lo piden y lo autorizan los scopes
        if requested is not None and requested != derived:
            raise _SubjectRejected
        return derived
