"""API REST del registry (spec §7.4). La monta M9 como extensión; no importa M9 ni M9 la importa."""

from collections.abc import Callable
from typing import Annotated, Any

from fastapi import APIRouter, FastAPI, Header, Request
from fastapi.responses import Response
from pydantic import BaseModel

from agent_core.domain import CredentialsInvalid, Principal, dumps
from agent_core.registry.errors import HTTP_STATUS, RegistryError, RegistryErrorCode
from agent_core.registry.models import EntityDraft, Origin
from agent_core.registry.service import RegistryService

Authenticate = Callable[[Request, str | None], Principal]
Auth = Annotated[str | None, Header(alias="authorization")]


class _Create(BaseModel):
    agent_id: str
    origin: Origin = Origin.manual
    title: str


class _Draft(BaseModel):
    expected_rev: int
    changes: list[EntityDraft]


class _Evaluate(BaseModel):
    suite_id: str
    suite_version: str | None = None


class _Approve(BaseModel):
    candidate_hash: str


class _Reason(BaseModel):
    reason: str


class _Promote(BaseModel):
    release_id: str
    reason: str = ""


class _Problem(Response):
    media_type = "application/problem+json"


def _json(value: Any, status: int = 200) -> Response:
    """`dumps` de M0 (Decimal exacto) en vez del codificador de FastAPI."""
    return Response(dumps(value), status_code=status, media_type="application/json")


def _trace_id(request: Request) -> str | None:
    value = getattr(request.state, "trace_id", None)
    return None if value is None else str(value)


def _problem(request: Request, exc: RegistryError) -> Response:
    status = HTTP_STATUS[exc.code]
    body: dict[str, Any] = {
        "type": f"urn:agentcore:registry:{exc.code.value}",
        "title": exc.code.value,
        "status": status,
        "code": exc.code.value,
        "detail": exc.detail,
        "trace_id": _trace_id(request),
    }
    if exc.code is RegistryErrorCode.validation_failed:
        body["violations"] = exc.payload
    elif exc.payload is not None:
        body["payload"] = exc.payload
    return _Problem(dumps(body), status_code=status)


def registry_extension(service: RegistryService) -> Callable[[FastAPI, Authenticate], None]:
    def install(app: FastAPI, authenticate: Authenticate) -> None:
        router = APIRouter(prefix="/v1/registry")

        @app.exception_handler(RegistryError)
        async def _registry_error(request: Request, exc: RegistryError) -> Response:
            return _problem(request, exc)

        if CredentialsInvalid not in app.exception_handlers:  # M9 ya instala el suyo

            @app.exception_handler(CredentialsInvalid)
            async def _creds(request: Request, exc: CredentialsInvalid) -> Response:
                body = {"code": "credentials_invalid", "status": 401, "trace_id": _trace_id(request)}
                return _Problem(dumps(body), status_code=401)

        def who(request: Request, authorization: str | None) -> Principal:
            return authenticate(request, authorization)

        @router.post("/proposals", status_code=201)
        def create(request: Request, body: _Create, authorization: Auth = None) -> Response:
            actor = who(request, authorization)
            return _json(service.create_proposal(actor, body.agent_id, body.origin, body.title), 201)

        @router.get("/proposals/{pid}")
        def show(request: Request, pid: str, authorization: Auth = None) -> Response:
            who(request, authorization)
            return _json(service.get_proposal(pid))

        @router.put("/proposals/{pid}/draft")
        def draft(request: Request, pid: str, body: _Draft, authorization: Auth = None) -> Response:
            return _json(service.put_draft(who(request, authorization), pid, body.changes, body.expected_rev))

        @router.post("/proposals/{pid}/validate")
        def validate(request: Request, pid: str, authorization: Auth = None) -> Response:
            return _json(service.validate(who(request, authorization), pid))

        @router.post("/proposals/{pid}/freeze")
        def freeze(request: Request, pid: str, authorization: Auth = None) -> Response:
            return _json(service.freeze(who(request, authorization), pid))

        @router.post("/proposals/{pid}/reopen")
        def reopen(request: Request, pid: str, authorization: Auth = None) -> Response:
            return _json(service.reopen(who(request, authorization), pid))

        @router.post("/proposals/{pid}/evaluate")
        def evaluate(request: Request, pid: str, body: _Evaluate, authorization: Auth = None) -> Response:
            actor = who(request, authorization)
            return _json(service.evaluate(actor, pid, body.suite_id, body.suite_version))

        @router.post("/proposals/{pid}/approve")
        def approve(request: Request, pid: str, body: _Approve, authorization: Auth = None) -> Response:
            return _json(service.approve(who(request, authorization), pid, body.candidate_hash))

        @router.post("/proposals/{pid}/reject")
        def reject(request: Request, pid: str, body: _Reason, authorization: Auth = None) -> Response:
            return _json(service.reject(who(request, authorization), pid, body.reason))

        @router.post("/proposals/{pid}/publish")
        def publish(
            request: Request,
            pid: str,
            idempotency_key: Annotated[str, Header()],
            authorization: Auth = None,
        ) -> Response:
            return _json(service.publish(who(request, authorization), pid, idempotency_key))

        @router.post("/aliases/{agent_id}/{alias}")
        def promote(
            request: Request, agent_id: str, alias: str, body: _Promote, authorization: Auth = None
        ) -> Response:
            actor = who(request, authorization)
            return _json(service.promote(actor, agent_id, alias, body.release_id, body.reason))

        @router.post("/releases/{rid}/revoke")
        def revoke(request: Request, rid: str, body: _Reason, authorization: Auth = None) -> Response:
            return _json(service.revoke(who(request, authorization), rid, body.reason))

        @router.get("/releases/{rid}")
        def release(request: Request, rid: str, authorization: Auth = None) -> Response:
            who(request, authorization)
            return _json(service.get_release(rid))

        @router.get("/releases/{a}/diff/{b}")
        def diff(request: Request, a: str, b: str, authorization: Auth = None) -> Response:
            who(request, authorization)
            return _json(service.diff_releases(a, b))

        @router.get("/entities/{kind}/{eid:path}")
        def entity(request: Request, kind: str, eid: str, authorization: Auth = None) -> Response:
            """`eid` admite `/` (p. ej. `t/saludo`); una versión se pide con `?version=`."""
            who(request, authorization)
            return _json(service.get_entity(kind, eid, request.query_params.get("version")))

        @router.get("/runs/{run_id}/lineage")
        def lineage(request: Request, run_id: str, authorization: Auth = None) -> Response:
            return _json(service.lineage_for_run(who(request, authorization), run_id))

        app.include_router(router)

    return install
