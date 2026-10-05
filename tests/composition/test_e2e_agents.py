"""Los agentes nuevos de `registry-e2e` (copiloto del asesor y constructor): motor real, LLM guionado."""

import json
import re
from decimal import Decimal
from pathlib import Path

import pytest

from agent_core.composition import EngineConfig
from agent_core.composition.serve_ports import DemoContext
from agent_core.decision import ProviderError
from agent_core.decision.providers.jev import JevProvider, JevTransportError
from agent_core.domain import ActionState, DecisionModelDef, EntityRef, JsonValue, Outcome, ToolDef
from agent_core.guards import LangThresholds
from agent_core.ports import GenerationResult
from agent_core.views import FieldClassifier
from testing import e2e_demo
from testing.e2e_demo import _with_portuguese as _pt
from testing.engine_world import CATALOG, EngineWorld, transfer_calibration
from testing.fakes.clock import FakeClock
from testing.fakes.gateway import ScriptedGateway
from testing.fakes.ids import FakeIds
from testing.fakes.registry_dir import registry_from_releases

E2E = Path(__file__).parents[1] / "fixtures" / "registry-e2e"


def _gen(output: dict[str, JsonValue]) -> GenerationResult:
    return GenerationResult(output=output, tokens_in=50, tokens_out=20, cost_usd=Decimal("0.0003"),
                            model="modelo-sintetico-1")


def test_the_copilot_loops_ask_read_answer_and_ask_again() -> None:
    gateway = ScriptedGateway()
    w = EngineWorld(registry_root=E2E, releases=("copiloto-demo",), agent="copiloto-asesor", gateway=gateway,
                    calibrations={"cal-transfer-demo": transfer_calibration()})
    started = w.start()
    assert [m.text for m in started.first_turn.messages] == ["¿Qué necesitas saber o hacer con este cliente?"]

    for question in ("¿cuánto debe en la tarjeta?", "¿y cuál es su último movimiento?"):
        gateway.push(_gen({"kind": "tool_call", "tool": "leer_productos@1.0.0", "args": {}}))
        gateway.push(_gen({"kind": "final", "output": {"resumen": "Tiene saldo pendiente.", "datos": []}}))
        gateway.push(_gen({"text": "Tiene saldo pendiente.", "citations": []}))
        w.understands("continue")
        turn = w.turn(question)
        events = [e.type for e in w.audit.read(turn.run_id)]
        assert events.count("tool_called") >= 1 and "agent_step" in events
        # responde y vuelve a preguntar en el mismo turno: el siguiente mensaje ya es el pedido (no se pierde)
        assert turn.status == "open" and len(turn.messages) == 2
    steps = [e.type for e in w.audit.read(turn.run_id)].count("agent_step")
    assert steps == 4  # dos pedidos, dos pasos cada uno


class _CopilotGateway(ScriptedGateway):
    """Guion del nodo `agent`; la redacción del `respond` cita el hecho que recibe (no conoce su id antes)."""

    def __init__(self, draft: str) -> None:
        super().__init__()
        self._draft = draft

    def generate(self, prompt: EntityRef, inputs_model_view: dict[str, JsonValue], locale: str,
                 schema: dict[str, JsonValue] | None = None) -> GenerationResult:
        facts = inputs_model_view.get("facts")
        if isinstance(facts, dict):  # el `respond`: cita lo que M8 le entrega
            cited = [entry["fact_id"] for entry in facts.values() if isinstance(entry, dict)]
            self.push(_gen({"text": self._draft, "citations": cited}))
        return super().generate(prompt, inputs_model_view, locale, schema)


def _copilot_world(gateway: ScriptedGateway, classifier: FieldClassifier) -> EngineWorld:
    w = EngineWorld(registry_root=E2E, releases=("copiloto-demo",), agent="copiloto-asesor", gateway=gateway,
                    calibrations={"cal-transfer-demo": transfer_calibration()}, field_classifier=classifier)
    for tool_id in ("leer_movimientos", "leer_productos", "leer_pqr_cliente", "obtener_handoff",
                    "leer_transcript"):
        definition = w.registry.get(EntityRef(id=tool_id, version="1.0.0"), ToolDef)
        w.tools.register(definition, handler=e2e_demo._HANDLERS[tool_id])
    return w


