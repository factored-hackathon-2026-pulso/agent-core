from dataclasses import dataclass, field

import pytest

from agent_core.domain import EngineEvent, Outcome, RunClosed, RunClosedPayload
from agent_core.ports import ToolExecutor
from agent_core.registry.evaluation.evaluator import ScenarioEvaluator
from agent_core.registry.evaluation.local_sandbox import LocalSandbox
from agent_core.registry.evaluation.ports import EvalTarget, HarnessUnavailable, SandboxHandle
from agent_core.registry.snapshot import SnapshotRegistry
from agent_core.registry.suite import EvalSuite, SandboxSeed, Scenario
from testing.builders import NOW
from testing.fakes.ids import FakeIds
from tests.registry.helpers import AGENT, demo_pinned, suite_content


def _closed(outcome: str) -> list[EngineEvent]:
    return [RunClosed(event_id="e", run_id="r", release="x", ts=NOW,
                      payload=RunClosedPayload(outcome=Outcome(outcome), closed_by="flow"))]


@dataclass
class FakeHarness:
    outcomes: dict[str, str]  # label -> outcome
    fail: bool = False
    seen: list[tuple[str, str, int]] = field(default_factory=list)

    def run(self, target: EvalTarget, agent_id: str, scenario: Scenario,
            tools: ToolExecutor) -> list[EngineEvent]:
        assert getattr(tools, "is_sandbox", False)
        self.seen.append((target.label, scenario.id, id(tools)))
        if self.fail:
            raise HarnessUnavailable("gateway caído")
        return _closed(self.outcomes[target.label])


def _targets() -> tuple[EvalTarget, EvalTarget]:
    pinned = demo_pinned()
    reg = SnapshotRegistry(pinned.release, pinned.entities)
    return EvalTarget("candidate", pinned.release, reg), EvalTarget("base", pinned.release, reg)


def _suite() -> EvalSuite:
    content = suite_content()  # 1 escenario, 2 repeticiones, espera resolved
    # El harness falso solo emite RunClosed: no se exige una acción verificada.
    content["scenarios"][0]["expect"].pop("actions_verified")
    return EvalSuite.model_validate(content)


SUITE = _suite()


def test_candidate_better_than_base_passes() -> None:
    cand, base = _targets()
    harness = FakeHarness({"candidate": "resolved", "base": "failed"})
    report = ScenarioEvaluator(harness, LocalSandbox(FakeIds())).run(SUITE, cand, base)
    assert report.verdict == "pass"
    assert report.candidate is not None and str(report.candidate.primary) == "1.0000"
    assert len(report.results) == 4


def test_candidate_worse_than_base_fails() -> None:
    cand, base = _targets()
    report = ScenarioEvaluator(FakeHarness({"candidate": "failed", "base": "resolved"}),
                               LocalSandbox(FakeIds())).run(SUITE, cand, base)
    assert report.verdict == "fail"


def test_each_run_gets_its_own_sandbox() -> None:  # T-REG-23
    cand, base = _targets()
    harness = FakeHarness({"candidate": "resolved", "base": "resolved"})
    ScenarioEvaluator(harness, LocalSandbox(FakeIds()), max_workers=1).run(SUITE, cand, base)
    assert len({tools_id for _, _, tools_id in harness.seen}) == 4


def test_infra_failure_is_failed_infra() -> None:  # T-REG-11
    cand, base = _targets()
    report = ScenarioEvaluator(FakeHarness({}, fail=True), LocalSandbox(FakeIds())).run(SUITE, cand, base)
    assert report.verdict == "failed_infra" and report.checks == []


class NotSandbox:
    def provision(self, seed: SandboxSeed, target: EvalTarget) -> SandboxHandle:
        return SandboxHandle("h")

    def tools(self, handle: SandboxHandle) -> ToolExecutor:
        return object()  # type: ignore[return-value]

    def teardown(self, handle: SandboxHandle) -> None:
        pass


def test_refuses_non_sandbox_tools() -> None:  # T-REG-24
    cand, base = _targets()
    harness = FakeHarness({"candidate": "resolved", "base": "resolved"})
    with pytest.raises(PermissionError):
        ScenarioEvaluator(harness, NotSandbox()).run(SUITE, cand, base)
    assert harness.seen == []


def test_without_base_compares_to_floor() -> None:
    cand, _ = _targets()
    harness = FakeHarness({"candidate": "resolved"})
    report = ScenarioEvaluator(harness, LocalSandbox(FakeIds())).run(SUITE, cand, None)
    assert report.verdict == "pass" and report.base is None
    assert AGENT == SUITE.agent_id
