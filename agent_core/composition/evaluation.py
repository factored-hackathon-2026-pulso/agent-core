"""`ScenarioHarness` real (registry spec §6.2): el motor compuesto sobre la release a evaluar."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from agent_core.composition.engine import EngineConfig, EngineDeps, build_turn_engine
from agent_core.decision import DecisionProvider
from agent_core.decision.calibration.artifact import CalibrationSource
from agent_core.domain import (
    AgentSelector,
    ConfirmAnswer,
    EngineEvent,
    EntityRef,
    GatewayError,
    JsonValue,
    Locale,
    Principal,
    RunInput,
    TurnInput,
)
from agent_core.ports import (
    AuditSink,
    AuthzPort,
    Clock,
    GenerationResult,
    IdSource,
    KeyProvider,
    LLMGateway,
    ToolExecutor,
    TranscriptStore,
    UnitOfWorkFactory,
)
from agent_core.registry import EvalTarget, HarnessUnavailable, Scenario
from agent_core.views import FieldClassifier


@dataclass(frozen=True)
class EvalStorage:
    uow_factory: UnitOfWorkFactory
    audit: AuditSink
    transcript: TranscriptStore


class _ProbingGateway:
    """El motor absorbe los `GatewayError`; la sonda los recuerda para marcar `failed_infra`."""

    def __init__(self, inner: LLMGateway) -> None:
        self._inner = inner
        self.failed = False

    def generate(self, prompt: EntityRef, inputs_model_view: dict[str, JsonValue], locale: Locale,
                 schema: dict[str, JsonValue] | None = None) -> GenerationResult:
        try:
            return self._inner.generate(prompt, inputs_model_view, locale, schema)
        except GatewayError:
            self.failed = True
            raise


class EngineScenarioHarness:
    def __init__(self, *, clock: Clock, ids: IdSource, keys: KeyProvider, gateway: LLMGateway,
                 providers: Callable[[str], Mapping[str, DecisionProvider]],
                 calibrations: CalibrationSource,
                 authz: AuthzPort, storage: Callable[[], EvalStorage],
                 classifier: FieldClassifier | None = None, config: EngineConfig | None = None) -> None:
        self._clock, self._ids, self._keys = clock, ids, keys
        self._gateway, self._providers, self._calibrations = gateway, providers, calibrations
        self._authz, self._storage, self._classifier = authz, storage, classifier
        self._config = config or EngineConfig()

    def _principal(self, scenario: Scenario, level: str) -> Principal:
        now = self._clock.now()
        return Principal.model_validate({
            "type": "customer", "id": scenario.principal.id, "attrs": dict(scenario.principal.attrs),
            "auth": {"level": level, "at": now}, "exp": now + timedelta(hours=1)})

    def run(self, target: EvalTarget, agent_id: str, scenario: Scenario,
            tools: ToolExecutor) -> list[EngineEvent]:
        storage = self._storage()
        probe = _ProbingGateway(self._gateway)
        engine = build_turn_engine(EngineDeps(
            clock=self._clock, ids=self._ids, keys=self._keys, uow_factory=storage.uow_factory,
            audit=storage.audit, registry=target.registry, releases=lambda _rid: target.release, tools=tools,
            gateway=probe, providers=self._providers(scenario.id), calibrations=self._calibrations,
            transcript=storage.transcript, authz=self._authz, classifier=self._classifier,
            config=self._config))
        run_id: str | None = None
        session_id: str | None = None
        token: str | None = None
        for n, step in enumerate(scenario.steps):
            principal = self._principal(scenario, step.auth)
            if step.op == "start":
                data: dict[str, Any] = {"agent": AgentSelector(id=agent_id, alias="prod"),
                                        "idempotency_key": f"eval-{scenario.id}"}
                if step.lang is not None:
                    data["lang"] = step.lang
                result = engine.start_run(principal, None, RunInput.model_validate(data))
                run_id, session_id, turn = result.run_id, result.session_id, result.first_turn
            else:
                if session_id is None:
                    break  # modo task: el run ya terminó en start
                confirm = (ConfirmAnswer(token=token or "", answer=step.answer)  # type: ignore[arg-type]
                           if step.op == "confirm" else None)
                turn = engine.handle_turn(principal, None, TurnInput(
                    session_id=session_id, text=step.text or "", channel="web", client_turn_id=f"c-{n}",
                    confirm=confirm))
            token = turn.confirmation.token if turn is not None and turn.confirmation is not None else None
        if probe.failed:
            raise HarnessUnavailable("el gateway falló durante el escenario")
        if run_id is None:
            raise HarnessUnavailable("el escenario no inició ningún run")
        return storage.audit.read(run_id)
