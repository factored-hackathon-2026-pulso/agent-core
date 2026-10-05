"""Exportación paginada de runs, eventos de auditoría y eventos del registry (N-08, §31.12).

Solo lectura y solo para el staff (rol `exporter`). Cada página trae `items` y `next_after`, el cursor para
pedir la siguiente (si la página vino vacía, el mismo que se pidió). Ver `agent_core.ports.export` para el
orden y la garantía de continuidad."""

from collections.abc import Callable
from typing import Annotated, Any

from fastapi import APIRouter, FastAPI, Header, Query, Request
from fastapi.responses import Response

from agent_core.domain import EngineError, Principal, ProblemCode, dumps, to_jsonable
from agent_core.ports import Clock, IdentityVerifier, RunExport
from agent_core.registry.errors import RegistryError
from agent_core.registry.http import problem_response
from agent_core.registry.roles import require_exporter
from agent_core.registry.service import RegistryService

Authenticate = Callable[[Request, str | None], Principal]
MAX_PAGE = 500
Auth = Annotated[str | None, Header(alias="authorization")]
After = Annotated[int, Query(ge=0)]
Limit = Annotated[int, Query(ge=1, le=MAX_PAGE)]


def _bearer(authorization: str | None) -> str:
    scheme, _, rest = (authorization or "").strip().partition(" ")
    return rest.strip() if scheme.lower() == "bearer" else (authorization or "").strip()


def _page(items: list[Any], cursors: list[int], after: int) -> Response:
    body = {"items": items, "next_after": cursors[-1] if cursors else after}
    return Response(dumps(body), media_type="application/json")


def export_extension(runs: RunExport, registry: RegistryService | None, verifier: IdentityVerifier,
                     clock: Clock) -> Callable[[FastAPI, Authenticate], None]:
    """Siempre con el verificador del staff: la exportación no usa la puerta de clientes de M9."""

    def install(app: FastAPI, authenticate: Authenticate) -> None:
        router = APIRouter(prefix="/v1/export")

        def deny(request: Request, authorization: str | None) -> Response | None:
            """`None` si el principal puede exportar; si no, el problema listo para devolver."""
            principal = verifier.verify(_bearer(authorization))  # CredentialsInvalid → 401 (manejador de M9)
            if principal.exp <= clock.now():
                raise EngineError(ProblemCode.principal_expired)
            try:
                require_exporter(principal)
            except RegistryError as exc:
                return problem_response(request, exc)
            return None

        @router.get("/runs")
        def list_runs(request: Request, after: After = 0, limit: Limit = 100,
                      authorization: Auth = None) -> Response:
            """Runs por orden de commit de su último cambio; `after` es el `cursor` de la última página."""
            if (refused := deny(request, authorization)) is not None:
                return refused
            found = runs.list_runs(after, limit)
            return _page([to_jsonable(r) for r in found], [r.cursor for r in found], after)

        @router.get("/runs/{run_id}/events")
        def run_events(request: Request, run_id: str, after: Annotated[int, Query(ge=-1)] = -1,
                       limit: Limit = 100, authorization: Auth = None) -> Response:
            """Eventos del run; `after` es el `seq` del último ya leído (-1 = desde el inicio)."""
            if (refused := deny(request, authorization)) is not None:
                return refused
            found = runs.events_after(run_id, after, limit)
            return _page([to_jsonable(e) for e in found], [e.seq or 0 for e in found], after)

        if registry is not None:

            @router.get("/registry-events")
            def registry_events(request: Request, after: After = 0, limit: Limit = 100,
                                authorization: Auth = None) -> Response:
                """Eventos del registry en orden de registro; `after` es cuántos ya se leyeron."""
                if (refused := deny(request, authorization)) is not None:
                    return refused
                found = registry.list_events(after, limit)
                return _page([to_jsonable(e) for e in found],
                             [after + i for i in range(1, len(found) + 1)], after)

        app.include_router(router)

    return install
