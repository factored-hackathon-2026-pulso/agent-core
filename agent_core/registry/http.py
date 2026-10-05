"""API REST del registry (spec §7.4). La monta M9 como extensión; no importa M9 ni M9 la importa."""

from collections.abc import Callable
from typing import Annotated, Any

from fastapi import APIRouter, FastAPI, Header, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel

from agent_core.domain import CredentialsInvalid, EngineError, Principal, ProblemCode, dumps
from agent_core.ports import Clock, IdentityVerifier
from agent_core.registry.candidate import Candidate
from agent_core.registry.errors import HTTP_STATUS, RegistryError, RegistryErrorCode
from agent_core.registry.evaluation.report import EvalReport
from agent_core.registry.models import (
    AliasChange,
    AliasState,
    Approval,
    EntityDraft,
    EntityVersion,
    Origin,
    PauseState,
    Proposal,
    ProposalState,
    ReasonCode,
    ReleaseDetail,
    ReleaseDiff,
    RunLineage,
    VersionSummary,
)
from agent_core.registry.roles import require_builder
from agent_core.registry.service import ProposalDetail, ProposalPage, RegistryService, ValidationReport

Authenticate = Callable[[Request, str | None], Principal]
Auth = Annotated[str | None, Header(alias="authorization")]
_IDEM_DOC = ("Reintentar con la misma clave devuelve el mismo resultado; "
             "la misma clave con otro cuerpo da 409 idempotency_conflict.")
IdemKey = Annotated[str | None, Header(alias="idempotency-key", max_length=255, description=_IDEM_DOC)]


class ProblemDoc(BaseModel):
    """`application/problem+json` del registry (spec §7.4). `violations` solo en `validation_failed`."""

    type: str
    title: str
    status: int
    code: str
    detail: str | None = None
    trace_id: str | None = None
    violations: list[Any] | None = None
    payload: Any | None = None


_ERRORS = {401: "credencial ausente, inválida o vencida", 403: "rol insuficiente o step-up requerido",
           404: "no existe", 409: "conflicto de estado, de revisión o de idempotencia",
           422: "cuerpo o validación inválidos", 429: "tope de cuota"}


def _doc(model: Any, status: int = 200, *errors: int) -> dict[str, Any]:
    """Documentación OpenAPI de una ruta: el cuerpo de éxito y los problem+json posibles. Solo documenta;
    los handlers devuelven `Response` ya serializada con `dumps`."""
    codes = sorted({401, 403, *errors})
    return {
        "status_code": status,
        "responses": {
            status: {"model": model, "description": "OK"},
            **{c: {"model": ProblemDoc, "description": _ERRORS[c], "content": {
                "application/problem+json": {"schema": {"$ref": "#/components/schemas/ProblemDoc"}}}}
               for c in codes}},
    }


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
    accept_yardstick_loosened: bool = False


class _Reason(BaseModel):
    reason: str
    reason_code: ReasonCode | None = None  # optional closed vocabulary; 422 outside the list


class _Promote(BaseModel):
    release_id: str
    reason: str = ""


# Cuerpos de request con nombre público: `agentcore contracts` los publica en `contracts/registry/` (N-01).
REQUEST_BODIES: dict[str, type[BaseModel]] = {
    "CreateProposalBody": _Create, "PutDraftBody": _Draft, "EvaluateBody": _Evaluate,
    "ApproveBody": _Approve, "ReasonBody": _Reason, "PromoteBody": _Promote,
}


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


problem_response = _problem  # para extensiones hermanas (p. ej. la exportación, N-08)


def _bearer(authorization: str | None) -> str:
    scheme, _, rest = (authorization or "").strip().partition(" ")
    return rest.strip() if scheme.lower() == "bearer" else (authorization or "").strip()


