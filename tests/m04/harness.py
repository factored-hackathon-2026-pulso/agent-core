"""Mundo de pruebas de M4: registro sintético, M2/M3/M10 reales y dobles de M5/M6/M11."""

from datetime import timedelta
from typing import Any

from agent_core.actions import ActionManager
from agent_core.domain import (
    ENTITY_KIND,
    Agent,
    AgentSelector,
    ConfirmAnswer,
    DecisionModelDef,
    DirectorySnapshot,
    EntityKind,
    EntityRef,
    Flow,
    OnBehalfOf,
    Principal,
    Prompt,
    RegistryEntity,
    Release,
    RunState,
    Template,
    ToolDef,
    TurnInput,
    TurnResult,
    directory_hash,
)
from agent_core.handoff import HandoffService
from agent_core.interpreter import CircuitBreaker, StepContext
from agent_core.ports import UnitOfWorkFactory
from agent_core.turn import TurnConfig, TurnEngine
from agent_core.views import TokenVault, ViewService
from testing.builders import principal as make_principal
from testing.builders import run_state
from testing.fakes.clock import FakeClock
from testing.fakes.decision import ScriptedDecision, make_decision
from testing.fakes.ids import FakeIds
from testing.fakes.keys import FakeKeyProvider
from testing.fakes.registry import InMemoryRegistry
from testing.fakes.responder import ScriptedResponder
from testing.fakes.storage import InMemoryAuditSink, InMemoryOutbox, InMemoryStore
from testing.fakes.tools import FakeToolExecutor, RecordedCall
from tests.m02.harness import CATALOG, AllowAllAuthz, SlowTools, tool_def
from tests.m02.harness import fact as make_fact
from tests.m04.helpers import (
    CommitCounter,
    FakeRuntime,
    FixedTrace,
    InMemoryTurnRecorder,
    PlainChain,
    ScriptedGuards,
    ScriptedUnderstand,
    cmd,
)
from tests.m10.helpers import HandoffAuthz, make_views

RUN_ID = "run-0001"
SESSION_ID = "session-0001"
RELEASE_ID = "rel-2026-09-28"
AGENT_REF = "atencion@1.0.0"

TEXTS: dict[str, tuple[str, str]] = {
    "t-aclarar": ("¿Puedes darme más detalles?", "Pode dar mais detalhes?"),
    "t-abstencion": ("Solo puedo ayudarte con tus tarjetas.", "So posso ajudar com seus cartoes."),
    "t-traspaso": ("Te paso con una persona del equipo.", "Vou passar para uma pessoa da equipe."),
    "t-acuse": ("Anoté tu otra solicitud, la vemos después.", "Anotei seu outro pedido."),
    "t-oferta": ("¿Quieres que ahora veamos tu otra solicitud?", "Quer ver seu outro pedido agora?"),
    "t-idioma": ("Solo atiendo en español y portugués.", "Atendo apenas em espanhol e portugues."),
    "t-largo": ("Tu mensaje es demasiado largo.", "Sua mensagem e longa demais."),
    "t-pedir": ("Cuéntame qué pasó con el cargo.", "Conte o que houve com a cobranca."),
    "t-pedir-tarjeta": ("¿Cuál tarjeta quieres bloquear?", "Qual cartao quer bloquear?"),
    "t-resumen": ("Voy a radicar tu disputa. ¿Confirmas?", "Vou registrar sua disputa. Confirma?"),
    "t-reprompt": ("¿Confirmas la disputa, sí o no?", "Confirma a disputa, sim ou nao?"),
    "t-listo": ("Listo, radiqué tu disputa.", "Pronto, registrei sua disputa."),
    "t-cancelado": ("Dejé la disputa sin radicar.", "Deixei a disputa sem registrar."),
}
ENGINE_TEMPLATES = {
    "clarify": "t-aclarar@1.0.0",
    "abstain": "t-abstencion@1.0.0",
    "handoff": "t-traspaso@1.0.0",
    "pending_ack": "t-acuse@1.0.0",
    "pending_offer": "t-oferta@1.0.0",
    "unsupported_language": "t-idioma@1.0.0",
    "input_too_large": "t-largo@1.0.0",
}


