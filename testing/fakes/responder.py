"""Doble de `ResponderPort` (M2): resultados de `generate` guionados en orden."""

from collections import deque
from collections.abc import Sequence
from typing import TYPE_CHECKING

from agent_core.domain import RunState
from agent_core.interpreter import GenerateRequest, GenerateResult


class ScriptedResponder:
    def __init__(self, script: Sequence[GenerateResult] = ()) -> None:
        self._script = deque(script)
        self.calls: list[GenerateRequest] = []

    def push(self, *results: GenerateResult) -> None:
        self._script.extend(results)

    def generate(self, request: GenerateRequest, state: RunState) -> GenerateResult:
        self.calls.append(request)
        if not self._script:
            raise AssertionError("ScriptedResponder sin resultado guionado")
        return self._script.popleft()


if TYPE_CHECKING:
    from agent_core.interpreter import ResponderPort

    def _conforms(x: ScriptedResponder) -> ResponderPort:
        return x
