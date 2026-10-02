"""`build_turn_engine`: compone `TurnEngine` con M2, M3, M5, M6, M7, M8, M10 y M11 reales (m04 §15).

Todo lo que toca el mundo (Postgres, LLM, tools, autorización, reloj, IDs) entra por `EngineDeps`: con dobles
guionados es el motor de las pruebas y del replay `fixture`; con adaptadores reales, el de `agentcore`."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

from agent_core.actions import ActionManager
from agent_core.audit import AuditLog, TranscriptReader, TurnRecorder
from agent_core.composition.directory import DirectoryToolExecutor
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
from agent_core.turn import DecisionUnderstand, TraceIds, TurnConfig, TurnEngine
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
    config: EngineConfig = field(default_factory=EngineConfig)


def _tools(deps: EngineDeps) -> ToolExecutor:
    """`deps.tools`, wrapped to serve `directory/list` when a directory is wired (ADR 0021, spec §4)."""
    if deps.directory is None:
        return deps.tools
    return DirectoryToolExecutor(deps.tools, deps.directory, deps.authz, deps.ids)


class DerivedTrace:
    """`TraceIds` por defecto: el `trace_id` se deriva del turno (M9 lo reemplaza con el del request)."""

    def current(self, turn_id: str) -> str:
        return f"trace-{turn_id}"


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
    runtimes = EngineRuntimeFactory(
        clock=deps.clock, ids=deps.ids, keys=deps.keys, registry=deps.registry, releases=deps.releases,
        tools=_tools(deps), gateway=deps.gateway, decisions=decisions, actions=actions, views=views,
        uow_factory=deps.uow_factory, authz=deps.authz, breaker=CircuitBreaker(),
        knowledge=None if deps.knowledge is None else KnowledgeService(deps.knowledge, deps.authz),
        config=RuntimeConfig(number_format=cfg.number_format, max_regenerations=cfg.max_regenerations,
                             priority=cfg.priority, lang_thresholds=cfg.lang_thresholds))
    handoffs = HandoffService(uow_factory=deps.uow_factory, registry=deps.registry, views=views,
                              authz=deps.authz, keys=deps.keys, clock=deps.clock, ids=deps.ids)
    turns = TurnEngine(
        uow_factory=deps.uow_factory, registry=deps.registry, clock=deps.clock, ids=deps.ids,
        guards=GuardService(deps.registry, deps.clock, deps.ids, dict(cfg.lang_thresholds)),
        understand=DecisionUnderstand(UnderstandService(decisions), deps.transcript,
                                      recent_turns=cfg.recent_turns),
        actions=actions, handoff=handoffs,
        recorder=TurnRecorder(deps.transcript, deps.keys), chain=AuditLog(deps.audit),
        audit=deps.audit, runtimes=runtimes, trace=deps.trace or DerivedTrace(), config=cfg.turn,
        authz=deps.authz)
    transcripts = TranscriptReader(deps.transcript, deps.uow_factory, views, deps.keys, deps.ids)
    return BuiltEngine(turns=turns, handoffs=handoffs, transcripts=transcripts)


def build_turn_engine(deps: EngineDeps) -> TurnEngine:
    return build_engine(deps).turns
