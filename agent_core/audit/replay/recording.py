"""Envoltorios para `agentcore record` (M11 §3.5): capturan lo `full` y los borradores de una corrida."""

from typing import cast

from agent_core.audit.replay.fixture import Fixture, FullToolResult
from agent_core.domain import AnyEvent, EngineEvent, EntityRef, JsonValue, Locale, ToolDef
from agent_core.ports import GenerationResult, LLMGateway, ToolCallContext, ToolExecutor, ToolResult


class RecordingToolExecutor:
    def __init__(self, inner: ToolExecutor) -> None:
        self._inner = inner
        self.captured: dict[str, FullToolResult] = {}

    def execute(self, tool: EntityRef, args: dict[str, JsonValue], bound_params: dict[str, str],
                ctx: ToolCallContext, idempotency_key: str | None = None) -> ToolResult:
        result = self._inner.execute(tool, args, bound_params, ctx, idempotency_key)
        self.captured[result.call_id] = FullToolResult(
            status=result.status, result_full=result.result_full, source=result.source,
            error=result.error, required_level=result.required_level)
        return result

    def definition(self, tool: EntityRef) -> ToolDef:
        return self._inner.definition(tool)


class RecordingGateway:
    def __init__(self, inner: LLMGateway) -> None:
        self._inner = inner
        self.drafts: list[JsonValue] = []

    def generate(self, prompt: EntityRef, inputs_model_view: dict[str, JsonValue], locale: Locale,
                 schema: dict[str, JsonValue] | None = None) -> GenerationResult:
        result = self._inner.generate(prompt, inputs_model_view, locale, schema)
        self.drafts.append(result.output)
        return result


def build_fixture(name: str, run_id: str, release: str, events: list[EngineEvent],
                  inputs: list[dict[str, JsonValue]], tools: RecordingToolExecutor,
                  llm: RecordingGateway) -> Fixture:
    return Fixture(name=name, run_id=run_id, release=release, inputs=inputs,
                   events=cast("list[AnyEvent]", events),
                   full=dict(tools.captured), drafts=list(llm.drafts))
