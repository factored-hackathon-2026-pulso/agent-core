from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import pytest

from agent_core.domain import EngineEvent, Outcome, RunClosed, RunClosedPayload
from agent_core.ports import ToolExecutor
from agent_core.registry.evaluation.evaluator import ScenarioEvaluator
from agent_core.registry.evaluation.local_sandbox import LocalSandbox
from agent_core.registry.evaluation.ports import EvalRequest, EvalTarget, HarnessUnavailable, SandboxHandle
from agent_core.registry.evaluation.report import EvalReport
from agent_core.registry.evaluation.scoring import PLATFORM_GUARDRAILS
from agent_core.registry.evaluation.yardstick import Yardstick
from agent_core.registry.snapshot import SnapshotRegistry
from agent_core.registry.suite import EvalSuite, SandboxSeed, Scenario
from testing.builders import NOW
from testing.fakes.ids import FakeIds
from tests.registry.eval_support import metric, thr
from tests.registry.helpers import demo_pinned, suite_content


def _closed(outcome: str) -> list[EngineEvent]:
    return [RunClosed(event_id="e", run_id="r", release="x", ts=NOW,
                      payload=RunClosedPayload(outcome=Outcome(outcome), closed_by="flow"))]


@dataclass
class FakeHarness:
    outcomes: dict[str, str]  # label -> outcome
    fail: bool = False
    seen: list[tuple[str, str, ToolExecutor]] = field(default_factory=list)

    def run(self, target: EvalTarget, agent_id: str, scenario: Scenario,
            tools: ToolExecutor) -> list[EngineEvent]:
        assert getattr(tools, "is_sandbox", False)
        self.seen.append((target.label, scenario.id, tools))
        if self.fail:
            raise HarnessUnavailable("gateway down")
        return _closed(self.outcomes[target.label])


def _targets() -> tuple[EvalTarget, EvalTarget]:
    pinned = demo_pinned()
    reg = SnapshotRegistry(pinned.release, pinned.entities)
    return EvalTarget("candidate", pinned.release, reg), EvalTarget("base", pinned.release, reg)


def _suite(version: str = "1.0.0", **over: Any) -> EvalSuite:
    content = suite_content(version, **over)  # 1 scenario, 2 repetitions, expects resolved
    # The fake harness only emits RunClosed: a verified action cannot be required.
    content["scenarios"][0]["expect"].pop("actions_verified")
    return EvalSuite.model_validate(content)


SUITE = _suite()
SAME = Yardstick(metrics=[], suite=SUITE)


def _request(*, base: bool = True, old: Yardstick = SAME, new: Yardstick = SAME) -> EvalRequest:
    cand, base_target = _targets()
    return EvalRequest(candidate=cand, new=new, base=base_target if base else None,
                       old=old if base else None)


def _failed(report: EvalReport) -> list[str]:
    return sorted(i.metric_id for i in report.items if not i.passed)


def test_candidate_better_than_base_passes_and_shares_the_candidate_run() -> None:
    harness = FakeHarness({"candidate": "resolved", "base": "failed"})
    report = ScenarioEvaluator(harness, LocalSandbox(FakeIds())).run(_request())
    assert report.verdict == "pass"
    assert len(report.results) == 4 and {r.run for r in report.results} == {"base_on_old", "cand_on_new"}
    assert report.runs is not None and report.runs.cand_on_old == report.runs.cand_on_new


def test_candidate_worse_than_base_fails() -> None:
    report = ScenarioEvaluator(FakeHarness({"candidate": "failed", "base": "resolved"}),
                               LocalSandbox(FakeIds())).run(_request())
    assert report.verdict == "fail" and _failed(report) == ["scenario/resuelto"]


def test_each_run_gets_its_own_sandbox() -> None:  # T-REG-23
    harness = FakeHarness({"candidate": "resolved", "base": "resolved"})
    ScenarioEvaluator(harness, LocalSandbox(FakeIds()), max_workers=1).run(_request())
    assert len({id(tools) for _, _, tools in harness.seen}) == 4  # still referenced in `seen`


def test_infra_failure_is_failed_infra() -> None:  # T-REG-11
    report = ScenarioEvaluator(FakeHarness({}, fail=True), LocalSandbox(FakeIds())).run(_request())
    assert report.verdict == "failed_infra" and report.items == [] and report.runs is None


class NotSandbox:
    def provision(self, seed: SandboxSeed, target: EvalTarget) -> SandboxHandle:
        return SandboxHandle("h")

    def tools(self, handle: SandboxHandle) -> ToolExecutor:
        return object()  # type: ignore[return-value]

    def teardown(self, handle: SandboxHandle) -> None:
        pass


def test_refuses_non_sandbox_tools() -> None:  # T-REG-24
    harness = FakeHarness({"candidate": "resolved", "base": "resolved"})
    with pytest.raises(PermissionError):
        ScenarioEvaluator(harness, NotSandbox()).run(_request())
    assert harness.seen == []


def test_without_base_only_the_new_yardstick_runs() -> None:
    report = ScenarioEvaluator(FakeHarness({"candidate": "resolved"}), LocalSandbox(FakeIds())).run(
        _request(base=False))
    assert report.verdict == "pass" and len(report.results) == 2
    assert report.runs is not None and report.runs.base_on_old is None


def test_infra_failure_stops_queued_jobs() -> None:
    harness = FakeHarness({}, fail=True)
    report = ScenarioEvaluator(harness, LocalSandbox(FakeIds()), max_workers=1).run(_request())
    assert report.verdict == "failed_infra" and len(harness.seen) == 1


def test_a_different_old_suite_gets_its_own_candidate_run() -> None:
    old = Yardstick(metrics=[], suite=_suite("0.9.0"))
    harness = FakeHarness({"candidate": "resolved", "base": "resolved"})
    report = ScenarioEvaluator(harness, LocalSandbox(FakeIds())).run(_request(old=old))
    assert report.verdict == "pass" and len(report.results) == 6
    assert {r.run for r in report.results} == {"base_on_old", "cand_on_old", "cand_on_new"}


def test_agent_metrics_are_computed_from_the_run_events() -> None:
    resolved = metric("resueltos", event="engine.run_closed",
                      where=[{"field": "outcome", "op": "eq", "value": "resolved"}])

    def verdict(floor: str) -> str:
        new = Yardstick(metrics=[resolved], suite=_suite(thresholds={"resueltos": thr("0", floor)}))
        report = ScenarioEvaluator(FakeHarness({"candidate": "resolved"}), LocalSandbox(FakeIds())).run(
            _request(base=False, new=new))
        item = next(i for i in report.items if i.metric_id == "resueltos")
        assert item.value == Decimal(2) and item.floor == Decimal(floor)  # 2 resolved repetitions
        return report.verdict

    assert verdict("2") == "pass" and verdict("3") == "fail"


def test_platform_guardrails_are_measured_on_every_run() -> None:
    report = ScenarioEvaluator(FakeHarness({"candidate": "resolved", "base": "resolved"}),
                               LocalSandbox(FakeIds())).run(_request())
    assert report.runs is not None and report.runs.base_on_old is not None
    for measured in (report.runs.base_on_old, report.runs.cand_on_new):
        assert all(measured.metrics[k] == Decimal(0) for k in PLATFORM_GUARDRAILS)