def agent_data(id: str = "atencion", **over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": id,
        "version": "1.0.0",
        "mode": "conversational",
        "entry_flow": "disputa@1.0.0",
        "invocable_by": ["customer"],
        "min_auth_level": "session",
        "subject_kinds": ["customer"],
        "supported_locales": ["es", "pt"],
        "default_locale": "es",
        "budgets": {
            "max_nodes_per_turn": 40,
            "max_model_calls_per_turn": 3,
            "max_tokens_per_run": 20000,
            "max_cost_per_run": "0.50",
            "max_wall_ms_per_turn": 8000,
        },
        "templates": ENGINE_TEMPLATES,
        "max_clarifications": 2,
        "on_clarify_exhausted": "end",
        "default_target_queue": "general",
    }
    return base | over


def node(id: str, type: str, config: dict[str, Any], **next: str) -> dict[str, Any]:
    return {"id": id, "type": type, "config": config, "next": next}


def flow(id: str, priority: int, *nodes: dict[str, Any]) -> Flow:
    return Flow.model_validate({"id": id, "version": "1.0.0", "priority": priority, "nodes": list(nodes)})


def disputa_nodes(verified_to: str = "listo") -> list[dict[str, Any]]:
    return [
        node(
            "pedir",
            "collect",
            {"slot": "descripcion", "prompt_ref": "t-pedir@1.0.0", "max_attempts": 3},
            ok="confirmar",
            max_attempts="esc",
        ),
        node(
            "confirmar",
            "confirm",
            {
                "action": {"tool": "radicar_pqr@1.0.0", "args": {"descripcion": "slots.descripcion"}},
                "summary_template": "t-resumen@1.0.0",
                "reprompt_template": "t-reprompt@1.0.0",
                "max_attempts": 5,
            },
            yes="radicar",
            no="fin_cancelado",
            unclear="confirmar",
            max_attempts="fin_cancelado",
        ),
        node(
            "radicar",
            "tool",
            {"action_from": "confirmar", "save_as": "pqr"},
            ok="verificar",
            uncertain="verificar",
            denied="esc",
        ),
        node(
            "verificar",
            "verify",
            {
                "readback": "obtener_pqr@1.0.0",
                "by": "idempotency_key",
                "predicate": {"==": [{"var": "readback.status"}, "Open"]},
                "save_as": "pqr_verificada",
            },
            verified=verified_to,
            failed="esc",
        ),
        node("listo", "respond", {"template_ref": "t-listo@1.0.0"}, next="fin"),
        node("fin", "end", {"outcome": "resolved"}),
        node("fin_cancelado", "respond", {"template_ref": "t-cancelado@1.0.0"}, next="fin_c"),
        node("fin_c", "end", {"outcome": "cancelled"}),
        node("esc", "escalate", {"reason_code": "verification_failed"}),
    ]


DISPUTA = flow("disputa", 50, *disputa_nodes())
# Variante cuyo `verify` deja al usuario esperando (pregunta con `await`) antes de terminar.
DISPUTA_LARGA = flow(
    "disputa-larga",
    40,
    *disputa_nodes("pregunta"),
    node("pregunta", "respond", {"template_ref": "t-listo@1.0.0", "await": True}, next="fin"),
)


