"""`directory/list` tool (ADR 0021, spec §4): the directory filtered for the caller, served by composition."""

from pydantic import TypeAdapter, ValidationError

from agent_core.domain import (
    DirectoryEntry,
    DirectorySnapshot,
    EntityRef,
    JsonValue,
    Locale,
    ToolDef,
    directory_hash,
    transfer_ineligibility,
)
from agent_core.ports import (
    AgentDirectory,
    AuthzPort,
    IdKind,
    IdSource,
    ToolCallContext,
    ToolExecutor,
    ToolResult,
    ToolStatus,
)

DIRECTORY_TOOL = ToolDef.model_validate({
    "id": "directory/list", "version": "1.0.0", "risk_class": "read", "min_auth_level": "anonymous",
    "idempotent": True, "source": "directory",
    "description": "Lists the specialists of a directory the current person can be transferred to.",
    "args_schema": {"type": "object",
                    "properties": {"directory": {"type": "string"}, "locale": {"type": "string"}},
                    "required": ["directory", "locale"]},
})

_LOCALE = TypeAdapter(Locale)


def _is_directory_tool(tool: EntityRef) -> bool:
    return (tool.id, tool.version) == (DIRECTORY_TOOL.id, DIRECTORY_TOOL.version)


class DirectoryToolExecutor:
    """Wraps a `ToolExecutor`: serves `directory/list` and delegates every other tool."""

    def __init__(self, inner: ToolExecutor, directory: AgentDirectory, authz: AuthzPort,
                 ids: IdSource) -> None:
        self._inner, self._directory, self._authz, self._ids = inner, directory, authz, ids

    def definition(self, tool: EntityRef) -> ToolDef:
        if _is_directory_tool(tool):
            return DIRECTORY_TOOL
        return self._inner.definition(tool)

    def execute(self, tool: EntityRef, args: dict[str, JsonValue], bound_params: dict[str, str],
                ctx: ToolCallContext, idempotency_key: str | None = None) -> ToolResult:
        if not _is_directory_tool(tool):
            return self._inner.execute(tool, args, bound_params, ctx, idempotency_key)
        call_id = self._ids.new_id(IdKind.call)
        name, locale = args.get("directory"), args.get("locale")
        if not isinstance(name, str) or not isinstance(locale, str):
            return ToolResult(status=ToolStatus.error, call_id=call_id, error="bad_args")
        try:
            _LOCALE.validate_python(locale)
        except ValidationError:
            return ToolResult(status=ToolStatus.error, call_id=call_id, error="bad_args")
        members = sorted(self._directory.members(name), key=lambda member: member[1].id)
        entries = [
            DirectoryEntry(agent_id=agent.id, release_id=release_id, summary=agent.routing.summary,
                           examples=list(agent.routing.examples), accepts=agent.accepts,
                           supported_locales=list(agent.supported_locales))
            for release_id, agent in members
            if agent.routing is not None and agent.accepts is not None
            and transfer_ineligibility(agent, ctx.principal, ctx.subject, locale) is None
            and self._authz.authorize_agent(ctx.principal, agent, ctx.subject).allowed
        ]
        snapshot = DirectorySnapshot(directory=name, hash=directory_hash((a.id, r) for r, a in members),
                                     entries=entries)
        result: dict[str, JsonValue] = {**snapshot.model_dump(mode="json"), "choices": list(snapshot.choices)}
        return ToolResult(status=ToolStatus.ok, result_full=result, source="directory", call_id=call_id)
