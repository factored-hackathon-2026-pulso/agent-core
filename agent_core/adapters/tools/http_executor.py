"""`HttpToolExecutor`: `ToolExecutor` over a standalone tool service (HTTP, ADR 0025).

The registry still owns the `ToolDef` (risk, auth level, idempotence, read-back); the service only runs the
data side by tool name. The engine already verified the caller's identity, so the service receives the
verified *claims* (never a token) next to `bound_params`, which are engine-controlled; the model's `args`
never carry the subject. Nothing here raises into the engine and nothing logs args, results or claims.
"""

import logging
import os
from collections.abc import Mapping
from typing import Protocol
from urllib.parse import quote, urlsplit

import httpx

from agent_core.domain import EntityRef, JsonValue, SchemaError, ToolDef, dumps, loads
from agent_core.ports import IdKind, IdSource, RegistryPort, ToolCallContext, ToolResult, ToolStatus

_LOG = logging.getLogger("agent_core.adapters.tools")

TOOL_SERVICE_URL_ENV = "AGENTCORE_TOOL_SERVICE_URL"
TOOL_SERVICE_TOKEN_ENV = "AGENTCORE_TOOL_SERVICE_TOKEN"
TOOL_SERVICE_TIMEOUT_ENV = "AGENTCORE_TOOL_SERVICE_TIMEOUT_S"
DEFAULT_TIMEOUT_S = 10.0

_READ_STATUSES = frozenset({ToolStatus.ok, ToolStatus.error, ToolStatus.timeout, ToolStatus.denied,
                            ToolStatus.step_up_required})
_WRITE_STATUSES = frozenset({ToolStatus.ok, ToolStatus.denied, ToolStatus.uncertain,
                             ToolStatus.step_up_required})


class HttpToolExecutor:
    """Tools over HTTP; only raises for a programming error (a write without `idempotency_key`)."""

    def __init__(self, registry: RegistryPort, ids: IdSource, base_url: str, token: str, *,
                 timeout_s: float = DEFAULT_TIMEOUT_S, client: httpx.Client | None = None) -> None:
        self._registry = registry
        self._ids = ids
        self._base = base_url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        self._timeout = httpx.Timeout(timeout_s)
        self._client = client if client is not None else httpx.Client()

    def close(self) -> None:
        self._client.close()

    def definition(self, tool: EntityRef) -> ToolDef:
        return self._registry.get(tool, ToolDef)

    def execute(self, tool: EntityRef, args: dict[str, JsonValue], bound_params: dict[str, str],
                ctx: ToolCallContext, idempotency_key: str | None = None) -> ToolResult:
        tool_def = self.definition(tool)
        if tool_def.is_write and idempotency_key is None:
            raise ValueError("una escritura siempre lleva idempotency_key (ADR 0007)")
        call_id = self._ids.new_id(IdKind.call)
        if not tool_def.accepts(ctx.principal.auth, ctx.at):  # before any call: nothing can have happened
            return ToolResult(status=ToolStatus.step_up_required, source=tool_def.source, call_id=call_id,
                              required_level=tool_def.min_auth_level)
        body = {"tool": str(tool), "args": args, "bound_params": bound_params,
                "context": _context(ctx, call_id), "idempotency_key": idempotency_key}
        try:
            payload = dumps(body)
        except (TypeError, ValueError):
            raise SchemaError("los argumentos de la tool no son JSON canónico") from None
        url = f"{self._base}/v1/tools/{quote(str(tool.id), safe='')}/execute"
        try:
            response = self._client.post(url, content=payload.encode(), headers=self._headers,
                                         timeout=self._timeout)
        except httpx.TimeoutException:
            return self._failure(tool_def, call_id, "timeout", ToolStatus.timeout)
        except Exception as error:  # connection, transport or anything unforeseen; its text is never logged
            return self._failure(tool_def, call_id, type(error).__name__, ToolStatus.error)
        try:
            return self._result(response, tool_def, call_id)
        except Exception as error:  # nothing else leaves the adapter
            why = f"bad response ({type(error).__name__})"
            return self._failure(tool_def, call_id, why, ToolStatus.error)

    def _failure(self, tool_def: ToolDef, call_id: str, why: str, read_status: ToolStatus) -> ToolResult:
        """A write that failed in transit may have happened: `uncertain` (ADR 0007), never `error`."""
        status = ToolStatus.uncertain if tool_def.is_write else read_status
        _LOG.warning("tool-service call=%s tool=%s status=%s cause=%s",
                     call_id, tool_def.id, status.value, why)
        return ToolResult(status=status, source=tool_def.source, call_id=call_id,
                          error="timeout" if status is ToolStatus.timeout else "tool-service unavailable")

    def _result(self, response: httpx.Response, tool_def: ToolDef, call_id: str) -> ToolResult:
        if response.status_code in (401, 403):  # this deployment's token is wrong: not a data answer
            why = f"service rejected the caller (http {response.status_code})"
            return self._failure(tool_def, call_id, why, ToolStatus.error)
        data = loads(response.content.decode())
        if not isinstance(data, dict) or response.status_code >= 500:
            return self._failure(tool_def, call_id, f"unexpected body (http {response.status_code})",
                                 ToolStatus.error)
        raw = data.get("status")
        if not isinstance(raw, str) or raw not in ToolStatus.__members__:
            return self._failure(tool_def, call_id, "unknown status", ToolStatus.error)
        status = ToolStatus(raw)
        allowed = _WRITE_STATUSES if tool_def.is_write else _READ_STATUSES
        if status not in allowed:
            # a write that answers `error`/`timeout` did not promise it left no effect: it is `uncertain`
            why = f"status {status.value} out of contract"
            return self._failure(tool_def, call_id, why, ToolStatus.error)
        error = data.get("error")
        message = error.get("message") if isinstance(error, dict) else error
        required = tool_def.min_auth_level if status is ToolStatus.step_up_required else None
        source = data.get("source")
        return ToolResult(
            status=status, result_full=data.get("result") if status is ToolStatus.ok else None,
            source=source if isinstance(source, str) else tool_def.source, call_id=call_id,
            error=message[:200] if isinstance(message, str) and status is not ToolStatus.ok else None,
            required_level=required)


