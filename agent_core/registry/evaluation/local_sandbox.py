"""`LocalSandbox` (spec §6.3, respaldo de la entrega): respuestas sembradas por tool, aisladas por corrida."""

from collections import deque

from agent_core.domain import EntityRef, JsonValue, ToolDef
from agent_core.ports import IdKind, IdSource, ToolCallContext, ToolResult, ToolStatus
from agent_core.registry.evaluation.ports import EvalTarget, SandboxHandle
from agent_core.registry.suite import SandboxSeed, ToolReply


class LocalSandboxTools:
    is_sandbox = True

    def __init__(self, seed: SandboxSeed, target: EvalTarget, ids: IdSource) -> None:
        self._queues = {tool: deque(replies) for tool, replies in seed.tools.items()}
        self._target, self._ids = target, ids
        self._writes: dict[tuple[str, str], ToolReply] = {}

    def execute(self, tool: EntityRef, args: dict[str, JsonValue], bound_params: dict[str, str],
                ctx: ToolCallContext, idempotency_key: str | None = None) -> ToolResult:
        call_id = self._ids.new_id(IdKind.call)
        if idempotency_key is not None and (tool.id, idempotency_key) in self._writes:
            reply = self._writes[(tool.id, idempotency_key)]
        else:
            queue = self._queues.get(tool.id)
            if not queue:
                return ToolResult(status=ToolStatus("error"), call_id=call_id, error="sin respuesta sembrada")
            reply = queue.popleft() if len(queue) > 1 else queue[0]
            if idempotency_key is not None and reply.status == ToolStatus("ok"):
                self._writes[(tool.id, idempotency_key)] = reply
        return ToolResult(status=reply.status, result_full=reply.result, error=reply.error, call_id=call_id,
                          source="sandbox")

    def definition(self, tool: EntityRef) -> ToolDef:
        return self._target.registry.get(tool, ToolDef)


class LocalSandbox:
    def __init__(self, ids: IdSource) -> None:
        self._ids = ids
        self._tools: dict[str, LocalSandboxTools] = {}

    def provision(self, seed: SandboxSeed, target: EvalTarget) -> SandboxHandle:
        handle = SandboxHandle(self._ids.new_id(IdKind.run))
        self._tools[handle.handle_id] = LocalSandboxTools(seed, target, self._ids)
        return handle

    def tools(self, handle: SandboxHandle) -> LocalSandboxTools:
        return self._tools[handle.handle_id]

    def teardown(self, handle: SandboxHandle) -> None:
        self._tools.pop(handle.handle_id, None)