def registry_extension(service: RegistryService, verifier: IdentityVerifier | None = None,
                       clock: Clock | None = None) -> Callable[[FastAPI, Authenticate], None]:
    """Con `verifier` (el del staff, spec §8) la API verifica solo con esas claves; sin él usa el
    `authenticate` de M9. El verificador solo comprueba la firma, así que la vigencia (`exp <= now`, como la
    puerta de M9) la comprueba esta API con `clock`, obligatorio junto al verificador: falla cerrado."""
    if verifier is not None and clock is None:
        raise ValueError("un verificador del staff exige un Clock para comprobar la vigencia")

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
            if verifier is None:
                principal = authenticate(request, authorization)
            else:
                credential = _bearer(authorization)
                if not credential:
                    raise CredentialsInvalid("credencial ausente")
                principal = verifier.verify(credential)
                if clock is not None and principal.exp <= clock.now():
                    raise EngineError(ProblemCode.principal_expired)
            require_builder(principal)  # incluye las lecturas
            return principal

        @router.post("/proposals", **_doc(Proposal, 201, 409, 422, 429))
        def create(request: Request, body: _Create, authorization: Auth = None,
                   idempotency_key: IdemKey = None) -> Response:
            actor = who(request, authorization)
            created = service.create_proposal(actor, body.agent_id, body.origin, body.title,
                                              idempotency_key=idempotency_key)
            return _json(created, 201)

        @router.get("/proposals", **_doc(ProposalPage, 200, 422))
        def listing(
            request: Request,
            agent_id: str | None = None,
            state: ProposalState | None = None,
            created_by: str | None = None,
            limit: Annotated[int, Query(description="1 a 200; más grande se acota")] = 50,
            offset: Annotated[int, Query(ge=0)] = 0,
            authorization: Auth = None,
        ) -> Response:
            """Más recientes primero. Solo lectura, para cualquier `builder`."""
            page = service.list_proposals(who(request, authorization), agent_id=agent_id,
                                          state=None if state is None else state.value, created_by=created_by,
                                          limit=limit, offset=offset)
            return _json(page)

        @router.get("/proposals/{pid}", **_doc(ProposalDetail, 200, 404))
        def show(request: Request, pid: str, authorization: Auth = None) -> Response:
            who(request, authorization)
            return _json(service.get_proposal(pid))

        @router.put("/proposals/{pid}/draft", **_doc(Proposal, 200, 404, 409, 422))
        def draft(request: Request, pid: str, body: _Draft, authorization: Auth = None,
                  idempotency_key: IdemKey = None) -> Response:
            return _json(service.put_draft(who(request, authorization), pid, body.changes, body.expected_rev,
                                           idempotency_key=idempotency_key))

        @router.post("/proposals/{pid}/validate", **_doc(ValidationReport, 200, 404))
        def validate(request: Request, pid: str, authorization: Auth = None) -> Response:
            return _json(service.validate(who(request, authorization), pid))

        @router.post("/proposals/{pid}/freeze", **_doc(Candidate, 200, 404, 409, 422))
        def freeze(request: Request, pid: str, authorization: Auth = None,
                   idempotency_key: IdemKey = None) -> Response:
            return _json(service.freeze(who(request, authorization), pid, idempotency_key=idempotency_key))

        @router.post("/proposals/{pid}/reopen", **_doc(Proposal, 200, 404, 409))
        def reopen(request: Request, pid: str, authorization: Auth = None,
                   idempotency_key: IdemKey = None) -> Response:
            return _json(service.reopen(who(request, authorization), pid, idempotency_key=idempotency_key))

        @router.post("/proposals/{pid}/evaluate", **_doc(EvalReport, 200, 404, 409, 422, 429))
        def evaluate(request: Request, pid: str, body: _Evaluate, authorization: Auth = None,
                     idempotency_key: IdemKey = None) -> Response:
            actor = who(request, authorization)
            return _json(service.evaluate(actor, pid, body.suite_id, body.suite_version,
                                          idempotency_key=idempotency_key))

        @router.post("/proposals/{pid}/approve", **_doc(Approval, 200, 404, 409))
        def approve(request: Request, pid: str, body: _Approve, authorization: Auth = None) -> Response:
            approval = service.approve(who(request, authorization), pid, body.candidate_hash,
                                       accept_yardstick_loosened=body.accept_yardstick_loosened)
            return _json(approval)

        @router.post("/proposals/{pid}/reject", **_doc(Proposal, 200, 404, 409))
        def reject(request: Request, pid: str, body: _Reason, authorization: Auth = None) -> Response:
            return _json(service.reject(who(request, authorization), pid, body.reason,
                                        reason_code=body.reason_code))

        @router.post("/proposals/{pid}/publish", **_doc(ReleaseDetail, 200, 404, 409, 422))
        def publish(
            request: Request,
            pid: str,
            idempotency_key: Annotated[str, Header(max_length=255)],
            authorization: Auth = None,
        ) -> Response:
            return _json(service.publish(who(request, authorization), pid, idempotency_key))

        @router.post("/aliases/{agent_id}/{alias}", **_doc(AliasChange, 200, 404, 409, 422))
        def promote(
            request: Request, agent_id: str, alias: str, body: _Promote, authorization: Auth = None
        ) -> Response:
            actor = who(request, authorization)
            return _json(service.promote(actor, agent_id, alias, body.release_id, body.reason))

        @router.post("/agents/{agent_id}/pause", **_doc(PauseState, 200, 404, 409))
        def pause(request: Request, agent_id: str, body: _Reason, authorization: Auth = None) -> Response:
            """Saca al agente del directorio de `recepcion`; `prod` no cambia y los casos abiertos siguen."""
            return _json(service.pause_agent(who(request, authorization), agent_id, body.reason))

        @router.post("/agents/{agent_id}/resume", **_doc(PauseState, 200, 404, 409))
        def resume(request: Request, agent_id: str, body: _Reason, authorization: Auth = None) -> Response:
            return _json(service.resume_agent(who(request, authorization), agent_id, body.reason))

        @router.get("/agents/{agent_id}/pause", **_doc(PauseState, 200))
        def pause_state(request: Request, agent_id: str, authorization: Auth = None) -> Response:
            who(request, authorization)
            return _json(service.get_pause(agent_id))

        @router.get("/aliases/{agent_id}/{alias}", **_doc(AliasState, 200, 404))
        def alias_state(request: Request, agent_id: str, alias: str, authorization: Auth = None) -> Response:
            who(request, authorization)
            return _json(service.get_alias(agent_id, alias))

        @router.get("/versions/{kind}/{eid:path}", **_doc(list[VersionSummary], 200, 404))
        def versions(request: Request, kind: str, eid: str, authorization: Auth = None) -> Response:
            """Versiones de una entidad, de la más antigua a la más reciente (`eid` admite `/`)."""
            who(request, authorization)
            return _json(service.list_versions(kind, eid))

        @router.post("/releases/{rid}/revoke", **_doc(ReleaseDetail, 200, 404, 409))
        def revoke(request: Request, rid: str, body: _Reason, authorization: Auth = None) -> Response:
            return _json(service.revoke(who(request, authorization), rid, body.reason))

        @router.get("/releases/{rid}", **_doc(ReleaseDetail, 200, 404))
        def release(request: Request, rid: str, authorization: Auth = None) -> Response:
            who(request, authorization)
            return _json(service.get_release(rid))

        @router.get("/releases/{a}/diff/{b}", **_doc(ReleaseDiff, 200, 404))
        def diff(request: Request, a: str, b: str, authorization: Auth = None) -> Response:
            who(request, authorization)
            return _json(service.diff_releases(a, b))

        @router.get("/entities/{kind}/{eid:path}", **_doc(EntityVersion, 200, 404))
        def entity(request: Request, kind: str, eid: str, authorization: Auth = None) -> Response:
            """`eid` admite `/` (p. ej. `t/saludo`); una versión se pide con `?version=`."""
            who(request, authorization)
            return _json(service.get_entity(kind, eid, request.query_params.get("version")))

        @router.get("/runs/{run_id}/lineage", **_doc(RunLineage, 200, 404))
        def lineage(request: Request, run_id: str, authorization: Auth = None) -> Response:
            return _json(service.lineage_for_run(who(request, authorization), run_id))

        app.include_router(router)

    return install
