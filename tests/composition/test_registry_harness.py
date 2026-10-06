from collections.abc import Callable
from typing import Any

import pytest

from agent_core.composition import EngineScenarioHarness
from agent_core.domain import (
    EngineEvent,
    GatewayError,
    GatewayErrorKind,
    OnBehalfOf,
    Outcome,
    Principal,
    PrincipalKey,
    PrincipalType,
    Prompt,
    RunClosed,
    SubjectRef,
)
from agent_core.ports import LLMGateway, RegistryPort
from agent_core.registry import EvalSuite, EvalTarget, HarnessUnavailable, LocalSandbox, SnapshotRegistry
from agent_core.registry.evaluation.scoring import score_run
from agent_core.registry.suite import ScenarioPrincipal
from testing.engine_world import CitingGateway, SyntheticAuthz
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


def test_an_invalid_output_is_the_agents_own_result_not_harness_unavailable() -> None:
    """`invalid_output` is what the MODEL produced (schema violation after the agent's own regeneration):
    the agent absorbs it by design (regenerate, then degrade); the scenario is scored on what the agent did.
    Only a gateway that cannot answer (unavailable, timeout, rate limit) is infrastructure."""
    harness: EngineScenarioHarness = build_harness(
        gateway_error=GatewayError(GatewayErrorKind.invalid_output))
    scenario = next(s for s in demo_suite().scenarios if s.id == "resuelto")
    sandbox = LocalSandbox(FakeIds())
    handle = sandbox.provision(scenario.seed, _target())
    events = harness.run(_target(), AGENT, scenario, sandbox.tools(handle))
    assert [e for e in events if isinstance(e, RunClosed)]


@pytest.mark.parametrize("failure", [Failure("jev caído"), Timeout(after_ms=100)])
def test_a_provider_failure_becomes_harness_unavailable(failure: Failure | Timeout) -> None:
    harness = build_harness(provider_failure=failure)
    scenario = next(s for s in demo_suite().scenarios if s.id == "resuelto")
    sandbox = LocalSandbox(FakeIds())
    with pytest.raises(HarnessUnavailable, match="proveedor"):
        harness.run(_target(), AGENT, scenario, sandbox.tools(sandbox.provision(scenario.seed, _target())))


def test_a_second_evaluation_on_a_persistent_store_executes_again() -> None:
    """Con la base de evaluación persistente, la clave de idempotencia no puede repetir runs viejos."""
    harness = build_harness(persistent=True)
    scenario = next(s for s in demo_suite().scenarios if s.id == "resuelto")
    sandbox = LocalSandbox(FakeIds())
    def once() -> list[EngineEvent]:
        handle = sandbox.provision(scenario.seed, _target())
        return harness.run(_target(), AGENT, scenario, sandbox.tools(handle))

    runs = [once(), once()]
    assert {e.run_id for e in runs[0]}.isdisjoint({e.run_id for e in runs[1]})
    assert any(isinstance(e, RunClosed) for e in runs[1])


def _with_prompt_text(text: str) -> EvalTarget:
    """A candidate: the same release (same exact refs) whose p/resumen_radicado entity has another text."""
    pinned = demo_pinned()
    entities = [e.model_copy(update={"locales": {"es": text, "pt": text}})
                if isinstance(e, Prompt) and e.id == "p/resumen_radicado" else e for e in pinned.entities]
    return EvalTarget("candidate", pinned.release, SnapshotRegistry(pinned.release, entities))


def _recording(seen: list[str]) -> Callable[[RegistryPort], LLMGateway]:
    """Gateway factory: each gateway records the prompt text found in the registry it is bound to."""
    def bind(registry: RegistryPort) -> LLMGateway:
        class _Gateway(CitingGateway):
            def generate(self, prompt: Any, inputs: Any, locale: Any, schema: Any = None) -> Any:
                seen.append(registry.get(prompt, Prompt).locales[locale])
                return super().generate(prompt, inputs, locale, schema)
        return _Gateway()
    return bind


