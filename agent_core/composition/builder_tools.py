"""`BuilderToolExecutor`: las tools del agente constructor sobre `RegistryService` (ADR 0019 §4; spec
write-draft §8).

Decide por su propia credencial (rol `constructor`, sin `attrs.actor = "human"`), nunca por la del run: el
principal del run solo viaja como `AuditContext`. No existe ninguna tool de aprobar, publicar, promover ni
revocar: publicar es un gate humano externo (ADR 0006)."""

from collections.abc import Callable, Mapping
from types import MappingProxyType
from typing import Any, cast

from pydantic import ValidationError

from agent_core.domain import EntityRef, JsonValue, Principal, ToolDef
from agent_core.flows import DRAFT_OUTPUT_SCHEMA
from agent_core.ports import IdKind, IdSource, ToolCallContext, ToolResult, ToolStatus
from agent_core.registry import (
    AuditContext,
    EntityDraft,
    Origin,
    Proposal,
    RegistryError,
    RegistryErrorCode,
    RegistryService,
)

Args = dict[str, JsonValue]
Handler = Callable[[Args, str | None, AuditContext | None], JsonValue]

_STR: dict[str, JsonValue] = {"type": "string"}
_CHANGES = cast(dict[str, JsonValue], DRAFT_OUTPUT_SCHEMA["properties"])["changes"]


def _schema(required: list[str], properties: dict[str, JsonValue]) -> dict[str, JsonValue]:
    return {"type": "object", "additionalProperties": False, "required": list(required),
            "properties": properties}


_PROPOSAL = _schema(["proposal_id"], {"proposal_id": _STR})


def _tool(name: str, risk: str, description: str, schema: dict[str, JsonValue]) -> ToolDef:
    extra: dict[str, Any] = {"readback_by": "idempotency_key"} if risk == "write_draft" else {}
    return ToolDef.model_validate({
        "id": f"registry/{name}", "version": "1.0.0", "risk_class": risk, "min_auth_level": "session",
        "idempotent": True, "description": description, "args_schema": schema, **extra})


BUILDER_TOOL_DEFS: Mapping[str, ToolDef] = MappingProxyType({d.id: d for d in (
    _tool("create_proposal", "write_draft", "Crea una propuesta de cambio en el registry (borrador).",
          _schema(["agent_id", "origin", "title"], {
              "agent_id": _STR, "title": _STR,
              "origin": {"type": "string", "enum": ["manual", "builder_chat", "auto_detect", "import"]}})),
    _tool("put_draft", "write_draft", "Reemplaza los cambios del borrador de una propuesta.",
          _schema(["proposal_id", "expected_rev", "changes"], {
              "proposal_id": _STR, "expected_rev": {"type": "integer"}, "changes": _CHANGES})),
    _tool("freeze", "write_draft", "Congela la propuesta y arma la candidata.", _PROPOSAL),
    _tool("reopen", "write_draft", "Reabre una propuesta congelada para seguir editando.", _PROPOSAL),
    _tool("evaluate", "write_draft", "Evalúa la candidata de la propuesta contra una suite.",
          _schema(["proposal_id", "suite_id"],
                  {"proposal_id": _STR, "suite_id": _STR, "suite_version": _STR})),
    _tool("validate", "compute", "Valida el borrador sin congelarlo.", _PROPOSAL),
    _tool("get_proposal", "read", "Lee una propuesta, sus cambios y su última evaluación.", _PROPOSAL),
    _tool("get_entity", "read", "Lee una versión de una entidad del registry.",
          _schema(["kind", "entity_id"], {"kind": _STR, "entity_id": _STR, "version": _STR})),
    _tool("list_versions", "read", "Lista las versiones de una entidad del registry.",
          _schema(["kind", "entity_id"], {"kind": _STR, "entity_id": _STR})),
    _tool("get_write", "read", "Consulta una escritura del constructor por su clave de idempotencia.",
          _schema(["idempotency_key"], {"idempotency_key": _STR})),
)})

# Bloqueos de política antes de cualquier efecto (ADR 0007 §6: `denied`).
_DENIED = frozenset({RegistryErrorCode.forbidden_role, RegistryErrorCode.step_up_required,
                     RegistryErrorCode.quota_exceeded})


def _failure(definition: ToolDef, code: RegistryErrorCode) -> ToolStatus:
    if code in _DENIED:
        return ToolStatus.denied
    return ToolStatus.uncertain if definition.is_write else ToolStatus.error


def _text(args: Args, name: str) -> str:
    value = args[name]
    if not isinstance(value, str):
        raise TypeError(name)
    return value


def _optional_text(args: Args, name: str) -> str | None:
    value = args.get(name)
    if value is not None and not isinstance(value, str):
        raise TypeError(name)
    return value


def _number(args: Args, name: str) -> int:
    value = args[name]
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(name)
    return value


def _proposal(p: Proposal) -> dict[str, JsonValue]:
    return {"proposal_id": p.proposal_id, "rev": p.rev, "state": p.state.value,
            "base_release_id": p.base_release_id}


