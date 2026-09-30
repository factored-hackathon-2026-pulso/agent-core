"""Errores como `application/problem+json` (M9 §3.5): `{type, title, status, code, detail, trace_id}`.

Nunca se devuelve el texto de una excepción inesperada, el de `CredentialsInvalid` ni el valor de un campo
inválido: el `detail` es del motor (`EngineError`), de los nombres de campo o vacío."""

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from agent_core.api.tracing import request_trace_id
from agent_core.domain import PROBLEM_STATUS, CredentialsInvalid, EngineError, ProblemCode

log = logging.getLogger("agentcore.api")

PROBLEM_TITLES: dict[ProblemCode, str] = {
    ProblemCode.credentials_invalid: "Credenciales inválidas",
    ProblemCode.principal_expired: "Principal vencido",
    ProblemCode.subject_forbidden: "Sujeto no autorizado",
    ProblemCode.agent_forbidden: "Agente no autorizado",
    ProblemCode.version_pin_forbidden: "Fijar versión no permitido",
    ProblemCode.delegation_expired: "Delegación vencida o revocada",
    ProblemCode.delegation_mismatch: "Delegación de otro asesor",
    ProblemCode.principal_mismatch: "Principal distinto al del run",
    ProblemCode.not_found: "Recurso inexistente",
    ProblemCode.turn_in_progress: "Turno en curso",
    ProblemCode.handoff_already_resolved: "Traspaso ya resuelto",
    ProblemCode.idempotency_conflict: "Conflicto de idempotencia",
    ProblemCode.run_closed: "Run cerrado",
    ProblemCode.invalid_request: "Solicitud inválida",
    ProblemCode.rate_limited: "Límite de tasa excedido",
    ProblemCode.cost_budget_exceeded: "Tope de costo diario excedido",
    ProblemCode.internal_error: "Error interno",
}


class ProblemResponse(JSONResponse):
    media_type = "application/problem+json"


def problem(code: ProblemCode, detail: str, trace_id: str, status: int | None = None) -> ProblemResponse:
    status = PROBLEM_STATUS[code] if status is None else status
    body = {
        "type": f"urn:agentcore:problem:{code.value}",
        "title": PROBLEM_TITLES[code],
        "status": status,
        "code": code.value,
        "detail": detail,
        "trace_id": trace_id,
    }
    return ProblemResponse(body, status_code=status)


def _validation_detail(exc: RequestValidationError) -> str:
    """Solo dónde y qué regla falló; nunca el valor recibido."""
    return "; ".join(f"{'.'.join(str(part) for part in err['loc'])}: {err['type']}" for err in exc.errors())


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(EngineError)
    async def _engine(request: Request, exc: EngineError) -> ProblemResponse:
        detail = "" if exc.code is ProblemCode.internal_error else exc.detail
        return problem(exc.code, detail, request_trace_id(request))

    @app.exception_handler(CredentialsInvalid)
    async def _credentials(request: Request, exc: CredentialsInvalid) -> ProblemResponse:
        return problem(ProblemCode.credentials_invalid, "", request_trace_id(request))

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError) -> ProblemResponse:
        return problem(ProblemCode.invalid_request, _validation_detail(exc), request_trace_id(request))

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException) -> ProblemResponse:
        # Errores del propio framework (ruta inexistente, método no permitido): conservan su status HTTP.
        code = (
            ProblemCode.not_found
            if exc.status_code == 404
            else (ProblemCode.internal_error if exc.status_code >= 500 else ProblemCode.invalid_request)
        )
        return problem(code, "", request_trace_id(request), status=exc.status_code)

    @app.exception_handler(Exception)
    async def _unexpected(request: Request, exc: Exception) -> ProblemResponse:
        # solo el tipo: el texto de la excepción puede traer secretos
        log.error("error no controlado: %s", type(exc).__name__)
        return problem(ProblemCode.internal_error, "", request_trace_id(request))
