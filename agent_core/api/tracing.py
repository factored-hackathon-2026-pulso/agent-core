"""`trace_id` por request (ADR 0003 #4): el de la traza OTel si hay una activa; si no, uno del `IdSource`."""

from fastapi import FastAPI, Request, Response
from starlette.middleware.base import RequestResponseEndpoint

from agent_core.ports import IdKind, IdSource
from agent_telemetry.setup import get_provider

FALLBACK_TRACE_ID = "unknown"


def request_trace_id(request: Request) -> str:
    """Solo lo escribe `install_tracing`; sin él (prueba mal armada) no se inventa uno."""
    return str(getattr(request.state, "trace_id", FALLBACK_TRACE_ID))


def install_tracing(app: FastAPI, ids: IdSource) -> None:
    """Un span `agentcore.api.request` por request. Sus atributos son una lista cerrada: nunca credenciales,
    `principal.id` ni el body."""

    @app.middleware("http")
    async def _trace(request: Request, call_next: RequestResponseEndpoint) -> Response:
        tracer = get_provider().get_tracer("agentcore.api")
        with tracer.start_as_current_span(
            "agentcore.api.request", attributes={"http.request.method": request.method}
        ) as span:
            ctx = span.get_span_context()
            valid = ctx.is_valid
            request.state.trace_id = format(ctx.trace_id, "032x") if valid else ids.new_id(IdKind.event)
            response = await call_next(request)
            span.set_attribute("http.response.status_code", response.status_code)
            return response
