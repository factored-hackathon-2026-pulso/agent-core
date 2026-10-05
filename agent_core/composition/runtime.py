"""`RuntimeFactory` real de M4 (m04 C1): el puente hacia M7 y el `StepContext` de M2 con M5 y M8 reales."""

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace

from agent_core.actions import ActionManager
from agent_core.adapters.llm import LLMAgentPort
from agent_core.composition.decision import DecisionAdapter
from agent_core.composition.responder import ResponderAdapter
from agent_core.composition.suggester import SuggesterAdapter
from agent_core.decision import DecisionService, EventScope
from agent_core.domain import (
    Agent,
    EncryptedBlob,
    EntityKind,
    EntityRef,
    InvalidRuntimeRef,
    JsonValue,
    LanguageDetection,
    Message,
    OnBehalfOf,
    Principal,
    RefSpec,
    Release,
    RunState,
    SuggestConfig,
    ToolDef,
)
from agent_core.flows import parse_path, release_view
from agent_core.guards import UNCALIBRATED, LangThresholds
from agent_core.interpreter import (
    CircuitBreaker,
    GenerateRequest,
    Projector,
    StepContext,
    SuggestRequest,
)
from agent_core.knowledge import KnowledgeService
from agent_core.ports import (
    AuthzPort,
    Clock,
    IdSource,
    KeyProvider,
    LLMGateway,
    RegistryPort,
    ToolExecutor,
    UnitOfWorkFactory,
)
from agent_core.response import (
    NumberFormat,
    Responder,
    ResponderContext,
    Suggester,
    SuggesterContext,
    ToolEntry,
    ValidationContext,
)
from agent_core.views import TokenVault, ViewService

RESPOND_PURPOSE = "respond"
_FACT_READ = re.compile(r"facts\.([a-z][a-z0-9_]*)(?:\..*)?")


@dataclass(frozen=True)
class RuntimeConfig:
    """Datos de despliegue del turno que no salen de la release."""

    number_format: NumberFormat | None = None  # m08 §3.3: lo decide quien construye el contexto
    max_regenerations: int = 1
    priority: str = "normal"
    lang_thresholds: Mapping[str, LangThresholds] = field(default_factory=dict)


class EngineRuntime:
    """`TurnRuntime`: `step` base del turno y las tres operaciones de M7 (tokenizar, renderizar, sellar)."""

    def __init__(self, step: StepContext, principal: Principal, on_behalf_of: OnBehalfOf | None) -> None:
        self.step = step
        self._principal = principal
        self._on_behalf_of = on_behalf_of
        self._sealed_size = len(step.vault)

    def model_text(self, text: str) -> str:
        return self.step.views.tokenize_text(text, self.step.vault)

    def render(self, message: Message) -> Message:
        rendered = self.step.views.render(message.text, self.step.vault, self._principal, RESPOND_PURPOSE,
                                          self._on_behalf_of)
        return message.model_copy(update={"text": rendered.text})

    def sealed_token_map(self) -> EncryptedBlob | None:
        """`vault.seal()` solo si el turno agregó tokens (el vault solo crece)."""
        if len(self.step.vault) == self._sealed_size:
            return None
        return self.step.vault.seal()


def release_resolver(registry: RegistryPort, release: Release) -> Callable[[EntityKind, RefSpec], EntityRef]:
    """`resolve_ref` de la release del run: referencia de autoría → referencia exacta (M8 y nodo agent)."""
    view = release_view(registry, release)

    def resolve_ref(kind: EntityKind, ref: RefSpec) -> EntityRef:
        entity = view.resolve(kind, ref)
        if entity is None:
            raise InvalidRuntimeRef(f"{kind.value} {ref} no está en la release {release.id}")
        return EntityRef(id=entity.id, version=entity.version)

    return resolve_ref