class BuilderToolExecutor:
    def __init__(self, service: RegistryService, actor: Principal, ids: IdSource) -> None:
        self._service, self._actor, self._ids = service, actor, ids
        self._handlers: Mapping[str, Handler] = MappingProxyType({
            "registry/create_proposal": self._create_proposal, "registry/put_draft": self._put_draft,
            "registry/freeze": self._freeze, "registry/reopen": self._reopen,
            "registry/evaluate": self._evaluate, "registry/validate": self._validate,
            "registry/get_proposal": self._get_proposal, "registry/get_entity": self._get_entity,
            "registry/list_versions": self._list_versions, "registry/get_write": self._get_write})

    def definition(self, tool: EntityRef) -> ToolDef:
        found = BUILDER_TOOL_DEFS.get(tool.id)
        if found is None or found.version != tool.version:
            raise KeyError(str(tool))
        return found

    def execute(self, tool: EntityRef, args: dict[str, JsonValue], bound_params: dict[str, str],
                ctx: ToolCallContext, idempotency_key: str | None = None) -> ToolResult:
        definition = self.definition(tool)
        if definition.is_write and idempotency_key is None:
            raise ValueError("una escritura siempre lleva idempotency_key (ADR 0007)")
        call_id = self._ids.new_id(IdKind.call)
        try:
            audit = self._audit(ctx)
            value = self._handlers[tool.id](args, idempotency_key, audit)
        except RegistryError as exc:
            return ToolResult(status=_failure(definition, exc.code), call_id=call_id, error=exc.code.value)
        except (ValidationError, KeyError, TypeError, ValueError):  # argumentos mal formados: no hubo efecto
            return ToolResult(status=ToolStatus.denied, call_id=call_id, error="invalid_args")
        return ToolResult(status=ToolStatus.ok, result_full=value, call_id=call_id)

    @staticmethod
    def _audit(ctx: ToolCallContext) -> AuditContext | None:
        """El principal del run viaja solo como actor de auditoría, nunca como fuente de permisos."""
        who = ctx.principal
        if who.id is None:
            return None
        return AuditContext(run_id=ctx.run_id, on_behalf_of=f"{who.type.value}:{who.id}")

    def _create_proposal(self, args: Args, key: str | None, audit: AuditContext | None) -> JsonValue:
        p = self._service.create_proposal(self._actor, _text(args, "agent_id"), Origin(_text(args, "origin")),
                                          _text(args, "title"), idempotency_key=key, audit=audit)
        return _proposal(p)

    def _put_draft(self, args: Args, key: str | None, audit: AuditContext | None) -> JsonValue:
        raw = args["changes"]
        if not isinstance(raw, list):
            raise TypeError("changes")
        changes = [EntityDraft.model_validate(item) for item in raw]
        p = self._service.put_draft(self._actor, _text(args, "proposal_id"), changes,
                                    _number(args, "expected_rev"), idempotency_key=key, audit=audit)
        return _proposal(p)

    def _freeze(self, args: Args, key: str | None, audit: AuditContext | None) -> JsonValue:
        view = self._service.freeze(self._actor, _text(args, "proposal_id"), idempotency_key=key, audit=audit)
        return {"candidate_hash": view.candidate_hash, "release_id_preview": view.release_id_preview}

    def _reopen(self, args: Args, key: str | None, audit: AuditContext | None) -> JsonValue:
        return _proposal(self._service.reopen(self._actor, _text(args, "proposal_id"), idempotency_key=key,
                                              audit=audit))

    def _evaluate(self, args: Args, key: str | None, audit: AuditContext | None) -> JsonValue:
        report = self._service.evaluate(self._actor, _text(args, "proposal_id"), _text(args, "suite_id"),
                                        _optional_text(args, "suite_version"), idempotency_key=key,
                                        audit=audit)
        return {"verdict": report.verdict}

    def _validate(self, args: Args, key: str | None, audit: AuditContext | None) -> JsonValue:
        report = self._service.validate(self._actor, _text(args, "proposal_id"))
        shown: list[JsonValue] = [{"rule": v.rule, "node_id": v.node_id, "message": v.message}
                                  for v in report.violations[:20]]
        return {"valid": not report.violations, "candidate_hash": report.candidate_hash, "violations": shown}

    def _get_proposal(self, args: Args, key: str | None, audit: AuditContext | None) -> JsonValue:
        detail = self._service.get_proposal(_text(args, "proposal_id"))
        changes: list[JsonValue] = [{"kind": c.kind, "id": c.id} for c in detail.changes]
        return {**_proposal(detail.proposal), "candidate_hash": detail.proposal.candidate_hash,
                "changes": changes, "last_verdict": detail.last_eval.verdict if detail.last_eval else None}

    def _get_entity(self, args: Args, key: str | None, audit: AuditContext | None) -> JsonValue:
        found = self._service.get_entity(_text(args, "kind"), _text(args, "entity_id"),
                                         _optional_text(args, "version"))
        return found.model_dump(mode="json")

    def _list_versions(self, args: Args, key: str | None, audit: AuditContext | None) -> JsonValue:
        versions = self._service.list_versions(_text(args, "kind"), _text(args, "entity_id"))
        shown: list[JsonValue] = [{"version": v.ref.version, "created_by": v.created_by} for v in versions]
        return shown

    def _get_write(self, args: Args, key: str | None, audit: AuditContext | None) -> JsonValue:
        record = self._service.get_write(_text(args, "idempotency_key"))
        return None if record is None else record.model_dump(mode="json")
