"""Doble de `SuggesterPort` (M2 §3.8, ADR 0026): resultados de `suggest` guionados en orden."""

from collections import deque
from collections.abc import Sequence
from typing import TYPE_CHECKING

from agent_core.domain import RunState
from agent_core.interpreter import SuggestRequest, SuggestResult


class ScriptedSuggester:
    def __init__(self, script: Sequence[SuggestResult] = ()) -> None:
        self._script = deque(script)
        self.calls: list[SuggestRequest] = []

    def push(self, *results: SuggestResult) -> None:
        self._script.extend(results)

    def suggest(self, request: SuggestRequest, state: RunState) -> SuggestResult:
        self.calls.append(request)
        if not self._script:
            raise AssertionError("ScriptedSuggester sin resultado guionado")
        return self._script.popleft()


if TYPE_CHECKING:
    from agent_core.interpreter import SuggesterPort

    def _conforms(x: ScriptedSuggester) -> SuggesterPort:
        return x
