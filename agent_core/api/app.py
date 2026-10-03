"""`create_app`: la puerta HTTP del motor (M9 §2). Sin lógica de conversación: valida, autoriza y delega."""

from collections.abc import Callable
from dataclasses import dataclass, field
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as package_version
from typing import Annotated, Final

from fastapi import APIRouter, FastAPI, Header, Request
from fastapi.responses import JSONResponse
from fastapi.telemetry import TelemetryConfig

from agent_core.api.authorization import RunAuthorizer
from agent_core.api.gate import ANON_SESSION_ATTR, AccessGate, Admitted
from agent_core.api.limits import LimitGuard, RateLimitConfig
from agent_core.api.problems import install_error_handlers
from agent_core.api.protocols import (
    DenialRecorder,
    HandoffReader,
    SecurityLog,
    TranscriptService,
    TurnService,
)
from agent_core.api.routing import DecimalJsonRoute, JsonResponse
from agent_core.api.schemas import (
    CreateRunBody,
    ProblemBody,
    ResolutionBody,
    TurnBody,
    publish_run,
    publish_turn,
    run_summary,
    session_lineage,
)
from agent_core.api.tracing import install_tracing, request_trace_id
from agent_core.domain import (
    SCHEMA_VERSION,
    EngineError,
    Principal,
    ProblemCode,
    RunInput,
    RunState,
    TurnInput,
)
from agent_core.ports import (
    AuthzPort,
    Clock,
    CostCounters,
    IdentityVerifier,
    IdSource,
    RegistryPort,
    UnitOfWorkFactory,
)

Authenticate = Callable[[Request, str | None], Principal]
"""Admisión de M9 (firma, vigencia, límites) expuesta a las extensiones; lanza los mismos errores."""
ApiExtension = Callable[[FastAPI, Authenticate], None]
"""Monta rutas adicionales en la app. M9 no conoce a quien la implementa (p. ej. el registry)."""

_MAX_IDEMPOTENCY_KEY = 255
_PROBLEMS = {
    code: {"model": ProblemBody, "description": text}
    for code, text in {
        401: "credentials_invalid | principal_expired",
        403: "subject_forbidden | agent_forbidden | version_pin_forbidden | delegation_expired | "
        "delegation_mismatch | principal_mismatch",
        404: "not_found",
        409: "turn_in_progress | handoff_already_resolved | idempotency_conflict",
        410: "run_closed",
        422: "invalid_request",
        429: "rate_limited | cost_budget_exceeded",
    }.items()
}

Authorization = Annotated[str | None, Header(description="Principal firmado (`IdentityVerifier`).")]
OnBehalfOfHeader = Annotated[
    str | None, Header(alias="X-On-Behalf-Of", description="Delegación firmada (solo asesores).")
]


@dataclass(frozen=True)
class ApiDeps:
    """Todo lo que la API necesita, inyectado. El cableado real vive en `agent_core.composition`."""

    verifier: IdentityVerifier
    authz: AuthzPort
    registry: RegistryPort
    uow_factory: UnitOfWorkFactory
    counters: CostCounters
    clock: Clock
    ids: IdSource
    turns: TurnService
    handoffs: HandoffReader
    transcripts: TranscriptService
    denials: DenialRecorder
    security: SecurityLog
    limits: RateLimitConfig = field(default_factory=RateLimitConfig)
    step_up_simulated: bool = True  # el OTP de la demo es simulado (ADR 0010); apagar con un OTP real
    extensions: tuple[ApiExtension, ...] = ()  # rutas de otros paquetes; vacío = comportamiento previo
    # Comprobaciones de `/readyz` (nombre, función). Vacío = siempre listo. Las inyecta el cableado.
    readiness: tuple[tuple[str, Callable[[], bool]], ...] = ()
    build_sha: str | None = None  # commit de la imagen (`AGENTCORE_GIT_SHA`); lo informa `/version` (N-04)


def _package_version() -> str:
    try:
        return package_version("agent-core")
    except PackageNotFoundError:
        return "0+unknown"


def _failed_checks(checks: tuple[tuple[str, Callable[[], bool]], ...]) -> list[str]:
    """Nombres de las comprobaciones que no pasan. Una que lanza cuenta como fallida y su mensaje se descarta:
    puede traer hosts o credenciales."""
    failed: list[str] = []
    for name, check in checks:
        try:
            ok = check()
        except Exception:
            ok = False
        if not ok:
            failed.append(name)
    return failed


