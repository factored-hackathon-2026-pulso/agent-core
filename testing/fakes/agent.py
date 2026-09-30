"""Doble de `AgentPort` (M2 §3.7, ADR 0019): pasos del nodo `agent` guionados en orden."""

from collections import deque
from collections.abc import Sequence
from typing import TYPE_CHECKING

from agent_core.domain import RunState
from agent_core.interpreter import AgentRequest, AgentStepResult


class ScriptedAgent:
    def __init__(self, script: Sequence[AgentStepResult] = ()) -> None:
        self._script = deque(script)
        self.calls: list[AgentRequest] = []

    def push(self, *results: AgentStepResult) -> None:
        self._script.extend(results)

    def step(self, request: AgentRequest, state: RunState) -> AgentStepResult:
        self.calls.append(request)
        if not self._script:
            raise AssertionError("ScriptedAgent sin paso guionado")
        return self._script.popleft()


if TYPE_CHECKING:
    from agent_core.interpreter import AgentPort

    def _conforms(x: ScriptedAgent) -> AgentPort:
        return x