BLOQUEAR = flow(
    "bloquear-tarjeta",
    80,
    node("pedir_tarjeta", "collect", {"slot": "tarjeta", "prompt_ref": "t-pedir-tarjeta@1.0.0"}, ok="fin"),
    node("fin", "end", {"outcome": "resolved"}),
)
PROCESAR = flow(
    "procesar",
    10,
    node(
        "leer", "tool", {"tool": "obtener_dato@1.0.0", "args": {}, "save_as": "dato"}, ok="fin", failed="fin"
    ),
    node("fin", "end", {"outcome": "completed", "output_map": {"dato": "facts.dato.value"}}),
)
GENERAR = flow(
    "generar",
    10,
    node(
        "g",
        "respond",
        {
            "generate": {
                "prompt_ref": "p-resumen@1.0.0",
                "allowed_facts": [],
                "fallback_template_ref": "t-listo@1.0.0",
            }
        },
        next="fin",
    ),
    node("fin", "end", {"outcome": "resolved"}),
)
PROMPT = Prompt.model_validate(
    {
        "id": "p-resumen",
        "version": "1.0.0",
        "locales": {"es": "resume", "pt": "resume"},
        "model_profile": "perfil@1.0.0",
    }
)
# Reception (ADR 0021): asks for the problem and transfers. The tests seed the directory fact and the
# routing decision, so the flow needs neither `directory/list` nor `decide` (Task 9 covers them end to end).
RECEPCION = flow(
    "recepcion",
    10,
    node("entender", "collect", {"slot": "problema", "prompt_ref": "t-pedir@1.0.0"}, ok="transferir",
         max_attempts="esc"),
    node(
        "transferir",
        "transfer",
        {"target_from": "decisions.ruta.choice", "directory_from": "directorio",
         "packet": {"reason": "routed", "slots": ["problema"]}},
        rejected="esc",
    ),
    node("esc", "escalate", {"reason_code": "policy:transfer_rejected"}),
)
# A reception whose entry node is the transfer: reached during `start_run` (P6, `no_turn`).
RECEPCION_DIRECTA = flow(
    "recepcion-directa",
    10,
    node(
        "transferir",
        "transfer",
        {"target_from": "decisions.ruta.choice", "directory_from": "directorio",
         "packet": {"reason": "routed", "slots": []}},
        rejected="esc",
    ),
    node("esc", "escalate", {"reason_code": "policy:transfer_rejected"}),
)
UNDERSTAND_MODEL = DecisionModelDef.model_validate({
    "id": "understand", "version": "1.0.0",
    "output_schema": {"type": "object", "additionalProperties": True},
    "calibrated_fields": [], "input_view": [], "providers": [{"provider": "llm_structured"}],
    "calibration": {"method": "none"}, "thresholds_from": None,
})
RECEPTION_RELEASE_ID = "rel-recepcion-1"
SPECIALIST_RELEASE_ID = "rel-disputas-1"
OTHER_RELEASE_ID = "rel-saldos-1"
DIRECTORY = "customer-care"
DEFAULT_ACCEPTS: dict[str, Any] = {"slots": {"problema": {"type": "string", "required": True}}}
TAREA_BUG = flow(
    "tarea-bug",
    10,
    node("esperar", "respond", {"template_ref": "t-listo@1.0.0", "await": True}, next="fin"),
    node("fin", "end", {"outcome": "completed"}),
)


class FakeRuntimeFactory:
    """Arma un `TurnRuntime` sobre M2/M7 reales (C1). Cuenta cuántas veces se abre."""

    def __init__(self, world: "World") -> None:
        self.world = world
        self.opened = 0

    def open(self, state: RunState, principal: Principal, on_behalf_of: OnBehalfOf | None) -> FakeRuntime:
        self.opened += 1
        w = self.world
        keys = FakeKeyProvider.default()
        agent = w.registry.get(state.agent, Agent)
        step = StepContext(
            release=w.release(),
            agent=agent,
            locale=state.locale,
            clock=w.clock,
            degraded=False,
            registry=w.registry,
            tools=w.step_tools,
            decisions=w.decisions,
            actions=w.manager,
            responder=w.responder,
            views=ViewService(keys, AllowAllAuthz(), w.clock, w.classifier),  # type: ignore[arg-type]
            vault=TokenVault(state.run_id, keys, w.ids),
            ids=w.ids,
            uow_factory=w.uow_factory,
            breaker=w.breaker,
        )
        return FakeRuntime(step)


