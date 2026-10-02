"""`ScenarioEvaluator` (registry section 6.2; evaluation spec section 6.1): runs the yardsticks in a sandbox
and decides the gate.

It runs the new yardstick on the candidate and, if the base recorded its suite, the old yardstick on the base
and on the candidate; if the old suite equals the new one, the candidate runs once and that run is measured
with each yardstick's definitions. Each scenario runs `repetitions_of` times, each in its own sandbox."""

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass

from agent_core.domain import EngineEvent, MetricDef
from agent_core.registry.evaluation.gate import evaluate_gate
from agent_core.registry.evaluation.ports import (
    EvalRequest,
    EvalTarget,
    HarnessUnavailable,
    Judge,
    SandboxPort,
    ScenarioHarness,
    ScenarioTranscript,
)
from agent_core.registry.evaluation.report import (
    EvalReport,
    GateRuns,
    JudgeNote,
    RunName,
    ScenarioResult,
    SuiteMeasurement,
)
from agent_core.registry.evaluation.scoring import measure, score_run
from agent_core.registry.evaluation.yardstick import Yardstick
from agent_core.registry.suite import EvalSuite, Scenario

Plan = list[tuple[RunName, EvalTarget, EvalSuite]]


@dataclass(frozen=True)
class _Job:
    run: RunName
    target: EvalTarget
    suite: EvalSuite
    scenario: Scenario
    repetition: int


def _old_yardstick(request: EvalRequest) -> Yardstick | None:
    """The old yardstick that can run: needs a base and that the base recorded its suite."""
    old = request.old
    if request.base is None or old is None or old.suite is None:
        return None
    return old


class ScenarioEvaluator:
    def __init__(self, harness: ScenarioHarness, sandbox: SandboxPort, *, max_workers: int = 4,
                 judge: Judge | None = None) -> None:
        self._harness, self._sandbox = harness, sandbox
        self._max_workers, self._judge = max_workers, judge

    def _one(self, job: _Job) -> tuple[ScenarioResult, list[EngineEvent]]:
        handle = self._sandbox.provision(job.scenario.seed, job.target)
        try:
            tools = self._sandbox.tools(handle)
            if getattr(tools, "is_sandbox", False) is not True:
                raise PermissionError("the evaluator only runs against a sandbox")
            events = self._harness.run(job.target, job.suite.agent_id, job.scenario, tools)
        finally:
            self._sandbox.teardown(handle)
        score = score_run(events, job.scenario.expect, job.scenario.sensitive_values,
                          job.scenario.assertions)
        return ScenarioResult(scenario_id=job.scenario.id, label=job.target.label, run=job.run,
                              repetition=job.repetition, score=score), events

    @staticmethod
    def _plan(request: EvalRequest, new_suite: EvalSuite) -> tuple[Plan, bool]:
        plan: Plan = [("cand_on_new", request.candidate, new_suite)]
        old = _old_yardstick(request)
        shared = old is not None and old.suite == new_suite
        if old is not None and old.suite is not None and request.base is not None:
            plan.append(("base_on_old", request.base, old.suite))
            if not shared:
                plan.append(("cand_on_old", request.candidate, old.suite))
        return plan, shared

    def run(self, request: EvalRequest) -> EvalReport:
        new_suite = request.new.suite
        if new_suite is None:
            raise ValueError("the new yardstick needs its suite")
        plan, shared = self._plan(request, new_suite)
        jobs = [_Job(name, target, suite, scenario, r) for name, target, suite in plan
                for scenario in suite.scripted() for r in range(suite.repetitions_of(scenario))]
        pool = ThreadPoolExecutor(max_workers=self._max_workers)
        try:
            futures = [pool.submit(self._one, job) for job in jobs]
            try:
                for future in as_completed(futures):
                    future.result()
            except (HarnessUnavailable, TimeoutError) as exc:
                pool.shutdown(wait=True, cancel_futures=True)
                return EvalReport(verdict="failed_infra", detail=type(exc).__name__ + ": " + str(exc)[:200])
            done = [f.result() for f in futures]  # job order, not completion order
        finally:
            pool.shutdown(wait=True, cancel_futures=True)

        def measured(name: RunName, suite: EvalSuite, metrics: list[MetricDef]) -> SuiteMeasurement:
            return measure(suite, metrics, [(r.scenario_id, r.score, ev) for r, ev in done if r.run == name])

        cand_on_new = measured("cand_on_new", new_suite, request.new.metrics)
        runs = GateRuns(cand_on_new=cand_on_new)
        old = _old_yardstick(request)
        if old is not None and old.suite is not None:
            cand_run: RunName = "cand_on_new" if shared else "cand_on_old"
            runs = GateRuns(base_on_old=measured("base_on_old", old.suite, old.metrics),
                            cand_on_old=measured(cand_run, old.suite, old.metrics), cand_on_new=cand_on_new)
        verdict, items = evaluate_gate(old, request.new, runs)
        notes: list[JudgeNote] = []
        if self._judge is not None:
            transcripts = [ScenarioTranscript(r.scenario_id, r.label, r.repetition, ev) for r, ev in done]
            notes = self._judge.score(new_suite, transcripts)
        return EvalReport(verdict=verdict, items=items, runs=runs, results=[r for r, _ in done],
                          judge_notes=notes)