def _run(harness: EngineScenarioHarness, target: EvalTarget) -> list[EngineEvent]:
    scenario = next(s for s in demo_suite().scenarios if s.id == "resuelto")
    sandbox = LocalSandbox(FakeIds())
    return harness.run(target, AGENT, scenario, sandbox.tools(sandbox.provision(scenario.seed, target)))


def test_a_candidate_prompt_is_exercised_when_the_gateway_is_bound_to_the_target() -> None:
    seen: list[str] = []
    harness = build_harness(bind_gateway=_recording(seen))
    _run(harness, _with_prompt_text("TEXTO-CANDIDATO-UNICO"))
    assert seen and set(seen) == {"TEXTO-CANDIDATO-UNICO"}
    seen.clear()
    _run(harness, _target())  # the base target keeps resolving its own prompt text
    assert seen and "TEXTO-CANDIDATO-UNICO" not in seen


def test_without_bind_gateway_the_shared_gateway_is_used_unchanged() -> None:
    harness = build_harness()
    closed = [e for e in _run(harness, _with_prompt_text("OTRO")) if isinstance(e, RunClosed)]
    assert closed and closed[-1].payload.outcome is Outcome.resolved


class _RecordingAuthz(SyntheticAuthz):
    def __init__(self) -> None:
        self.seen: list[tuple[Principal, OnBehalfOf | None, SubjectRef | None]] = []

    def bind_params(self, principal: Principal, obo: OnBehalfOf | None,
                    subject: SubjectRef | None) -> dict[str, str]:
        self.seen.append((principal, obo, subject))
        return super().bind_params(principal, obo, subject)


def _run_with_principal(authz: _RecordingAuthz, principal: dict[str, Any]) -> None:
    harness = build_harness(authz=authz)
    who = ScenarioPrincipal.model_validate(principal)
    scenario = demo_suite().scenarios[0].model_copy(update={"principal": who})
    sandbox = LocalSandbox(FakeIds())
    harness.run(_target(), AGENT, scenario, sandbox.tools(sandbox.provision(scenario.seed, _target())))


def test_an_advisor_scenario_runs_with_an_advisor_principal_and_a_synthetic_delegation() -> None:
    authz = _RecordingAuthz()
    _run_with_principal(authz, {"id": "adv-7", "type": "advisor",
                                "subject": {"kind": "customer", "ref": "cust-001"}})
    assert authz.seen
    principal, obo, subject = authz.seen[0]
    assert principal.type is PrincipalType.advisor and principal.id == "adv-7"
    assert obo is not None and obo.grantee == PrincipalKey(type=PrincipalType.advisor, id="adv-7")
    assert obo.subject == SubjectRef(kind="customer", ref="cust-001") and obo.grant_ref == "eval"
    assert subject == obo.subject


def test_a_customer_scenario_is_unchanged_no_delegation_and_customer_type() -> None:
    authz = _RecordingAuthz()
    _run_with_principal(authz, {"id": "cust-001"})
    assert authz.seen
    assert all(p.type is PrincipalType.customer and o is None for p, o, _ in authz.seen)


def test_a_step_after_the_run_closed_does_not_crash_the_evaluation() -> None:
    """A scenario may keep talking after the agent closed the run (abstained, resolved, escalated): the
    evaluator records what happened instead of failing the whole evaluation with `run_closed`."""
    from agent_core.registry.suite import Step

    harness = build_harness()
    base = next(s for s in demo_suite().scenarios if s.id == "resuelto")
    longer = base.model_copy(update={"steps": [*base.steps, Step(op="turn", text="gracias"),
                                               Step(op="turn", text="una cosa más")]})
    sandbox = LocalSandbox(FakeIds())
    events = harness.run(_target(), AGENT, longer, sandbox.tools(sandbox.provision(longer.seed, _target())))
    closed = [e for e in events if isinstance(e, RunClosed)]
    assert len(closed) == 1 and closed[0].payload.outcome is Outcome.resolved
