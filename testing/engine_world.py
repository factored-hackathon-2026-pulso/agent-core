"""Mundo del motor compuesto: M2–M11 reales (`build_turn_engine`) con los puertos externos guionados.

Solo datos sintéticos. Sirve a las pruebas de `tests/composition` y a `agentcore record` (`testing/replay`).
Los únicos dobles son los del mundo exterior: LLM, proveedores de decisión, tools, almacenamiento y
autorización."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

from agent_core.audit import RecordingGateway, RecordingToolExecutor
from agent_core.composition import (
    DIRECTORY_TOOL,
    DirectoryToolExecutor,
    EngineConfig,
    EngineDeps,
    build_turn_engine,
)
from agent_core.decision import RawPrediction
from agent_core.decision.calibration.artifact import (
    CalibrationArtifact,
    DirectoryCalibrationSource,
    InMemoryCalibrationSource,
    Target,
)
from agent_core.domain import (
    AgentSelector,
    ConfirmAnswer,
    EntityKind,
    EntityRef,
    JsonValue,
    KnowledgeView,
    Locale,
    OnBehalfOf,
    Principal,
    Purpose,
    Release,
    RunInput,
    SubjectRef,
    ToolDef,
    TurnInput,
)
from agent_core.ports import (
    AuditSink,
    AuthzDecision,
    GenerationResult,
    KnowledgeSource,
    ToolExecutor,
    UnitOfWorkFactory,
)
from agent_core.turn import TurnEngine
from agent_core.views import DEFAULT_CATALOG, FieldClassifier, FieldRule
from testing.builders import NOW
from testing.builders import principal as make_principal
from testing.fakes.authz import TableAuthz
from testing.fakes.clock import FakeClock
from testing.fakes.directory import InMemoryDirectory
from testing.fakes.gateway import ScriptedGateway
from testing.fakes.ids import FakeIds
from testing.fakes.keys import FakeKeyProvider
from testing.fakes.provider import ScriptedProvider
from testing.fakes.registry_dir import registry_from_releases
from testing.fakes.storage import InMemoryAuditSink, InMemoryStore
from testing.fakes.tools import FakeToolExecutor, Scripted
from testing.fakes.transcript import InMemoryTranscript

REGISTRY_DEMO = Path(__file__).parents[1] / "tests" / "fixtures" / "registry-demo"
RELEASE_ID = "demo"
TRANSFER_DEMO = Path(__file__).parents[1] / "tests" / "fixtures" / "registry-transfer-demo"
TRANSFER_RELEASES = ("recepcion-demo", "disputas-demo", "consultas-demo")
TRANSFER_CALIBRATION = "cal-transfer-demo"
DRAFT = "Tu disputa quedó radicada y te avisaremos cuando haya novedades."
CANDIDATAS: list[JsonValue] = [
    {"transaction_id": "tx-1", "amount": Decimal("120.50"), "currency": "USD"},
    {"transaction_id": "tx-2", "amount": Decimal("30.00"), "currency": "USD"},
]
# Catálogo sintético: lo que en producción publica el equipo de datos como `FieldClassification`.
CATALOG = {
    **DEFAULT_CATALOG,
    "amount": FieldRule(field_class="financial"),
    "currency": FieldRule(field_class="public"),
    "status": FieldRule(field_class="public"),
    "id": FieldRule(field_class="public"),
    # Directory option ids (ADR 0021, spec §3.2, §12.14): not PII; if tokenized,
    # `decide_choice` could not choose.
    "directory.choices": FieldRule(field_class="public"),
    "directory.entries.agent_id": FieldRule(field_class="public"),
    "directory.entries.release_id": FieldRule(field_class="public"),
    # Routing cards: registry-authored text, shown to the router wrapped as untrusted (never tokenized).
    "directory.entries.summary": FieldRule(field_class="untrusted_text"),
    "directory.entries.examples": FieldRule(field_class="untrusted_text"),
}


def _buscar(args: dict[str, JsonValue]) -> JsonValue:
    return CANDIDATAS


def _seleccionar(args: dict[str, JsonValue]) -> JsonValue:
    lista = args["lista"]
    assert isinstance(lista, list)
    return next(t for t in lista if isinstance(t, dict) and t["transaction_id"] == args["id"])


def _convertir(args: dict[str, JsonValue]) -> JsonValue:
    return Decimal("120.50")


def _radicar(args: dict[str, JsonValue]) -> JsonValue:
    return {"status": "Open", "id": "pqr-demo-1", **args}


_HANDLERS = {"buscar_transacciones": _buscar, "seleccionar": _seleccionar, "convertir_moneda": _convertir}


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

    def knowledge_view(self, principal: Principal, purpose: Purpose) -> KnowledgeView:
        return TableAuthz().knowledge_view(principal, purpose)

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


def demo_calibration() -> CalibrationArtifact:
    """Umbrales sintéticos: cada valor que el guion emite supera su umbral."""
    thresholds = {("command", command, "jev", "es"): 0.5
                  for command in ("continue", "affirm", "deny", "start_flow", "out_of_scope", "interrupt")}
    thresholds[("flow", "disputa-cargo", "jev", "es")] = 0.5
    thresholds[("interrupt", "fraude", "jev", "es")] = 0.5
    thresholds[("match", "unica", "classifier", "es")] = 0.5
    target = {"command": Target(metric="precision", value=0.9)}
    return CalibrationArtifact(run_id="cal-demo", split_hash="b" * 64, method="isotonic", calibrators={},
                               thresholds=thresholds, target=target)


def transfer_calibration() -> CalibrationArtifact:
    """The hand-made artifact of the transfer demo (U2): its `"*"` threshold lets a runtime choice pass."""
    artifact = DirectoryCalibrationSource(TRANSFER_DEMO / "calibrations").get(TRANSFER_CALIBRATION)
    assert artifact is not None, "falta calibrations/cal-transfer-demo.json en el registro de la demo"
    return artifact


def principal_at(level: str) -> Principal:
    return make_principal(auth={"level": level, "at": NOW})


@dataclass
class Driver:
    """Aplica operaciones (`start`, `turn`, `confirm`) a un motor: el mismo código al grabar y al reproducir.
    Lo que se graba en `ops` es exactamente lo que el replay vuelve a aplicar.

    - `{"op": "start", "lang"?, "auth"?}` crea el run (primer turno incluido).
    - `{"op": "turn", "text", "auth"?}` es un turno de texto.
    - `{"op": "confirm", "answer": "yes"|"no", "auth"?}` responde la última confirmación pendiente."""

    engine: TurnEngine
    agent: str = "atencion"  # entry agent of `start`; not written into the recorded op
    ops: list[dict[str, JsonValue]] = field(default_factory=list)
    session_id: str | None = None
    run_id: str | None = None
    confirmation_token: str | None = None
    results: list[Any] = field(default_factory=list)
    _turns: int = 0

    def apply(self, op: dict[str, JsonValue]) -> Any:
        self.ops.append(dict(op))
        principal = principal_at(str(op.get("auth", "step_up")))
        kind = op["op"]
        result: Any
        if kind == "start":
            lang = op.get("lang")
            run_input = RunInput.model_validate({
                "agent": AgentSelector(id=self.agent, alias="prod"), "idempotency_key": "key-1",
                **({"lang": lang} if lang is not None else {})})
            result = self.engine.start_run(principal, None, run_input)
            self.session_id, self.run_id = result.session_id, result.run_id
            turn = result.first_turn
        else:
            assert self.session_id is not None, "un turno necesita un run iniciado"
            self._turns += 1
            confirm = None
            if kind == "confirm":
                assert self.confirmation_token is not None, "no hay confirmación pendiente"
                confirm = ConfirmAnswer(token=self.confirmation_token, answer=op["answer"])  # type: ignore[arg-type]
            turn = self.engine.handle_turn(principal, None, TurnInput(
                session_id=self.session_id, text=str(op.get("text", "")) if confirm is None else "",
                channel="web", client_turn_id=f"c-{self._turns}", confirm=confirm))
            result = turn
        if turn is not None:
            self.confirmation_token = turn.confirmation.token if turn.confirmation is not None else None
        self.results.append(result)
        return result


class EngineWorld:
    """El motor real sobre un registro de autoría. `uow_factory` y `audit` por defecto son en memoria.

    Con `record=True` las tools y el LLM pasan por los envoltorios de M11 (capturan `full` y borradores)."""

    def __init__(self, *, registry_root: Path = REGISTRY_DEMO, uow_factory: UnitOfWorkFactory | None = None,
                 audit: AuditSink | None = None, config: EngineConfig | None = None,
                 gateway: ScriptedGateway | None = None, record: bool = False,
                 clock: FakeClock | None = None, ids: FakeIds | None = None,
                 knowledge: KnowledgeSource | None = None, releases: tuple[str, ...] = (RELEASE_ID,),
                 agent: str = "atencion", directory: bool = False,
                 calibrations: Mapping[str, CalibrationArtifact] | None = None) -> None:
        self.clock = clock or FakeClock()
        self.ids = ids or FakeIds()
        self.registry = registry_from_releases(registry_root, releases)
        self._release_ids = releases
        self.release: Release = self.registry.resolve_release(
            AgentSelector(id=agent, alias="prod"), principal_at("step_up"))
        self.store = InMemoryStore()
        self.uow_factory = uow_factory or self.store.uow
        self.audit = audit or InMemoryAuditSink(self.store)
        self.transcript = InMemoryTranscript()
        self.gateway = gateway or CitingGateway()
        self.jev = ScriptedProvider("jev", clock=self.clock)
        self.classifier = ScriptedProvider("classifier", clock=self.clock)
        self.tools = FakeToolExecutor(self.ids)
        self._tool_versions = self._union_of_tools()
        self._register_tools()
        self.authz = SyntheticAuthz()
        self.directory = InMemoryDirectory(self.registry) if directory else None
        inner: ToolExecutor = self.tools
        if self.directory is not None and record:
            # F5: the recording must see `directory/list` to replay it (replay serves every tool from
            # the record).
            inner = DirectoryToolExecutor(self.tools, self.directory, self.authz, self.ids)
        self.recording_tools = RecordingToolExecutor(inner) if record else None
        self.recording_llm = RecordingGateway(self.gateway) if record else None
        self.deps = EngineDeps(
            clock=self.clock, ids=self.ids, keys=FakeKeyProvider.default(), uow_factory=self.uow_factory,
            audit=self.audit, registry=self.registry, releases=self._release,
            tools=self.recording_tools or inner, gateway=self.recording_llm or self.gateway,
            providers={"jev": self.jev, "classifier": self.classifier},
            calibrations=InMemoryCalibrationSource(calibrations or {"cal-demo": demo_calibration()}),
            transcript=self.transcript, authz=self.authz, classifier=FieldClassifier(CATALOG),
            config=config or EngineConfig(), knowledge=knowledge,
            directory=None if record else self.directory)
        self.engine: TurnEngine = build_turn_engine(self.deps)
        self.runtimes = self.engine._runtimes  # RuntimeFactory real
        self.driver = Driver(self.engine, agent=agent)
        self.principal = principal_at("step_up")

    def _release(self, release_id: str) -> Release:
        return self.registry.resolve_release_by_id(release_id)

    def _union_of_tools(self) -> dict[str, str]:
        """Tools of every pinned release; `directory/list` is left out: `DirectoryToolExecutor` serves it."""
        versions: dict[str, str] = {}
        for release_id in self._release_ids:
            release = self.registry.resolve_release_by_id(release_id)
            for tool_id, version in release.entities.get(EntityKind.tool, {}).items():
                if tool_id != DIRECTORY_TOOL.id:
                    versions.setdefault(tool_id, version)
        return versions

    def _register_tools(self) -> None:
        for tool_id, version in self._tool_versions.items():
            definition = self.registry.get(EntityRef(id=tool_id, version=version), ToolDef)
            if tool_id == "obtener_pqr":
                self.tools.register_readback(definition, of=EntityRef(id="radicar_pqr", version=version))
            else:
                self.tools.register(definition, handler=_HANDLERS.get(tool_id, _radicar))

    def script_tool(self, tool_id: str, *items: Scripted) -> None:
        """Resultados guionados que tienen prioridad sobre el manejador de la tool (p. ej. `uncertain`)."""
        version = self._tool_versions[tool_id]
        ref = EntityRef(id=tool_id, version=version)
        definition = self.registry.get(ref, ToolDef)
        self.tools.register(definition, script=items, handler=_HANDLERS.get(tool_id, _radicar))

    # --- guion -------------------------------------------------------------------------------------------

    def understands(self, command: str, *, flow: str | None = None, interrupt: str | None = None,
                    p: float = 0.95) -> None:
        value: dict[str, JsonValue] = {"command": command}
        p_raw: dict[str, float | None] = {"command": p}
        for name, label in (("flow", flow), ("interrupt", interrupt)):
            if label is not None:
                value[name] = label
                p_raw[name] = p
        self.jev.push(RawPrediction(value=value, p_raw=p_raw, tokens=20))

    def matches(self, transaction: str = "tx-1") -> None:
        self.classifier.push(RawPrediction(value={"match": "unica", "transaction": transaction},
                                           p_raw={"match": 0.9}, tokens=10))

    def routes(self, choice: str, p: float = 0.9) -> None:
        """`elegir-especialista` (classifier) picks `choice` among the directory options."""
        self.classifier.push(RawPrediction(value={"choice": choice}, p_raw={"choice": p}, tokens=10))

    # --- turnos ------------------------------------------------------------------------------------------

    def start(self, **over: Any) -> Any:
        return self.driver.apply({"op": "start", **over})

    def turn(self, text: str = "hola", **over: Any) -> Any:
        return self.driver.apply({"op": "turn", "text": text, **over})

    def confirm(self, answer: str = "yes", **over: Any) -> Any:
        return self.driver.apply({"op": "confirm", "answer": answer, **over})


def transfer_world(**over: Any) -> EngineWorld:
    """Reception and two specialists over `registry-transfer-demo` (ADR 0021, phase 7)."""
    options: dict[str, Any] = {
        "registry_root": TRANSFER_DEMO, "releases": TRANSFER_RELEASES, "agent": "recepcion",
        "directory": True,
        "calibrations": {TRANSFER_CALIBRATION: transfer_calibration()},
    }
    return EngineWorld(**(options | over))
