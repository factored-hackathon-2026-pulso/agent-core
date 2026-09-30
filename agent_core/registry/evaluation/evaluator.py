"""`ScenarioEvaluator` (spec §6.2): k corridas por escenario sobre candidata y base, en sandbox."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from agent_core.domain import EngineEvent
from agent_core.registry.evaluation.gate import decide
from agent_core.registry.evaluation.ports import (
    EvalTarget,
    HarnessUnavailable,
    Judge,
    SandboxPort,
    ScenarioHarness,
    ScenarioTranscript,
)
from agent_core.registry.evaluation.report import EvalReport, ScenarioResult, SuiteMetrics
from agent_core.registry.evaluation.scoring import aggregate, score_run
from agent_core.registry.suite import EvalSuite, Scenario


@dataclass(frozen=True)
class _Job:
    target: EvalTarget
    scenario: Scenario
    repetition: int


class ScenarioEvaluator:
    def __init__(self, harness: ScenarioHarness, sandbox: SandboxPort, *, max_workers: int = 4,
                 judge: Judge | None = None) -> None:
        self._harness, self._sandbox = harness, sandbox
        self._max_workers, self._judge = max_workers, judge

    def _one(self, suite: EvalSuite, job: _Job) -> tuple[ScenarioResult, list[EngineEvent]]:
        handle = self._sandbox.provision(job.scenario.seed, job.target)
        try:
            tools = self._sandbox.tools(handle)
            if getattr(tools, "is_sandbox", False) is not True:
                raise PermissionError("el evaluador solo corre contra un sandbox")
            events = self._harness.run(job.target, suite.agent_id, job.scenario, tools)
        finally:
            self._sandbox.teardown(handle)
        score = score_run(events, job.scenario.expect, job.scenario.sensitive_values)
        return ScenarioResult(scenario_id=job.scenario.id, label=job.target.label, repetition=job.repetition,
                              score=score), events

    def run(self, suite: EvalSuite, candidate: EvalTarget, base: EvalTarget | None) -> EvalReport:
        targets = [candidate] if base is None else [candidate, base]
        jobs = [_Job(t, s, r) for t in targets for s in suite.scenarios for r in range(suite.repetitions)]
        try:
            with ThreadPoolExecutor(max_workers=self._max_workers) as pool:
                done = list(pool.map(lambda job: self._one(suite, job), jobs))
        except (HarnessUnavailable, TimeoutError) as exc:
            return EvalReport(verdict="failed_infra", detail=type(exc).__name__ + ": " + str(exc)[:200])
        results = [r for r, _ in done]

        def metrics(label: str) -> SuiteMetrics:
            return aggregate([r.score for r in results if r.label == label])

        cand_m = metrics("candidate")
        base_m = metrics("base") if base is not None else None
        verdict, checks = decide(suite, cand_m, base_m)
        notes = []
        if self._judge is not None:
            transcripts = [ScenarioTranscript(r.scenario_id, r.label, r.repetition, ev) for r, ev in done]
            notes = self._judge.score(suite, transcripts)
        return EvalReport(verdict=verdict, checks=checks, candidate=cand_m, base=base_m, results=results,
                          judge_notes=notes)