class EngineRuntimeFactory:
    def __init__(self, *, clock: Clock, ids: IdSource, keys: KeyProvider, registry: RegistryPort,
                 releases: Callable[[str], Release], tools: ToolExecutor, gateway: LLMGateway,
                 decisions: DecisionService, actions: ActionManager, views: ViewService,
                 uow_factory: UnitOfWorkFactory, authz: AuthzPort, breaker: CircuitBreaker,
                 config: RuntimeConfig, knowledge: KnowledgeService | None = None) -> None:
        self._knowledge = knowledge
        self._clock, self._ids, self._keys = clock, ids, keys
        self._registry, self._releases = registry, releases
        self._tools, self._gateway = tools, gateway
        self._decisions, self._actions, self._views = decisions, actions, views
        self._uow_factory, self._authz, self._breaker = uow_factory, authz, breaker
        self._config = config
        self._responder = Responder(registry)
        self._suggester = Suggester()

    def open(self, state: RunState, principal: Principal, on_behalf_of: OnBehalfOf | None) -> EngineRuntime:
        release = self._releases(state.release)
        agent = self._registry.get(state.agent, Agent)
        vault = (TokenVault.open(state.token_map, state.run_id, self._keys, self._ids)
                 if state.token_map is not None else TokenVault(state.run_id, self._keys, self._ids))
        scope = EventScope(run_id=state.run_id, release=state.release, session_id=state.session_id)
        holder: list[StepContext] = []  # el responder y el suggester necesitan el step que los contiene
        responder = ResponderAdapter(
            self._responder, lambda request, current: self._context(holder[0], request, current))
        suggester = SuggesterAdapter(
            self._suggester, lambda request, current: self._suggest_context(holder[0], request, current))
        step = StepContext(
            release=release, agent=agent, locale=state.locale, clock=self._clock, degraded=False,
            registry=self._registry, tools=self._tools,
            decisions=DecisionAdapter(self._decisions, vault, scope),
            actions=self._actions, responder=responder,
            views=self._views, vault=vault, ids=self._ids, uow_factory=self._uow_factory,
            bound_params=self._authz.bind_params(principal, on_behalf_of, state.subject),
            breaker=self._breaker,
            agents=LLMAgentPort(self._gateway, self._registry, release_resolver(self._registry, release)),
            knowledge=self._knowledge, suggester=suggester)
        holder.append(step)
        return EngineRuntime(step, principal, on_behalf_of)

    def _facts_model_view(self, step: StepContext, state: RunState) -> dict[str, JsonValue]:
        """Cada hecho del run en vista `model`: `{nombre: {"value": ...}}`."""
        projector = Projector(state, step)
        by_name: dict[str, JsonValue] = {}
        for name in state.facts:
            path = parse_path(f"facts.{name}.value")
            assert path is not None  # los nombres de hechos ya cumplen la gramática de M1
            by_name[name] = {"value": projector.model_value(path)}
        return by_name

    def _validation(self, step: StepContext, state: RunState, by_name: dict[str, JsonValue],
                    find_clear_pii: Callable[[str], list[str]]) -> ValidationContext:
        lang_cfg = step.registry.get(step.release.language_detection, LanguageDetection)
        return ValidationContext(
            facts_model_view={fact.fact_id: _value(by_name[name]) for name, fact in state.facts.items()},
            fact_sources={fact.fact_id: fact.source for fact in state.facts.values()},
            allowed=frozenset(), pages_model_view={}, vault=step.vault, locale=state.locale,
            lang_cfg=lang_cfg,
            lang_thresholds=self._config.lang_thresholds.get(lang_cfg.thresholds_from or "", UNCALIBRATED),
            supported_locales=tuple(step.agent.supported_locales),
            find_clear_pii=find_clear_pii,
            number_format=self._config.number_format)

    def _context(self, step: StepContext, request: GenerateRequest, state: RunState) -> ResponderContext:
        """`ResponderContext` del nodo: hechos en vista `model`, cierre de PII y configuración de idioma."""
        by_name = self._facts_model_view(step, state)
        resolve_ref = release_resolver(step.registry, step.release)
        facts_full = {name: fact.value for name, fact in state.facts.items()}
        validation = self._validation(step, state, by_name,
                                      lambda text: step.views.find_clear_pii(text, facts_full))
        return ResponderContext(
            gateway=self._gateway, clock=self._clock, ids=self._ids, resolve_ref=resolve_ref,
            locale=state.locale, degraded=False, release=state.release, turn_id=None,
            default_target_queue=step.agent.default_target_queue, priority=self._config.priority,
            facts_model_view_by_name=by_name, validation=validation, claims=request.claims,
            node_id=request.node_id, max_regenerations=self._config.max_regenerations)

    def _suggest_context(self, step: StepContext, request: SuggestRequest,
                         state: RunState) -> SuggesterContext:
        """`SuggesterContext` del nodo `suggest` (ADR 0026): catálogo de tools, hechos citables y validación.

        Una sugerencia no lleva PII en claro: además de los hechos `pii_direct` (M7 `find_clear_pii`), nada
        de lo que M7 tokenizó para el modelo (p. ej. la tarjeta o el correo que el cliente pegó en un
        mensaje: no es un hecho) vuelve en claro (M7 `find_tokenized_echo`)."""
        config: SuggestConfig = request.config
        resolve_ref = release_resolver(step.registry, step.release)
        by_name = self._facts_model_view(step, state)
        facts_full = {name: fact.value for name, fact in state.facts.items()}

        def clear_pii(text: str) -> list[str]:
            return [*step.views.find_clear_pii(text, facts_full),
                    *step.views.find_tokenized_echo(text, step.vault)]

        names = dict.fromkeys(
            m.group(1) for raw in (*config.reads, *config.optional_reads)
            if (m := _FACT_READ.fullmatch(raw)) is not None and m.group(1) in state.facts)
        citable = {name: state.facts[name].fact_id for name in names}
        validation = replace(self._validation(step, state, by_name, clear_pii),
                             allowed=frozenset(citable.values()))
        return SuggesterContext(
            gateway=self._gateway, clock=self._clock,
            prompt=resolve_ref(EntityKind.prompt, config.prompt_ref), locale=state.locale,
            goal=config.goal, inputs=request.inputs, citable=citable,
            tools=tuple(_entry(step, resolve_ref, ref) for ref in config.tools_allowed),
            actions=tuple(_entry(step, resolve_ref, ref) for ref in config.actions_allowed),
            escalation=request.escalation, max_items=config.max_items, validation=validation,
            degraded=step.degraded, max_regenerations=self._config.max_regenerations)


def _entry(step: StepContext, resolve_ref: Callable[[EntityKind, RefSpec], EntityRef], ref: RefSpec,
           ) -> ToolEntry:
    exact = resolve_ref(EntityKind.tool, ref)
    tool = step.registry.get(exact, ToolDef)
    if not tool.description or tool.args_schema is None:  # G0-24 lo impide en la validación estática
        raise InvalidRuntimeRef(f"la tool {exact} no tiene description y args_schema")
    # Un flow publicado trae las tools con versión exacta: el modelo y la plataforma ven `id@MAYOR`, la forma
    # con que el agente las declara (`leer_movimientos@1`); la exacta también se acepta.
    short = f"{exact.id}@{exact.version.split('.')[0]}"
    return ToolEntry(ref=short, exact=str(exact), description=tool.description,
                     args_schema=tool.args_schema)


def _value(entry: JsonValue) -> JsonValue:
    assert isinstance(entry, dict)
    return entry["value"]