def _idempotency_key(raw: str | None, principal: Principal) -> str:
    """La clave que ve M4. Los anónimos comparten `PrincipalKey(customer, None)`: se antepone su sesión
    firmada para que la clave de uno nunca devuelva el run de otro."""
    if raw is None or not raw.strip() or len(raw) > _MAX_IDEMPOTENCY_KEY or not raw.isprintable():
        raise EngineError(ProblemCode.invalid_request, "Idempotency-Key")
    if principal.id is None:
        return f"{principal.attrs[ANON_SESSION_ATTR]}:{raw}"
    return raw


# U1/C1: FastAPI >= 0.142 ships native OpenTelemetry on by default (tracing, metrics, logs, operation
# spans and auto-configuration from OTEL_* env vars). Its logs plane exports exception messages and
# stack traces, and its spans carry `url.path`/`url.query`. All of it is off: `agentcore.api.request`
# is the only server span and the exporter is configured explicitly by
# `agent_core.composition.setup_observability`.
FASTAPI_TELEMETRY_OFF: Final[TelemetryConfig] = {
    "tracing": False,
    "metrics": False,
    "logs": False,
    "operation_spans": False,
    "auto_configure": False,
}


def create_app(deps: ApiDeps) -> FastAPI:
    app = FastAPI(title="agent-core", version=SCHEMA_VERSION, telemetry=FASTAPI_TELEMETRY_OFF)
    install_tracing(app, deps.ids)
    install_error_handlers(app)

    @app.get("/healthz", include_in_schema=False)
    def healthz() -> dict[str, str]:
        """Liveness: el proceso responde. No toca dependencias."""
        return {"status": "ok"}

    @app.get("/version", include_in_schema=False)
    def version() -> dict[str, str | None]:
        """Qué corre: paquete, versión del contrato y commit de la imagen. Sin credencial y sin datos."""
        return {"package": _package_version(), "contract": SCHEMA_VERSION, "sha": deps.build_sha}

    @app.get("/readyz", include_in_schema=False, response_model=None)
    def readyz() -> JSONResponse:
        """Readiness: todas las comprobaciones inyectadas pasan. Sin credencial y sin detalle de errores."""
        failed = _failed_checks(deps.readiness)
        if failed:
            return JSONResponse({"status": "unavailable", "failed": failed}, status_code=503)
        return JSONResponse({"status": "ready"})

    gate = AccessGate(deps.verifier, deps.clock, deps.ids, deps.denials, deps.security)
    guard = LimitGuard(deps.counters, deps.clock, deps.limits)
    authorizer = RunAuthorizer(deps.authz, deps.registry, deps.clock, deps.ids, deps.denials, deps.security)
    router = APIRouter(prefix="/v1", route_class=DecimalJsonRoute, responses=_PROBLEMS)  # type: ignore[arg-type]

    def admit(
        request: Request,
        authorization: str | None,
        on_behalf_of: str | None,
        session_id: str | None = None,
    ) -> Admitted:
        """Chequeos 1 a 5: firma, vigencia, delegación, coincidencia de principal y límites."""
        trace_id = request_trace_id(request)

        def load_run() -> RunState | None:
            if session_id is None:
                return None
            with deps.uow_factory() as uow:
                return uow.find_run_by_session(session_id)

        admitted = gate.admit(authorization, on_behalf_of, load_run, trace_id=trace_id)
        guard.check(admitted.principal)
        return admitted

    def readable_run(
        request: Request, run_id: str, authorization: str | None, on_behalf_of: str | None
    ) -> tuple[Admitted, RunState]:
        admitted = admit(request, authorization, on_behalf_of)
        with deps.uow_factory() as uow:
            run = uow.load_run(run_id)
        if run is None:
            raise EngineError(ProblemCode.not_found, "run")
        authorizer.authorize_read(admitted, run, trace_id=request_trace_id(request))
        return admitted, run

    @router.post("/runs", status_code=201, summary="Crea un run", operation_id="create_run")
    def create_run(
        request: Request,
        body: CreateRunBody,
        authorization: Authorization = None,
        on_behalf_of: OnBehalfOfHeader = None,
        idempotency_key: Annotated[
            str | None, Header(description="Obligatoria; identifica el intento.")
        ] = None,
    ) -> JsonResponse:
        admitted = admit(request, authorization, on_behalf_of)
        run_input = RunInput(
            agent=body.agent,
            subject=body.subject,
            input=body.input,
            lang=body.lang,
            idempotency_key=_idempotency_key(idempotency_key, admitted.principal),
        )
        run_input = authorizer.authorize_new_run(admitted, run_input, trace_id=request_trace_id(request))
        result = deps.turns.start_run(admitted.principal, admitted.on_behalf_of, run_input)
        return JsonResponse(publish_run(result, step_up_simulated=deps.step_up_simulated), 201)

    @router.post("/sessions/{session_id}/turns", summary="Procesa un turno", operation_id="post_turn")
    def post_turn(
        request: Request,
        session_id: str,
        body: TurnBody,
        authorization: Authorization = None,
        on_behalf_of: OnBehalfOfHeader = None,
    ) -> JsonResponse:
        admitted = admit(request, authorization, on_behalf_of, session_id)
        if admitted.run is None:
            raise EngineError(ProblemCode.not_found, "sesión")
        authorizer.authorize_existing_run(admitted, admitted.run, trace_id=request_trace_id(request))
        turn = TurnInput(
            session_id=session_id,
            text=body.text,
            channel=body.channel,
            lang=body.lang,
            client_turn_id=body.client_turn_id,
            confirm=body.confirm,
        )
        result = deps.turns.handle_turn(admitted.principal, admitted.on_behalf_of, turn)
        return JsonResponse(publish_turn(result, step_up_simulated=deps.step_up_simulated))

    @router.get("/runs/{run_id}", summary="Estado resumido de un run", operation_id="get_run")
    def get_run(
        request: Request,
        run_id: str,
        authorization: Authorization = None,
        on_behalf_of: OnBehalfOfHeader = None,
    ) -> JsonResponse:
        _, run = readable_run(request, run_id, authorization, on_behalf_of)
        return JsonResponse(run_summary(run, request_trace_id(request)))

    @router.get(
        "/sessions/{session_id}/lineage", summary="Linaje de la sesión", operation_id="get_session_lineage"
    )
    def get_session_lineage(
        request: Request,
        session_id: str,
        authorization: Authorization = None,
        on_behalf_of: OnBehalfOfHeader = None,
    ) -> JsonResponse:
        admitted = admit(request, authorization, on_behalf_of, session_id)
        if admitted.run is None:
            raise EngineError(ProblemCode.not_found, "sesión")
        with deps.uow_factory() as uow:
            runs = uow.list_runs_by_session(session_id)
        trace_id = request_trace_id(request)
        for run in runs:
            authorizer.authorize_read(admitted, run, trace_id=trace_id)
        return JsonResponse(session_lineage(session_id, runs, trace_id))

    @router.get("/runs/{run_id}/transcript", summary="Transcript renderizado", operation_id="get_transcript")
    def get_transcript(
        request: Request,
        run_id: str,
        authorization: Authorization = None,
        on_behalf_of: OnBehalfOfHeader = None,
    ) -> JsonResponse:
        admitted, run = readable_run(request, run_id, authorization, on_behalf_of)
        try:
            entries = deps.transcripts.read_rendered(run.run_id, admitted.principal, admitted.on_behalf_of)
        except LookupError:
            raise EngineError(ProblemCode.not_found, "run") from None
        rendered = [
            {
                "turn_id": e.turn_id,
                "role": e.role,
                "text": e.text,
                "reason": e.reason,
                "unknown_tokens": list(e.unknown_tokens),
            }
            for e in entries
        ]
        return JsonResponse(
            {"run_id": run.run_id, "entries": rendered, "trace_id": request_trace_id(request)}
        )

    @router.get("/handoffs/{handoff_ref}", summary="Paquete de traspaso", operation_id="get_handoff")
    def get_handoff(
        request: Request,
        handoff_ref: str,
        authorization: Authorization = None,
        on_behalf_of: OnBehalfOfHeader = None,
    ) -> JsonResponse:
        admitted = admit(request, authorization, on_behalf_of)
        packet = deps.handoffs.get(handoff_ref, admitted.principal, admitted.on_behalf_of)
        return JsonResponse({**packet, "trace_id": request_trace_id(request)})

    @router.post(
        "/handoffs/{handoff_ref}/resolution", summary="Resuelve un traspaso", operation_id="post_resolution"
    )
    def post_resolution(
        request: Request,
        handoff_ref: str,
        body: ResolutionBody,
        authorization: Authorization = None,
        on_behalf_of: OnBehalfOfHeader = None,
    ) -> JsonResponse:
        admitted = admit(request, authorization, on_behalf_of)
        deps.handoffs.record_resolution(
            handoff_ref,
            admitted.principal,
            body.resolution_code,
            body.handoff_quality,
            body.notes,
            on_behalf_of=admitted.on_behalf_of,
        )
        return JsonResponse(
            {
                "handoff_ref": handoff_ref,
                "resolution_code": body.resolution_code,
                "handoff_quality": body.handoff_quality,
                "trace_id": request_trace_id(request),
            }
        )

    app.include_router(router)

    def authenticate(request: Request, authorization: str | None) -> Principal:
        return admit(request, authorization, None).principal

    for extension in deps.extensions:
        extension(app, authenticate)
    return app
