"""Puertos de la evaluación (spec §6, §7.3). `ScenarioHarness` lo implementa `agent_core.composition`."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from agent_core.domain import EngineEvent, Release
from agent_core.ports import RegistryPort, ToolExecutor
from agent_core.registry.evaluation.report import EvalReport, JudgeNote, Label
from agent_core.registry.suite import EvalSuite, SandboxSeed, Scenario


@dataclass(frozen=True)
class EvalTarget:
    label: Label
    release: Release
    registry: RegistryPort


class EvalPort(Protocol):
    def run(self, suite: EvalSuite, candidate: EvalTarget, base: EvalTarget | None) -> EvalReport: ...


class HarnessUnavailable(Exception):
    """Falla de infraestructura (LLM, sandbox, tiempo). Nunca cuenta como pase ni como fallo."""


class ScenarioHarness(Protocol):
    def run(self, target: EvalTarget, agent_id: str, scenario: Scenario,
            tools: ToolExecutor) -> list[EngineEvent]:
        """Corre el escenario con el motor y devuelve los eventos del run. Lanza `HarnessUnavailable`."""
        ...


@dataclass(frozen=True)
class SandboxHandle:
    handle_id: str


class SandboxPort(Protocol):
    def provision(self, seed: SandboxSeed, target: EvalTarget) -> SandboxHandle: ...

    def tools(self, handle: SandboxHandle) -> ToolExecutor:
        """El ejecutor debe tener `is_sandbox = True` (spec §6.3)."""
        ...

    def teardown(self, handle: SandboxHandle) -> None: ...


@dataclass(frozen=True)
class ScenarioTranscript:
    scenario_id: str
    label: Label
    repetition: int
    events: Sequence[EngineEvent]


class Judge(Protocol):
    def score(self, suite: EvalSuite, transcripts: Sequence[ScenarioTranscript]) -> list[JudgeNote]: ...
