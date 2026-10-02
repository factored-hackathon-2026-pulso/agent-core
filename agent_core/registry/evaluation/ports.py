"""Evaluation ports (registry sections 6 and 7.3; ADR 0020). `ScenarioHarness` is implemented by
`agent_core.composition`."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from agent_core.domain import EngineEvent, Release
from agent_core.ports import RegistryPort, ToolExecutor
from agent_core.registry.evaluation.report import EvalReport, JudgeNote, Label
from agent_core.registry.evaluation.yardstick import Yardstick
from agent_core.registry.suite import EvalSuite, SandboxSeed, Scenario


@dataclass(frozen=True)
class EvalTarget:
    label: Label
    release: Release
    registry: RegistryPort


@dataclass(frozen=True)
class EvalRequest:
    """What a double-yardstick evaluation measures (evaluation spec section 6.1).

    `new` is the candidate's yardstick (its metrics and the chosen suite). `old` is the base release's
    yardstick as published; with `old.suite = None` (the base recorded no suite, D3) only the floors of
    `new` apply."""

    candidate: EvalTarget
    new: Yardstick
    base: EvalTarget | None = None
    old: Yardstick | None = None


class EvalPort(Protocol):
    def run(self, request: EvalRequest) -> EvalReport: ...


class HarnessUnavailable(Exception):
    """Infrastructure failure (LLM, sandbox, time). Never counts as a pass or as a failure."""


class ScenarioHarness(Protocol):
    def run(self, target: EvalTarget, agent_id: str, scenario: Scenario,
            tools: ToolExecutor) -> list[EngineEvent]:
        """Run the scenario with the engine and return the run's events. Raises `HarnessUnavailable`."""
        ...


@dataclass(frozen=True)
class SandboxHandle:
    handle_id: str


class SandboxPort(Protocol):
    def provision(self, seed: SandboxSeed, target: EvalTarget) -> SandboxHandle: ...

    def tools(self, handle: SandboxHandle) -> ToolExecutor:
        """The executor must have `is_sandbox = True` (registry section 6.3)."""
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
