"""`ScenarioHarness` real (registry spec §6.2): el motor compuesto sobre la release a evaluar."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from agent_core.composition.engine import EngineConfig, EngineDeps, build_turn_engine
from agent_core.decision import DecisionProvider, ProviderError, ProviderTimeout, RawPrediction
from agent_core.decision.calibration.artifact import CalibrationSource
from agent_core.domain import (
    AgentSelector,
    ConfirmAnswer,
    EngineError,
    EngineEvent,
    EntityRef,
    GatewayError,
    GatewayErrorKind,
    JsonValue,
    Locale,
    OnBehalfOf,
    Principal,
    PrincipalKey,
    PrincipalType,
    ProblemCode,
    ProviderSpec,
    RunInput,
    SubjectRef,
    Suggestion,
    TurnInput,
)
from agent_core.ports import (
    AuditSink,
    AuthzPort,
    Clock,
    GenerationResult,
    IdKind,
    IdSource,
    KeyProvider,
    LLMGateway,
    RegistryPort,
    ToolExecutor,
    TranscriptStore,
    UnitOfWorkFactory,
)
from agent_core.registry import EvalTarget, HarnessUnavailable, Scenario, ScenarioRun
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
        except GatewayError as error:
            # `invalid_output` es lo que PRODUJO el modelo (viola el esquema): el agente lo absorbe por diseño
            # (regenera y luego degrada) y el escenario se puntúa con lo que el agente hizo. Solo un gateway
            # que no puede responder (unavailable, timeout, rate_limited, refused) es infraestructura.
            if error.kind is not GatewayErrorKind.invalid_output:
                self.failed = True
            raise


class _ProbingProvider:
    """Igual que la sonda del gateway: el motor absorbe las fallas de proveedor (cae al siguiente de la
    cadena o degrada), así que la sonda las recuerda para marcar `failed_infra` (registry §6.2 punto 6)."""

    def __init__(self, inner: DecisionProvider, failures: list[str]) -> None:
        self._inner, self._failures = inner, failures
        self.name = inner.name

    def predict(self, spec: ProviderSpec, inputs_model_view: dict[str, JsonValue],
                schema: dict[str, JsonValue], locale: Locale) -> RawPrediction:
        try:
            return self._inner.predict(spec, inputs_model_view, schema, locale)
        except (ProviderError, ProviderTimeout):
            self._failures.append(self.name)
            raise


class EngineScenarioHarness:
    def __init__(self, *, clock: Clock, ids: IdSource, keys: KeyProvider, gateway: LLMGateway,
                 providers: Callable[[str], Mapping[str, DecisionProvider]],
                 calibrations: CalibrationSource,
                 authz: AuthzPort, storage: Callable[[], EvalStorage],
                 classifier: FieldClassifier | None = None, config: EngineConfig | None = None,
                 bind_gateway: Callable[[RegistryPort], LLMGateway] | None = None,
                 gateway_for: Callable[[str], LLMGateway] | None = None) -> None:
        """`bind_gateway` rebuilds the gateway over the evaluated target's registry. Without it the shared
        `gateway` is used as is; a registry-backed one then resolves prompts in the LIVE registry and never
        exercises a candidate's prompts (the responder silently falls back to its template).
        `gateway_for` (scenario id → gateway) lets a double answer per scenario, as `providers` does; the
        real gateway is the same for every scenario, so it is `None` outside tests and demos. It wins over
        both."""
        self._bind_gateway = bind_gateway
        self._clock, self._ids, self._keys = clock, ids, keys
        self._gateway, self._providers, self._calibrations = gateway, providers, calibrations
        self._gateway_for = gateway_for
        self._authz, self._storage, self._classifier = authz, storage, classifier
        self._config = config or EngineConfig()

    def _principal(self, scenario: Scenario, level: str) -> Principal:
        now = self._clock.now()
        return Principal.model_validate({
            "type": scenario.principal.type, "id": scenario.principal.id,
            "attrs": dict(scenario.principal.attrs),
            "auth": {"level": level, "at": now}, "exp": now + timedelta(hours=1)})

    def _on_behalf_of(self, scenario: Scenario) -> tuple[OnBehalfOf | None, SubjectRef | None]:
        """An advisor scenario acts on a subject through a synthetic delegation; a customer one has none."""
        who = scenario.principal
        if who.type != "advisor" or who.subject is None:
            return None, None
        subject = SubjectRef(kind=who.subject.kind, ref=who.subject.ref)
        obo = OnBehalfOf(subject=subject, grant_ref="eval",
                         grantee=PrincipalKey(type=PrincipalType.advisor, id=who.id),
                         exp=self._clock.now() + timedelta(hours=1))
        return obo, subject

    def run(self, target: EvalTarget, agent_id: str, scenario: Scenario,
            tools: ToolExecutor) -> list[EngineEvent]:
        return self.run_with_suggestions(target, agent_id, scenario, tools).events

    def run_with_suggestions(self, target: EvalTarget, agent_id: str, scenario: Scenario,
                             tools: ToolExecutor) -> ScenarioRun:
        """The events of the run and, for a task agent, the `suggestions` of its `RunResult` (ADR 0026)."""
        storage = self._storage()
        # La base de evaluación es persistente: una clave derivada solo del escenario devolvería los eventos
        # del primer run sin ejecutar nada. Cada ejecución (etiqueta, escenario, repetición) lleva su id.
        idempotency_key = f"eval-{self._ids.new_id(IdKind.eval_run)}-{target.label}-{scenario.id}"
        if self._gateway_for is not None:
            gateway = self._gateway_for(scenario.id)
        elif self._bind_gateway is not None:
            gateway = self._bind_gateway(target.registry)
        else:
            gateway = self._gateway
        probe = _ProbingGateway(gateway)
        provider_failures: list[str] = []
        providers = {name: _ProbingProvider(p, provider_failures)
                     for name, p in self._providers(scenario.id).items()}
        engine = build_turn_engine(EngineDeps(
            clock=self._clock, ids=self._ids, keys=self._keys, uow_factory=storage.uow_factory,
            audit=storage.audit, registry=target.registry, releases=lambda _rid: target.release, tools=tools,
            gateway=probe, providers=providers, calibrations=self._calibrations,
            transcript=storage.transcript, authz=self._authz, classifier=self._classifier,
            config=self._config))
        run_id: str | None = None
        session_id: str | None = None
        token: str | None = None
        obo, subject = self._on_behalf_of(scenario)
        suggestions: list[Suggestion] = []
        for n, step in enumerate(scenario.steps):
            principal = self._principal(scenario, step.auth)
            if step.op == "start":
                data: dict[str, Any] = {"agent": AgentSelector(id=agent_id, alias="prod"),
                                        "idempotency_key": idempotency_key}
                if step.lang is not None:
                    data["lang"] = step.lang
                if subject is not None:
                    data["subject"] = subject
                if step.input is not None:
                    data["input"] = step.input
                result = engine.start_run(principal, obo, RunInput.model_validate(data))
                run_id, session_id, turn = result.run_id, result.session_id, result.first_turn
                suggestions = list(result.suggestions)
            else:
                if session_id is None:
                    break  # modo task: el run ya terminó en start
                confirm = (ConfirmAnswer(token=token or "", answer=step.answer)  # type: ignore[arg-type]
                           if step.op == "confirm" else None)
                try:
                    turn = engine.handle_turn(principal, obo, TurnInput(
                        session_id=session_id, text=step.text or "", channel="web", client_turn_id=f"c-{n}",
                        confirm=confirm))
                except EngineError as exc:
                    if exc.code is not ProblemCode.run_closed:
                        raise
                    break  # the agent closed the run before the scenario's last step: score what happened
            token = turn.confirmation.token if turn is not None and turn.confirmation is not None else None
        if probe.failed:
            raise HarnessUnavailable("el gateway falló durante el escenario")
        if provider_failures:
            raise HarnessUnavailable(f"un proveedor de decisión falló durante el escenario: "
                                     f"{sorted(set(provider_failures))}")
        if run_id is None:
            raise HarnessUnavailable("el escenario no inició ningún run")
        return ScenarioRun(events=storage.audit.read(run_id), suggestions=suggestions)
