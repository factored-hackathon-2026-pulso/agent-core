"""Adaptador de M8 hacia el `ResponderPort` de M2 (m08 §2, m02 D6). Vive en la raíz de composición."""

from collections.abc import Callable
from decimal import Decimal

from agent_core.domain import (
    EngineEvent,
    EscalationRequest,
    LlmUsage,
    ResponseEmitted,
    ResponseFailed,
    RunState,
)
from agent_core.interpreter import GenerateRequest, GenerateResult
from agent_core.response import Responder, ResponderContext

ContextFactory = Callable[[GenerateRequest, RunState], ResponderContext]


def _llm(events: list[EngineEvent]) -> LlmUsage | None:
    """Uso del LLM de la cadena: `llm` de `response_emitted` o de `response_failed`."""
    for event in events:
        if isinstance(event, ResponseEmitted | ResponseFailed):
            return event.payload.llm
    return None


class ResponderAdapter:
    """`ResponderPort` sobre `Responder`. `context_for` arma por turno el `ResponderContext`
    (gateway, `resolve_ref`, validación, hechos en vista `model`); M8 no conoce `StepContext`."""

    def __init__(self, responder: Responder, context_for: ContextFactory) -> None:
        self._responder = responder
        self._context_for = context_for

    def generate(self, request: GenerateRequest, state: RunState) -> GenerateResult:
        ctx = self._context_for(request, state)
        outcome, rejected, events = self._responder.generate(request.config, state, ctx)
        llm = _llm(events)
        calls, tokens, cost = (0, 0, Decimal(0)) if llm is None else (
            llm.calls, llm.tokens_in + llm.tokens_out, llm.cost_usd)
        message = None if isinstance(outcome, EscalationRequest) else outcome
        escalation = outcome if isinstance(outcome, EscalationRequest) else None
        return GenerateResult(message=message, escalation=escalation, rejected=rejected, events=events,
                              model_calls=calls, tokens=tokens, cost_usd=cost)
