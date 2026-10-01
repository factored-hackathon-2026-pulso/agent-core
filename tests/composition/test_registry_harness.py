import pytest

from agent_core.composition import EngineScenarioHarness
from agent_core.domain import GatewayError, GatewayErrorKind, Outcome, RunClosed
from agent_core.registry import EvalSuite, EvalTarget, HarnessUnavailable, LocalSandbox, SnapshotRegistry
from agent_core.registry.evaluation.scoring import score_run
from testing.fakes.ids import FakeIds
from testing.fakes.provider import Failure, Timeout
from testing.registry_demo import build_harness, demo_suite
from tests.registry.helpers import AGENT, demo_pinned


def _target() -> EvalTarget:
    pinned = demo_pinned()
    return EvalTarget("candidate", pinned.release, SnapshotRegistry(pinned.release, pinned.entities))


def test_demo_scenario_resolves_with_real_engine() -> None:
    harness = build_harness()
    suite: EvalSuite = demo_suite()
    scenario = next(s for s in suite.scenarios if s.id == "resuelto")
    sandbox = LocalSandbox(FakeIds())
    handle = sandbox.provision(scenario.seed, _target())
    events = harness.run(_target(), AGENT, scenario, sandbox.tools(handle))
    closed = [e for e in events if isinstance(e, RunClosed)]
    assert closed and closed[-1].payload.outcome is Outcome.resolved
    assert score_run(events, scenario.expect, scenario.sensitive_values).passed


def test_gateway_failure_becomes_harness_unavailable() -> None:
    harness: EngineScenarioHarness = build_harness(
        gateway_error=GatewayError(GatewayErrorKind.unavailable))
    scenario = next(s for s in demo_suite().scenarios if s.id == "resuelto")
    sandbox = LocalSandbox(FakeIds())
    with pytest.raises(HarnessUnavailable):
        harness.run(_target(), AGENT, scenario, sandbox.tools(sandbox.provision(scenario.seed, _target())))


@pytest.mark.parametrize("failure", [Failure("jev caído"), Timeout(after_ms=100)])
def test_a_provider_failure_becomes_harness_unavailable(failure: Failure | Timeout) -> None:
    harness = build_harness(provider_failure=failure)
    scenario = next(s for s in demo_suite().scenarios if s.id == "resuelto")
    sandbox = LocalSandbox(FakeIds())
    with pytest.raises(HarnessUnavailable, match="proveedor"):
        harness.run(_target(), AGENT, scenario, sandbox.tools(sandbox.provision(scenario.seed, _target())))
