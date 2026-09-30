"""Mundo de pruebas de la composición: el motor completo (M2–M11 reales) con puertos externos guionados.

Solo datos sintéticos. Los únicos dobles son los del mundo exterior: LLM, proveedores de decisión, tools,
almacenamiento y autorización; todo lo demás es el código real que arma `build_turn_engine`."""

from decimal import Decimal
from pathlib import Path
from typing import Any

from agent_core.composition import EngineConfig, EngineDeps, build_turn_engine
from agent_core.decision import RawPrediction
from agent_core.decision.calibration.artifact import CalibrationArtifact, InMemoryCalibrationSource
from agent_core.domain import (
    AgentSelector,
    ConfirmAnswer,
    EntityKind,
    EntityRef,
    JsonValue,
    Locale,
    OnBehalfOf,
    Principal,
    Release,
    RunInput,
    SubjectRef,
    ToolDef,
    TurnInput,
    TurnResult,
)
from agent_core.ports import AuditSink, AuthzDecision, GenerationResult, UnitOfWorkFactory
from agent_core.turn import TurnEngine
from agent_core.views import FieldClassifier
from testing.builders import principal as make_principal
from testing.fakes.clock import FakeClock
from testing.fakes.gateway import ScriptedGateway
from testing.fakes.ids import FakeIds
from testing.fakes.keys import FakeKeyProvider
from testing.fakes.provider import ScriptedProvider
from testing.fakes.registry_dir import registry_from_directory
from testing.fakes.storage import InMemoryAuditSink, InMemoryStore
from testing.fakes.tools import FakeToolExecutor
from testing.fakes.transcript import InMemoryTranscript
from tests.m02.harness import CATALOG
from tests.m05.helpers import artifact

REGISTRY_DIR = Path(__file__).parent / "fixtures" / "registry"
DRAFT = "Tu disputa quedó radicada y te avisaremos cuando haya novedades."
CANDIDATAS = [
    {"transaction_id": "tx-1", "amount": Decimal("120.50"), "currency": "USD"},
    {"transaction_id": "tx-2", "amount": Decimal("30.00"), "currency": "USD"},
]


class SyntheticAuthz:
    """`AuthzPort` permisivo para pruebas: la política real es `TableAuthz` (M9)."""

    def authorize_agent(self, principal: Principal, agent: Any, subject: SubjectRef | None) -> AuthzDecision:
        return AuthzDecision(allowed=True)

    def authorize_subject(self, principal: Principal, obo: OnBehalfOf | None,
                          subject: SubjectRef | None) -> AuthzDecision:
        return AuthzDecision(allowed=True)

    def bind_params(self, principal: Principal, obo: OnBehalfOf | None,
                    subject: SubjectRef | None) -> dict[str, str]:
        return {}

    def can_read_field(self, reader: Principal, obo: OnBehalfOf | None, field: str, purpose: str) -> bool:
        return True

    def reportable_attrs(self) -> frozenset[str]:
        return frozenset({"country"})


class CitingGateway(ScriptedGateway):
    """LLM guionado: redacta `text` citando los `fact_id` que M8 le entrega (no los conoce de antemano)."""

    def __init__(self, text: str = DRAFT) -> None:
        super().__init__()
        self._text = text

    def generate(self, prompt: Any, inputs_model_view: Any, locale: Locale,
                 schema: dict[str, JsonValue] | None = None) -> GenerationResult:
        facts = inputs_model_view.get("facts", {})
        cited = [entry["fact_id"] for entry in facts.values()]
        self.push(GenerationResult(output={"text": self._text, "citations": cited}, tokens_in=40,
                                   tokens_out=12, cost_usd=Decimal("0.002"), model="modelo-sintetico-1"))
        return super().generate(prompt, inputs_model_view, locale, schema)


def _calibration() -> CalibrationArtifact:
    """Umbrales sintéticos: cada valor que el guion emite supera su umbral."""
    thresholds = {("command", command, "jev", "es"): 0.5
                  for command in ("continue", "affirm", "deny", "start_flow", "out_of_scope")}
    thresholds[("flow", "disputa-cargo", "jev", "es")] = 0.5
    thresholds[("match", "unica", "classifier", "es")] = 0.5
    return artifact(run_id="cal-demo", thresholds=thresholds)


