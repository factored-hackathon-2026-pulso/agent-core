"""Tope de peticiones simultáneas: pasadas las N en vuelo, `503` con `Retry-After` sin llegar a la ruta."""

import threading

from starlette.types import ASGIApp, Receive, Scope, Send

from agent_core.api.problems import problem
from agent_core.api.tracing import PROBE_PATHS
from agent_core.domain import ProblemCode


class InflightLimit:
    """ASGI. Cuenta las peticiones de `/v1` que están dentro de la app; las sondas (`/healthz`, `/readyz`,
    `/version`) ni cuentan ni se rechazan. El cupo se libera siempre, también si la app falla. Es un tope de
    carga del proceso, no un límite por principal (eso es `LimitGuard`): el código es `rate_limited` con
    estado 503 para no ampliar el contrato de errores."""

    def __init__(self, app: ASGIApp, max_inflight: int) -> None:
        if max_inflight < 1:
            raise ValueError("max_inflight debe ser positivo")
        self._app = app
        self._max = max_inflight
        self._inflight = 0
        self._lock = threading.Lock()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("path") in PROBE_PATHS:
            await self._app(scope, receive, send)
            return
        with self._lock:
            admitted = self._inflight < self._max
            if admitted:
                self._inflight += 1
        if not admitted:
            trace_id = str(scope.get("state", {}).get("trace_id", ""))
            busy = problem(ProblemCode.rate_limited, "", trace_id, status=503, retry_after=1)
            await busy(scope, receive, send)
            return
        try:
            await self._app(scope, receive, send)
        finally:
            with self._lock:
                self._inflight -= 1
