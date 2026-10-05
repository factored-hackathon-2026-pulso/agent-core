"""`build_turn_engine`: compone `TurnEngine` con M2, M3, M5, M6, M7, M8, M10 y M11 reales (m04 §15).

Todo lo que toca el mundo (Postgres, LLM, tools, autorización, reloj, IDs) entra por `EngineDeps`: con dobles
guionados es el motor de las pruebas y del replay `fixture`; con adaptadores reales, el de `agentcore`."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from decimal import Decimal

import agent_telemetry as tel
from agent_core.actions import ActionManager
from agent_core.audit import AuditLog, TranscriptReader, TurnRecorder
from agent_core.composition.directory import DirectoryToolExecutor
from agent_core.composition.engine_tools import EngineToolExecutor, UowRunLookup
from agent_core.composition.runtime import EngineRuntimeFactory, RuntimeConfig
from agent_core.decision import DecisionProvider, DecisionService, UnderstandService
from agent_core.decision.calibration.artifact import CalibrationSource
from agent_core.domain import Release
from agent_core.guards import GuardService, LangThresholds
from agent_core.handoff import HandoffService
from agent_core.interpreter import CircuitBreaker
from agent_core.knowledge import KnowledgeService
from agent_core.ports import (
    AgentDirectory,
    AuditSink,
    AuthzPort,
    Clock,
    IdSource,
    KeyProvider,
    KnowledgeSource,
    LLMGateway,
    RegistryPort,
    ToolExecutor,
    TranscriptStore,
    UnitOfWorkFactory,
)
from agent_core.response import NumberFormat
from agent_core.turn import DecisionUnderstand, TraceIds, TurnConfig, TurnEngine, TurnTelemetry
from agent_core.views import FieldClassifier, ViewService


@dataclass(frozen=True)
class EngineConfig:
    """Datos de despliegue: nada de esto sale de la release."""

    turn: TurnConfig = field(default_factory=TurnConfig)
    recent_turns: int = 6                 # entradas del transcript que ve Understand (m04 §14)
    number_format: NumberFormat | None = None
    max_regenerations: int = 1
    priority: str = "normal"
    lang_thresholds: Mapping[str, LangThresholds] = field(default_factory=dict)


@dataclass(frozen=True)
class EngineTools:
    """Las tools que sirve el motor (`seleccionar`, `convertir_moneda`, `obtener_handoff`, `leer_transcript`;
    `composition.engine_tools`). Solo `serve` las activa; `fx_rates` None deja `convertir_moneda` cerrada."""

    fx_rates: Mapping[str, Decimal] | None = None


@dataclass(frozen=True)
class EngineDeps:
    clock: Clock
    ids: IdSource
    keys: KeyProvider
    uow_factory: UnitOfWorkFactory
    audit: AuditSink
    registry: RegistryPort
    releases: Callable[[str], Release]    # release fijada del run por id (el `RegistryPort` no la lee)
    tools: ToolExecutor
    gateway: LLMGateway
    providers: Mapping[str, DecisionProvider]
    calibrations: CalibrationSource
    transcript: TranscriptStore
    authz: AuthzPort
    classifier: FieldClassifier | None = None
    trace: TraceIds | None = None
    knowledge: KnowledgeSource | None = None  # M12: sin fuente, un nodo `knowledge` es un error de cableado
    directory: AgentDirectory | None = None   # ADR 0021: con él el motor sirve `directory/list`
    telemetry: TurnTelemetry | None = None  # m04 §3.9: sin ella, no-op (pruebas, replay, evaluación)
    config: EngineConfig = field(default_factory=EngineConfig)
    engine_tools: EngineTools | None = None  # None: replay, evaluación y pruebas usan solo `tools`


def _tools(deps: EngineDeps) -> ToolExecutor:
    """`deps.tools`, wrapped to serve `directory/list` when a directory is wired (ADR 0021, spec §4)."""
    if deps.directory is None:
        return deps.tools
    return DirectoryToolExecutor(deps.tools, deps.directory, deps.authz, deps.ids)


class RequestTraceIds:
    """`TraceIds` (U3, F8): the request's trace id (the OTel one or M9's fallback, `bind_trace_id`); outside a
    request (replay, `agentcore record`, registry evaluation, in-process tests), derived from the turn. Never
    asks the IdSource: that would shift the recorded ids and change the committed fixtures."""

    def current(self, turn_id: str) -> str:
        return tel.current_trace_id() or f"trace-{turn_id}"


@dataclass(frozen=True)
class BuiltEngine:
    """El motor y los lectores que la API necesita de M10 y M11, armados con las mismas piezas."""

    turns: TurnEngine
    handoffs: HandoffService
    transcripts: TranscriptReader


def build_engine(deps: EngineDeps) -> BuiltEngine:
    cfg = deps.config
    views = ViewService(deps.keys, deps.authz, deps.clock, deps.classifier)
    decisions = DecisionService(deps.registry, deps.providers, deps.calibrations, deps.clock, deps.ids)
    actions = ActionManager(deps.ids, deps.clock)
    audit_log = AuditLog(deps.audit)
    # `record_resolution` escribe fuera de un turno: sus eventos deben encadenarse como los del motor (el
    # recorder por defecto los agrega sin `seq`/`hash` y Postgres los rechaza con un 500).
    handoffs = HandoffService(uow_factory=deps.uow_factory, registry=deps.registry, views=views,
                              authz=deps.authz, keys=deps.keys, clock=deps.clock, ids=deps.ids,
                              record=audit_log.recorder())
    transcripts = TranscriptReader(deps.transcript, deps.uow_factory, views, deps.keys, deps.ids)
    tools = _tools(deps)
    if deps.engine_tools is not None:  # M10/M11 leen para quien llama: los mismos lectores que la API
        tools = EngineToolExecutor(tools, deps.ids, UowRunLookup(deps.uow_factory), handoffs, transcripts,
                                   deps.engine_tools.fx_rates)
    runtimes = EngineRuntimeFactory(
        clock=deps.clock, ids=deps.ids, keys=deps.keys, registry=deps.registry, releases=deps.releases,
        tools=tools, gateway=deps.gateway, decisions=decisions, actions=actions, views=views,
        uow_factory=deps.uow_factory, authz=deps.authz, breaker=CircuitBreaker(),
        knowledge=None if deps.knowledge is None else KnowledgeService(deps.knowledge, deps.authz),
        config=RuntimeConfig(number_format=cfg.number_format, max_regenerations=cfg.max_regenerations,
                             priority=cfg.priority, lang_thresholds=cfg.lang_thresholds))
    turns = TurnEngine(
        uow_factory=deps.uow_factory, registry=deps.registry, clock=deps.clock, ids=deps.ids,
        guards=GuardService(deps.registry, deps.clock, deps.ids, dict(cfg.lang_thresholds)),
        understand=DecisionUnderstand(UnderstandService(decisions), deps.transcript,
                                      recent_turns=cfg.recent_turns),
        actions=actions, handoff=handoffs,
        recorder=TurnRecorder(deps.transcript, deps.keys), chain=audit_log,
        audit=deps.audit, runtimes=runtimes, trace=deps.trace or RequestTraceIds(), config=cfg.turn,
        authz=deps.authz, telemetry=deps.telemetry)
    return BuiltEngine(turns=turns, handoffs=handoffs, transcripts=transcripts)


def build_turn_engine(deps: EngineDeps) -> TurnEngine:
    return build_engine(deps).turns