def _ask_the_copilot(classifier: FieldClassifier) -> tuple[str, dict[str, JsonValue]]:
    """El asesor pregunta el saldo; devuelve la respuesta y lo que vio el modelo que redacta."""
    gateway = _CopilotGateway("El saldo adeudado de la Tarjeta Oro es de 1342.80 USD, según los productos.")
    gateway.push(_gen({"kind": "tool_call", "tool": "leer_productos@1.0.0", "args": {}}))
    gateway.push(_gen({"kind": "final", "output": {
        "resumen": "El cliente tiene un saldo pendiente.",
        "datos": [{"concepto": "saldo adeudado Tarjeta Oro", "fuente": "productos",
                   "valor": Decimal("1342.80"), "moneda": "USD"}]}}))
    w = _copilot_world(gateway, classifier)
    w.start()
    w.understands("continue")
    turn = w.turn("¿cuánto debe en la tarjeta?")
    drafting = gateway.calls[-1].inputs  # la última llamada es la redacción
    assert "facts" in drafting
    return turn.messages[0].text, drafting


def test_the_copilot_answer_carries_the_figure_when_the_agent_output_fields_are_classified() -> None:
    """Hallazgo del E2E: `resumen` sin clasificar se tokenizaba y el copiloto decía «contiene PII»."""
    text, drafting = _ask_the_copilot(e2e_demo.field_classifier(_demo_context()))
    assert "1342.80 USD" in text
    shown = json.dumps(drafting, default=str, ensure_ascii=False)
    assert "⟦" not in shown and "1342.8" in shown
    # el texto que escribió el modelo viaja envuelto como dato no confiable; la cifra, tal cual
    assert "<datos_no_confiables" in shown


def test_the_copilot_falls_back_to_the_template_when_the_agent_output_fields_are_not_classified() -> None:
    """Contrapeso: sin clasificar la salida del agente, M7 tokeniza todo y M8 no respalda la cifra."""
    text, drafting = _ask_the_copilot(FieldClassifier(CATALOG))
    assert "⟦pii:" in json.dumps(drafting, ensure_ascii=False)
    assert "1342.80" not in text  # el borrador no pasó M8 (cifra sin fuente): respondió la plantilla


def _schema_property_names(schema: dict[str, JsonValue]) -> set[str]:
    names: set[str] = set()
    properties = schema.get("properties")
    if isinstance(properties, dict):
        for name, child in properties.items():
            names.add(name)
            if isinstance(child, dict):
                names |= _schema_property_names(child)
    items = schema.get("items")
    if isinstance(items, dict):
        names |= _schema_property_names(items)
    return names


def test_every_field_of_the_copilot_output_schema_has_an_explicit_class_in_the_demo_catalog() -> None:
    import yaml

    flow = yaml.safe_load((E2E / "flows" / "asistir@1.0.0.yaml").read_text("utf-8"))
    agent = next(n for n in flow["nodes"] if n["type"] == "agent")
    fields = _schema_property_names(agent["config"]["output_schema"])
    classifier = e2e_demo.field_classifier(_demo_context())
    assert fields >= {"resumen", "datos", "valor", "fecha", "fuente"}
    unclassified = {f for f in fields if f != "datos" and classifier.lookup(f"agent.datos.{f}") is None
                    and classifier.lookup(f"agent.{f}") is None}
    assert unclassified == set()  # M7 trata lo no clasificado como pii_direct y lo tokeniza


def _demo_context() -> DemoContext:
    registry = registry_from_releases(E2E, ("copiloto-demo",))
    return DemoContext(clock=FakeClock(), ids=FakeIds(), registry=registry)


