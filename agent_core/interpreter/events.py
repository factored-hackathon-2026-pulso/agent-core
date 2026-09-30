"""Eventos que emite M2 (índice §6; payloads en M0 §2.10). `seq`/`hash` los asigna M11."""

from agent_core.domain import (
    AccessDenied,
    AccessDeniedPayload,
    AccessDeniedReason,
    AuthLevel,
    EntityRef,
    JsonValue,
    NodeEntered,
    NodeEnteredPayload,
    ResponseEmitted,
    ResponseEmittedPayload,
    RuleEvaluated,
    RuleEvaluatedPayload,
    RunState,
    StepUpRequested,
    StepUpRequestedPayload,
    ToolCalled,
    ToolCalledPayload,
    ValidatorOutcome,
)
from agent_core.interpreter.context import ResumeKind, StepContext
from agent_core.ports import IdKind


class Events:
    """Arma la envoltura común con los puertos del contexto."""

    def __init__(self, ctx: StepContext) -> None:
        self._ctx = ctx

    def _envelope(self, state: RunState) -> dict[str, object]:
        return {
            "event_id": self._ctx.ids.new_id(IdKind.event),
            "run_id": state.run_id,
            "turn_id": self._ctx.turn_id,
            "session_id": state.session_id,
            "release": state.release,
            "ts": self._ctx.clock.now(),
        }

    def node_entered(self, state: RunState, flow: EntityRef, node_id: str, node_type: str,
                     resume_kind: ResumeKind) -> NodeEntered:
        payload = NodeEnteredPayload(flow=flow, node_id=node_id, node_type=node_type, resume_kind=resume_kind)
        return NodeEntered.model_validate({**self._envelope(state), "payload": payload})

    def rule_evaluated(self, state: RunState, node_id: str, policy: EntityRef | None,
                       inputs: dict[str, JsonValue], result: bool) -> RuleEvaluated:
        payload = RuleEvaluatedPayload(node_id=node_id, policy=policy, inputs=inputs, result=result)
        return RuleEvaluated.model_validate({**self._envelope(state), "payload": payload})

    def tool_called(self, state: RunState, payload: ToolCalledPayload) -> ToolCalled:
        return ToolCalled.model_validate({**self._envelope(state), "payload": payload})

    def step_up_requested(self, state: RunState, node_id: str, required_level: AuthLevel,
                          attempt: int) -> StepUpRequested:
        payload = StepUpRequestedPayload(node_id=node_id, required_level=required_level, attempt=attempt)
        return StepUpRequested.model_validate({**self._envelope(state), "payload": payload})

    def access_denied(self, state: RunState, tool: EntityRef) -> AccessDenied:
        payload = AccessDeniedPayload(reason=AccessDeniedReason.tool_denied, tool=tool)
        return AccessDenied.model_validate({**self._envelope(state), "payload": payload})

    def response_emitted_template(self, state: RunState, node_id: str, claims: frozenset[str],
                                  *, fallback_used: bool) -> ResponseEmitted:
        """D6: una plantilla no pasa por el validador ni el LLM; `transcript_fp` lo rellena M4."""
        payload = ResponseEmittedPayload(
            node_id=node_id, kind="template", validator=ValidatorOutcome(ok=True),
            fallback_used=fallback_used, claims=sorted(claims), transcript_fp=None, llm=None)
        return ResponseEmitted.model_validate({**self._envelope(state), "payload": payload})