class World:
    def __init__(
        self,
        *,
        agent_over: dict[str, Any] | None = None,
        interrupts: list[dict[str, Any]] | None = None,
        extra: tuple[RegistryEntity, ...] = (),
        guards_advance: timedelta = timedelta(0),
        understand_advance: timedelta = timedelta(0),
        recorder_advance: timedelta = timedelta(0),
        tool_advance: timedelta = timedelta(0),
        config: TurnConfig | None = None,
        chain: Any = None,
    ) -> None:
        from agent_core.views import FieldClassifier

        self.clock = FakeClock()
        self.ids = FakeIds()
        self.store = InMemoryStore()
        self.uow_factory: UnitOfWorkFactory = CommitCounter(self.store.uow)
        self.registry = InMemoryRegistry()
        self.tools = FakeToolExecutor(self.ids)
        self.step_tools: Any = SlowTools(self.tools, self.clock, tool_advance) if tool_advance else self.tools
        self.decisions = ScriptedDecision()
        self.responder = ScriptedResponder()
        self.manager = ActionManager(self.ids, self.clock)
        self.breaker = CircuitBreaker()
        self.classifier = FieldClassifier(CATALOG)
        self._interrupts = (
            interrupts
            if interrupts is not None
            else [
                {
                    "id": "fraude",
                    "priority": 100,
                    "action": {"type": "escalate", "target_queue": "fraude", "priority": "critical"},
                },
                {
                    "id": "reemplazo",
                    "priority": 90,
                    "action": {"type": "start_flow", "flow": "bloquear-tarjeta@1.0.0"},
                },
            ]
        )
        self.agent = Agent.model_validate(agent_data(**(agent_over or {})))
        self.entities: list[RegistryEntity] = []
        self.add(
            self.agent,
            Agent.model_validate(
                agent_data("tarea", mode="task", entry_flow="procesar@1.0.0", subject_kinds=[])
            ),
            Agent.model_validate(
                agent_data("tarea-bug", mode="task", entry_flow="tarea-bug@1.0.0", subject_kinds=[])
            ),
            DISPUTA,
            DISPUTA_LARGA,
            BLOQUEAR,
            PROCESAR,
            TAREA_BUG,
            GENERAR,
            PROMPT,
            *(
                Template(id=tid, version="1.0.0", locales={"es": es, "pt": pt})
                for tid, (es, pt) in TEXTS.items()
            ),
            *extra,
        )
        write = tool_def("radicar_pqr", "write_reversible", source="pqr")
        readback = tool_def("obtener_pqr", "read", source="pqr")
        self.add_tool(write, handler=lambda a: {"status": "Open", "pqr_id": "pqr-demo-1", **a})
        self.add(readback)
        self.tools.register_readback(readback, of=EntityRef(id="radicar_pqr", version="1.0.0"))
        self.add_tool(tool_def("obtener_dato", "read"), handler=lambda a: {"ok": True})
        for agent_id in ("atencion", "tarea", "tarea-bug"):
            self.registry.add_release(self.release(), agent_id, alias="prod")
        self.principal = make_principal()
        self.guards = ScriptedGuards(self.clock, guards_advance)
        self.understand = ScriptedUnderstand(self.clock, understand_advance)
        self.recorder = InMemoryTurnRecorder(self.clock, recorder_advance)
        self.runtimes = FakeRuntimeFactory(self)
        keys = FakeKeyProvider.default()
        authz = HandoffAuthz()
        self.handoff = HandoffService(
            uow_factory=self.store.uow,
            registry=self.registry,
            views=make_views(authz, keys),
            authz=authz,
            keys=keys,
            clock=self.clock,
            ids=self.ids,
        )
        self.audit = InMemoryAuditSink(self.store)
        self.chain = chain if chain is not None else PlainChain()
        self._config = config or TurnConfig()
        self.engine = TurnEngine(**self.engine_kwargs())
        self._n = 0

    def engine_kwargs(self) -> dict[str, Any]:
        return {
            "uow_factory": self.uow_factory,
            "registry": self.registry,
            "clock": self.clock,
            "ids": self.ids,
            "guards": self.guards,
            "understand": self.understand,
            "actions": self.manager,
            "handoff": self.handoff,
            "recorder": self.recorder,
            "chain": self.chain,
            "audit": self.audit,
            "runtimes": self.runtimes,
            "trace": FixedTrace(),
            "config": self._config,
        }

    # --- registro -----------------------------------------------------------------------------------

    def add(self, *entities: RegistryEntity) -> None:
        for entity in entities:
            if entity not in self.entities:
                self.entities.append(entity)
        self.registry.add(*entities)

    def add_tool(self, definition: ToolDef, **kwargs: Any) -> None:
        self.add(definition)
        self.tools.register(definition, **kwargs)

    @property
    def release_id(self) -> str:
        return RELEASE_ID

    def release(self) -> Release:
        entities: dict[EntityKind, dict[str, str]] = {}
        for entity in self.entities:
            entities.setdefault(ENTITY_KIND[type(entity)], {})[entity.id] = entity.version
        return Release.model_validate(
            {
                "id": RELEASE_ID,
                "status": "active",
                "entities": entities,
                "interrupts": self._interrupts,
                "language_detection": "lang@1.0.0",
            }
        )

    def text(self, template_id: str, locale: str = "es") -> str:
        return TEXTS[template_id][0 if locale == "es" else 1]

    # --- estado -------------------------------------------------------------------------------------

    def open_run(self, *, active: bool = False, **over: Any) -> RunState:
        base: dict[str, Any] = {
            "run_id": RUN_ID,
            "release": RELEASE_ID,
            "agent": AGENT_REF,
            "session_id": SESSION_ID,
            "created_at": self.clock.now(),
            "last_activity_at": self.clock.now(),
            "inactive_after": self.clock.now() + timedelta(minutes=30),
            "turn_count": 1,
        }
        if active:
            base |= {
                "active_flow": {"flow": "disputa@1.0.0", "node_id": "pedir"},
                "awaiting": "slot",
                "awaiting_node_id": "pedir",
            }
        state = run_state(**(base | over))
        with self.store.uow() as uow:
            saved = uow.save_run(state, 0)
            uow.commit()
        return saved

    def saved(self) -> RunState:
        return self.store.runs[RUN_ID]

    def events(self) -> list[Any]:
        return self.audit.read(RUN_ID)

    def event_types(self) -> list[str]:
        return [e.type for e in self.events()]

    def outbox(self) -> InMemoryOutbox:
        return InMemoryOutbox(self.store)

    def write_calls(self) -> list[RecordedCall]:
        return [c for c in self.tools.calls if c.tool.id == "radicar_pqr"]

    # --- turnos -------------------------------------------------------------------------------------

    def turn(
        self,
        text: str = "hola",
        *,
        client_turn_id: str | None = None,
        confirm: tuple[str, str] | None = None,
        lang: str | None = None,
    ) -> TurnResult:
        self._n += 1
        turn = TurnInput(
            session_id=SESSION_ID,
            text=text,
            channel="web",
            lang=lang,  # type: ignore[arg-type]
            client_turn_id=client_turn_id or f"c-{self._n}",
            confirm=ConfirmAnswer(token=confirm[0], answer=confirm[1]) if confirm else None,  # type: ignore[arg-type]
        )
        return self.engine.handle_turn(self.principal, None, turn)

    def turn_confirm(self, token: str, answer: str, **kwargs: Any) -> TurnResult:
        return self.turn("", confirm=(token, answer), **kwargs)

    def at_confirm(self) -> TurnResult:
        """Lleva un run nuevo hasta `confirmar` (acción `proposed`) con dos turnos."""
        self.open_run()
        self.understand.push(cmd("start_flow", flow="disputa"))
        self.turn("quiero disputar un cargo")
        self.understand.push(cmd("continue"))
        result = self.turn("cargo desconocido de cincuenta dólares")
        assert result.confirmation is not None
        return result

    def seed_at_confirm(self) -> Any:
        """Deja el run en `confirmar` con una acción `proposed` real, manejando M2 directo (sin M4).

        Devuelve el `ConfirmationPrompt`. Reinicia el contador de commits: lo que sigue lo mide la prueba."""
        from dataclasses import replace

        from agent_core.interpreter import NO_RESUME, Resume, advance, begin_turn, start_flow

        state = self.open_run(turn_count=2)
        state = start_flow(begin_turn(state, self.clock), DISPUTA)
        ctx = replace(
            self.runtimes.open(state, self.principal, None).step, turn_id="turn-seed", release=self.release()
        )
        state = advance(state, ctx, NO_RESUME).state
        out = advance(begin_turn(state, self.clock), ctx, Resume("slot_answer", "cargo desconocido"))
        assert out.confirmation is not None
        final = out.state.model_copy(update={"awaiting": "confirmation", "awaiting_node_id": "confirmar"})
        with self.store.uow() as uow:
            uow.save_run(final, final.state_version)
            uow.commit()
        self.uow_factory.commits = 0  # type: ignore[attr-defined]
        return out.confirmation

    # --- transfer (ADR 0021) -------------------------------------------------------------------------

    specialist_release_id = SPECIALIST_RELEASE_ID

    def _specialist(
        self, agent_id: str, accepts: dict[str, Any], over: dict[str, Any] | None = None
    ) -> Agent:
        data = agent_data(
            agent_id,
            entry_flow="disputa@1.0.0",
            understand="understand@1.0.0",
            routing={"directory": DIRECTORY, "summary": f"{agent_id} (sintético)", "examples": []},
            accepts=accepts,
        )
        return Agent.model_validate(data | (over or {}))

    def reception(
        self,
        *,
        choice: str = "disputas",
        specialist_over: dict[str, Any] | None = None,
        accepts: dict[str, Any] | None = None,
        origin_depth: int | None = None,
        open_run: bool = True,
    ) -> RunState | None:
        """Registers `recepcion` and `recepcion-directa`, the specialist `disputas` and a published `saldos`
        left out of the snapshot, each under `prod` with its own release. Then (unless `open_run=False`, for
        `start_run` tests) opens the reception run waiting on `entender`, with the directory fact and the
        routing decision already in its state."""
        contract = accepts if accepts is not None else DEFAULT_ACCEPTS
        disputas = self._specialist("disputas", contract, specialist_over)
        saldos = self._specialist("saldos", DEFAULT_ACCEPTS)
        recepcion = Agent.model_validate(agent_data("recepcion", entry_flow="recepcion@1.0.0"))
        directa = Agent.model_validate(agent_data("recepcion-directa", entry_flow="recepcion-directa@1.0.0"))
        self.add(recepcion, directa, disputas, saldos, RECEPCION, RECEPCION_DIRECTA, UNDERSTAND_MODEL)
        full = self.release()
        for agent_id in ("recepcion", "recepcion-directa"):
            self.registry.add_release(full.model_copy(update={"id": RECEPTION_RELEASE_ID}), agent_id)
        self.registry.add_release(full.model_copy(update={"id": SPECIALIST_RELEASE_ID}), "disputas")
        self.registry.add_release(full.model_copy(update={"id": OTHER_RELEASE_ID}), "saldos")
        entry = {
            "agent_id": "disputas", "release_id": SPECIALIST_RELEASE_ID, "summary": "disputas (sintético)",
            "examples": [], "accepts": contract, "supported_locales": ["es", "pt"],
        }
        snapshot = DirectorySnapshot.model_validate({
            "directory": DIRECTORY,
            "hash": directory_hash([("disputas", SPECIALIST_RELEASE_ID), ("saldos", OTHER_RELEASE_ID)]),
            "entries": [entry],
        })
        directorio = {**snapshot.model_dump(mode="json"), "choices": snapshot.choices}
        if not open_run:
            return None
        decision = make_decision({"choice": choice}, {"choice": True}).decision
        over: dict[str, Any] = {}
        if origin_depth is not None:
            over["origin"] = {
                "kind": "transfer", "transfer_id": "transfer-0000", "from_run_id": "run-0000",
                "from_agent": "recepcion@1.0.0", "from_release_id": RELEASE_ID, "from_event_hash": "c" * 64,
                "depth": origin_depth,
            }
        return self.open_run(
            agent="recepcion@1.0.0",
            release=RECEPTION_RELEASE_ID,
            active_flow={"flow": "recepcion@1.0.0", "node_id": "entender"},
            awaiting="slot",
            awaiting_node_id="entender",
            facts={"directorio": make_fact(directorio)},
            decisions={"ruta": decision},
            **over,
        )

    def agent_selector(self, agent_id: str = "atencion") -> AgentSelector:
        return AgentSelector(id=agent_id, alias="prod")