def _constructor_world():  # type: ignore[no-untyped-def]
    """El flow `construir` real contra el servicio del registry; cada test guiona el agente de `redactar`."""
    import yaml

    from agent_core.composition.builder_tools import BUILDER_TOOL_DEFS, BuilderToolExecutor
    from agent_core.composition.classification import load_catalog
    from agent_core.domain import Flow
    from agent_core.views import ViewService
    from testing.fakes.keys import FakeKeyProvider
    from tests.m02.harness import CATALOG as CATALOG_M02
    from tests.m02.harness import AllowAllAuthz, template
    from tests.m02.harness import World as FlowWorld
    from tests.registry.helpers import bot
    from tests.registry.service_world import World as RegistryWorld

    registry = RegistryWorld()
    w = FlowWorld()
    # el id de la propuesta se muestra: el overlay lo clasifica `public` (si no, M7 lo tokeniza)
    overlay = load_catalog([Path(__file__).parents[2] / "scripts" / "e2e" / "field-overlay.json"])
    w.views = ViewService(FakeKeyProvider.default(), AllowAllAuthz(), w.clock,
                          FieldClassifier({**CATALOG_M02, **overlay}))  # type: ignore[arg-type]
    w.add(*BUILDER_TOOL_DEFS.values())
    names = ("pedir_agente", "pedir_objetivo", "propuesta_creada", "propuesta_lista",
             "constructor_sin_borrador")
    for name in names:
        data = yaml.safe_load((E2E / "templates" / "t" / f"{name}@1.0.0.yaml").read_text("utf-8"))
        w.add(template(data["id"], data["locales"]["es"], data["locales"]["pt"]))
    executor = BuilderToolExecutor(registry.service, bot(), w.ids)
    raw = (E2E / "flows" / "construir@1.0.0.yaml").read_text("utf-8")
    raw = re.sub(r"([a-z_/]+)@1(?!\.)", r"\g<1>@1.0.0", raw)  # el motor solo corre referencias exactas
    raw = re.sub(r"(prompt_ref|template_ref): (t/[a-z_]+)$", r"\g<1>: \g<2>@1.0.0", raw, flags=re.M)
    flow = Flow.model_validate(yaml.safe_load(raw))
    return registry, w, flow, executor


def _run_constructor(changes: list[dict[str, JsonValue]]):  # type: ignore[no-untyped-def]
    from agent_core.interpreter import AgentFinal, AgentStepResult
    from testing.builders import principal
    from testing.fakes.agent import ScriptedAgent
    from tests.m02.harness import slot

    registry, w, flow, executor = _constructor_world()
    agent = ScriptedAgent([AgentStepResult(AgentFinal(output={"changes": changes}), tokens=10,
                                           cost_usd=Decimal("0.001"))])
    supervisor = principal(type="builder", id="ana", roles=["constructor", "aprobador"],
                           attrs={"actor": "human"})
    state = w.persist(w.state(flow, node_id="redactar", principal=supervisor,
                              slots={"agente": slot("atencion"), "objetivo": slot("acortar el resumen")}))
    return registry, w.step(state, tools=executor, agents=agent)


def test_the_constructor_flow_writes_a_draft_proposal_and_validates_it_without_approving() -> None:
    from agent_core.interpreter import Stop
    from tests.registry.helpers import prompt_draft

    registry, done = _run_constructor([prompt_draft().model_dump(mode="json")])

    assert done.stop is Stop.terminal and done.end_outcome is Outcome.resolved
    assert [a.state for a in done.state.actions] == [ActionState.verified, ActionState.verified]
    assert all(a.confirm_node_id is None and a.write_node_id for a in done.state.actions)  # sin confirm
    pid = done.state.facts["propuesta"].value["proposal_id"]  # type: ignore[index]
    detail = registry.service.get_proposal(str(pid))
    assert [(c.kind, c.id) for c in detail.changes] == [("prompt", "p/resumen_radicado")]
    assert detail.proposal.origin.value == "builder_chat"
    assert detail.proposal.state.value == "draft"  # el agente nunca aprueba ni publica
    assert done.state.facts["validacion"].value["valid"] is True  # type: ignore[index]
    # la respuesta nombra la propuesta (la plataforma la rastrea por su id)
    texts = [m.text for m in done.messages]
    assert len(texts) == 2 and all(str(pid) in text for text in texts), texts


def test_the_proposal_is_named_even_when_a_later_step_fails() -> None:
    from agent_core.interpreter import Stop

    broken = {"kind": "flow", "content": {"nodes": []}, "docs": {"description": "d", "rationale": "r",
                                                                     "changelog": "c"}}  # sin id ni version
    registry, done = _run_constructor([broken])

    assert done.stop is Stop.terminal and done.escalation is not None
    assert done.escalation.reason_code == "tool_failure"
    pid = done.state.facts["propuesta"].value["proposal_id"]  # type: ignore[index]
    assert registry.service.get_proposal(str(pid)).proposal.state.value == "draft"
    assert any(str(pid) in m.text for m in done.messages)  # "Abrí la propuesta …" salió antes del fallo