def _context(ctx: ToolCallContext, call_id: str) -> dict[str, JsonValue]:
    """The verified claims the service needs; never a token, never anything the model wrote."""
    principal = ctx.principal
    claims: dict[str, JsonValue] = {
        "run_id": ctx.run_id, "release": ctx.release, "call_id": call_id, "turn_id": ctx.turn_id,
        "principal": {"type": principal.type.value, "id": principal.id, "roles": list(principal.roles),
                      "scopes": list(principal.scopes), "attrs": dict(principal.attrs),
                      "auth_level": principal.auth.level.value},
        "subject": {"kind": ctx.subject.kind, "ref": ctx.subject.ref} if ctx.subject else None,
        "on_behalf_of": None}
    if ctx.on_behalf_of is not None:
        grant = ctx.on_behalf_of
        claims["on_behalf_of"] = {
            "subject": {"kind": grant.subject.kind, "ref": grant.subject.ref}, "grant_ref": grant.grant_ref,
            "grantee": {"type": grant.grantee.type.value, "id": grant.grantee.id},
            "scopes": list(grant.scopes)}
    return claims


class _Ctx(Protocol):
    registry: RegistryPort
    ids: IdSource


def http_tool_executor(ctx: _Ctx) -> HttpToolExecutor:
    """Factory for `serve --tools agent_core.adapters.tools:http_tool_executor`; configured by env, URL and
    token together or the process does not start."""
    return from_env(ctx.registry, ctx.ids, os.environ)


def from_env(registry: RegistryPort, ids: IdSource, env: Mapping[str, str]) -> HttpToolExecutor:
    url = (env.get(TOOL_SERVICE_URL_ENV) or "").strip()
    token = (env.get(TOOL_SERVICE_TOKEN_ENV) or "").strip()
    if not url or not token:
        raise SchemaError(f"{TOOL_SERVICE_URL_ENV} y {TOOL_SERVICE_TOKEN_ENV} van juntas: falta alguna")
    parsed = urlsplit(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise SchemaError(f"{TOOL_SERVICE_URL_ENV} debe ser una URL http(s) con host")
    try:
        timeout = float(env.get(TOOL_SERVICE_TIMEOUT_ENV) or DEFAULT_TIMEOUT_S)
    except ValueError:
        raise SchemaError(f"{TOOL_SERVICE_TIMEOUT_ENV} debe ser un número") from None
    if timeout <= 0:
        raise SchemaError(f"{TOOL_SERVICE_TIMEOUT_ENV} debe ser positivo")
    return HttpToolExecutor(registry, ids, url, token, timeout_s=timeout)
