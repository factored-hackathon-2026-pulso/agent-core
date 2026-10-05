"""Adaptador de M8 hacia el `SuggesterPort` de M2 (ADR 0026). Vive en la raíz de composición."""

from collections.abc import Callable
from decimal import Decimal

from agent_core.domain import RunState
from agent_core.interpreter import SuggestRequest, SuggestResult
from agent_core.response import Suggester, SuggesterContext

ContextFactory = Callable[[SuggestRequest, RunState], SuggesterContext]


class SuggesterAdapter:
    """`SuggesterPort` sobre `Suggester`. `context_for` arma por nodo el `SuggesterContext` (gateway, catálogo
    de tools, hechos citables, validación y lo que ve el modelo); M8 no conoce `StepContext`."""

    def __init__(self, suggester: Suggester, context_for: ContextFactory) -> None:
        self._suggester = suggester
        self._context_for = context_for

    def suggest(self, request: SuggestRequest, state: RunState) -> SuggestResult:
        outcome = self._suggester.generate(self._context_for(request, state))
        llm = outcome.llm
        calls, tokens, cost = (0, 0, Decimal(0)) if llm is None else (
            llm.calls, llm.tokens_in + llm.tokens_out, llm.cost_usd)
        return SuggestResult(
            suggestions=outcome.suggestions, failures=outcome.failures, regenerations=outcome.regenerations,
            llm=llm, model_calls=calls, tokens=tokens, cost_usd=cost)