class EngineWorld:
    """El motor real sobre el registro demo. `uow_factory` y `audit` por defecto son en memoria."""

    def __init__(self, *, uow_factory: UnitOfWorkFactory | None = None, audit: AuditSink | None = None,
                 config: EngineConfig | None = None, gateway: ScriptedGateway | None = None) -> None:
        self.clock, self.ids = FakeClock(), FakeIds()
        self.registry = registry_from_directory(REGISTRY_DIR, "demo")
        self.principal = make_principal()
        self.release: Release = self.registry.resolve_release(
            AgentSelector(id="atencion", alias="prod"), self.principal)
        self.store = InMemoryStore()
        self.uow_factory = uow_factory or self.store.uow
        self.audit = audit or InMemoryAuditSink(self.store)
        self.transcript = InMemoryTranscript()
        self.gateway = gateway or CitingGateway()
        self.jev = ScriptedProvider("jev", clock=self.clock)
        self.classifier = ScriptedProvider("classifier", clock=self.clock)
        self.tools = FakeToolExecutor(self.ids)
        self._register_tools()
        self.deps = EngineDeps(
            clock=self.clock, ids=self.ids, keys=FakeKeyProvider.default(), uow_factory=self.uow_factory,
            audit=self.audit, registry=self.registry, releases=self._release, tools=self.tools,
            gateway=self.gateway, providers={"jev": self.jev, "classifier": self.classifier},
            calibrations=InMemoryCalibrationSource({"cal-demo": _calibration()}),
            transcript=self.transcript, authz=SyntheticAuthz(), classifier=FieldClassifier(CATALOG),
            config=config or EngineConfig())
        self.engine: TurnEngine = build_turn_engine(self.deps)
        self.runtimes = self.engine._runtimes  # type: ignore[attr-defined]  # RuntimeFactory real
        self._n = 0

    def _release(self, release_id: str) -> Release:
        assert release_id == self.release.id
        return self.release

    def _register_tools(self) -> None:
        for tool_id, version in self.release.entities[EntityKind.tool].items():
            definition = self.registry.get(EntityRef(id=tool_id, version=version), ToolDef)
            if tool_id == "obtener_pqr":
                self.tools.register_readback(definition, of=EntityRef(id="radicar_pqr", version=version))
            elif tool_id == "buscar_transacciones":
                self.tools.register(definition, handler=lambda a: CANDIDATAS)  # type: ignore[arg-type,return-value]
            elif tool_id == "seleccionar":
                self.tools.register(definition, handler=lambda a: next(
                    t for t in a["lista"] if t["transaction_id"] == a["id"]))  # type: ignore[union-attr,index,arg-type]
            elif tool_id == "convertir_moneda":
                self.tools.register(definition, handler=lambda a: Decimal("120.50"))  # type: ignore[arg-type,return-value]
            else:
                self.tools.register(definition, handler=lambda a: {"status": "Open", "id": "pqr-demo-1", **a})

    # --- guion -------------------------------------------------------------------------------------------

    def understands(self, command: str, *, flow: str | None = None, p: float = 0.95) -> None:
        value: dict[str, JsonValue] = {"command": command}
        p_raw: dict[str, float | None] = {"command": p}
        if flow is not None:
            value["flow"] = flow
            p_raw["flow"] = p
        self.jev.push(RawPrediction(value=value, p_raw=p_raw, tokens=20))

    def matches(self, transaction: str = "tx-1") -> None:
        self.classifier.push(RawPrediction(value={"match": "unica", "transaction": transaction},
                                           p_raw={"match": 0.9}, tokens=10))

    # --- turnos ------------------------------------------------------------------------------------------

    def start(self, **over: Any) -> Any:
        run_input = RunInput.model_validate({"agent": AgentSelector(id="atencion", alias="prod"),
                                             "idempotency_key": "key-1", **over})
        return self.engine.start_run(self.principal, None, run_input)

    def turn(self, session_id: str, text: str = "hola", *,
             confirm: tuple[str, str] | None = None) -> TurnResult:
        self._n += 1
        answer = ConfirmAnswer(token=confirm[0], answer=confirm[1]) if confirm else None  # type: ignore[arg-type]
        return self.engine.handle_turn(self.principal, None, TurnInput(
            session_id=session_id, text="" if confirm else text, channel="web",
            client_turn_id=f"c-{self._n}", confirm=answer))