def test_the_agent_slot_takes_an_id_and_not_a_sentence() -> None:
    import yaml

    from agent_core.domain import SlotValidator
    from agent_core.interpreter.handlers.collect import validate_slot

    flow = yaml.safe_load((E2E / "flows" / "construir@1.0.0.yaml").read_text("utf-8"))
    config = next(n for n in flow["nodes"] if n["id"] == "pedir_agente")["config"]
    validator = SlotValidator.model_validate(config["validator"])

    assert validate_slot(validator, "cobros") == (True, "cobros")
    sentence = "Agente: cobros. Objetivo: un agente nuevo que atienda los casos de tipo Cobro indebido"
    assert validate_slot(validator, sentence) == (False, None)
    assert validate_slot(validator, "Cobros") == (False, None)


class _Refuse:
    """Transporte de JEV que registra el request y falla: solo interesa que el request se arme."""

    def __init__(self) -> None:
        self.requests: list[dict[str, JsonValue]] = []

    def send(self, request: dict[str, JsonValue], timeout_ms: int) -> dict[str, JsonValue]:
        self.requests.append(request)
        raise JevTransportError(503)


def test_the_real_jev_provider_can_build_its_request_for_every_conversational_agent() -> None:
    """JEV exige un enum no vacío por campo: un agente sin interrupciones lo rompe (hallazgo del E2E)."""
    for release, agent, model in (("copiloto-demo", "copiloto-asesor", "understand-copiloto"),
                                  ("constructor-demo", "constructor-chat", "understand-constructor")):
        w = EngineWorld(registry_root=E2E, releases=(release,), agent=agent,
                        calibrations={"cal-transfer-demo": transfer_calibration()})
        w.start()
        w.understands("continue")
        w.turn("hola")
        schema = w.jev.schemas[0]
        definition = w.registry.get(EntityRef(id=model, version="1.0.0"), DecisionModelDef)
        transport = _Refuse()
        with pytest.raises(ProviderError):  # llegó a enviar: el request se armó sin error de configuración
            JevProvider(transport).predict(definition.providers[0], {"text": "hola"}, schema, "es")
        assert set(transport.requests[0]["questions"]) >= {"command", "flow", "interrupt"}  # type: ignore[arg-type]


def _locales(w: EngineWorld, run_id: str) -> list[tuple[str, str]]:
    found = []
    for e in w.audit.read(run_id):
        guards = e.payload.guards if e.type == "turn_started" else None
        if guards is not None and guards.lang is not None:
            found.append((guards.lang.decision, guards.lang.locale))
    return found


def test_the_run_switches_between_spanish_and_portuguese_when_the_thresholds_are_configured() -> None:
    """Sin `lang_thresholds` el idioma nunca cambia (M6 §3.1.7); `serve --lang-thresholds` los aporta."""
    def run(config: EngineConfig | None) -> list[tuple[str, str]]:
        gateway = ScriptedGateway()
        calibrations = {"cal-transfer-demo": _pt(transfer_calibration())}
        w = EngineWorld(registry_root=E2E, releases=("copiloto-demo",), agent="copiloto-asesor",
                        gateway=gateway, calibrations=calibrations, config=config)
        w.start()
        texts = ("Qual é o saldo devedor do cartão do cliente?", "¿Cuál es el último movimiento del cliente?")
        for text in texts:
            gateway.push(_gen({"kind": "final", "output": {"resumen": "ok", "datos": []}}))
            gateway.push(_gen({"text": "ok", "citations": []}))
            w.understands("continue")
            turn = w.turn(text)
        return _locales(w, turn.run_id)

    assert run(None) == [("kept", "es"), ("kept", "es")]
    switching = run(EngineConfig(lang_thresholds={"lang-cal-demo": LangThresholds(
        switch_threshold=0.9, unsupported_threshold=0.9, min_distance=0.2)}))
    assert switching == [("switched", "pt"), ("switched", "es")]


def test_the_seeded_agents_that_serve_customers_have_an_eval_suite() -> None:
    """Sin `eval_suite` una propuesta no se puede probar, aprobar, publicar ni activar (ADR 0018 §4)."""
    from agent_core.registry.yaml_io import load_seed

    pinned, suites = load_seed(E2E)
    by_agent = {s.agent_id: s for s in suites}
    assert {"disputas", "recepcion"} <= set(by_agent)
    agents = {e.id: e for p in pinned for e in p.entities if getattr(e, "tools_allowed", None) is not None}
    for agent_id, suite in by_agent.items():
        allowed = {str(t).split("@")[0] for t in agents[agent_id].tools_allowed}
        for scenario in suite.scenarios:
            assert set(scenario.seed.tools) <= allowed, (agent_id, scenario.id)  # el sandbox solo siembra sus tools
