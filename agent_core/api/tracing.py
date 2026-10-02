"""`trace_id` por request (ADR 0003 #4): el de la traza OTel si hay una activa; si no, uno del `IdSource`."""

from fastapi import FastAPI, Request, Response
from opentelemetry.trace import Status, StatusCode
from starlette.middleware.base import RequestResponseEndpoint

from agent_core.ports import IdKind, IdSource
from agent_telemetry import tracer as telemetry_tracer

FALLBACK_TRACE_ID = "unknown"


def request_trace_id(request: Request) -> str:
    """Solo lo escribe `install_tracing`; sin él (prueba mal armada) no se inventa uno."""
    return str(getattr(request.state, "trace_id", FALLBACK_TRACE_ID))


def install_tracing(app: FastAPI, ids: IdSource) -> None:
    """Un span `agentcore.api.request` por request. Sus atributos son una lista cerrada
    (`http.request.method`, `http.response.status_code`, `error.type`): nunca credenciales,
    `principal.id` ni el body."""

    @app.middleware("http")
    async def _trace(request: Request, call_next: RequestResponseEndpoint) -> Response:
        tracer = telemetry_tracer("agentcore.api")
        # C2: never an `exception` event (message, stack) nor a status description: only the type.
        with tracer.start_as_current_span(
            "agentcore.api.request",
            attributes={"http.request.method": request.method},
            record_exception=False,
            set_status_on_exception=False,
        ) as span:
            ctx = span.get_span_context()
            valid = ctx.is_valid
            request.state.trace_id = format(ctx.trace_id, "032x") if valid else ids.new_id(IdKind.event)
            try:
                response = await call_next(request)
            except Exception as exc:  # the 500 handler runs outside this middleware (ServerErrorMiddleware)
                span.set_attribute("error.type", type(exc).__name__)
                span.set_attribute("http.response.status_code", 500)
                span.set_status(Status(StatusCode.ERROR))
                raise
            span.set_attribute("http.response.status_code", response.status_code)
            if response.status_code >= 500:
                span.set_status(Status(StatusCode.ERROR))
            return response
