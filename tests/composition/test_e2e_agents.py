"""Los agentes nuevos de `registry-e2e` (copiloto del asesor y constructor): motor real, LLM guionado."""

import re
from decimal import Decimal
from pathlib import Path

import pytest

from agent_core.decision import ProviderError
from agent_core.decision.providers.jev import JevProvider, JevTransportError
from agent_core.domain import ActionState, DecisionModelDef, EntityRef, JsonValue, Message, Outcome
from agent_core.ports import GenerationResult
from testing.engine_world import EngineWorld, transfer_calibration
from testing.fakes.gateway import ScriptedGateway

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
        gateway.push(_gen({"kind": "final", "output": {"resumen": "Tiene saldo pendiente."}}))
        gateway.push(_gen({"text": "Tiene saldo pendiente.", "citations": []}))
        w.understands("continue")
        turn = w.turn(question)
        events = [e.type for e in w.audit.read(turn.run_id)]
        assert events.count("tool_called") >= 1 and "agent_step" in events
        # responde y vuelve a preguntar en el mismo turno: el siguiente mensaje ya es el pedido (no se pierde)
        assert turn.status == "open" and len(turn.messages) == 2
    steps = [e.type for e in w.audit.read(turn.run_id)].count("agent_step")
    assert steps == 4  # dos pedidos, dos pasos cada uno


def test_the_constructor_flow_writes_a_draft_proposal_and_validates_it_without_approving() -> None:
    import yaml

    from agent_core.composition.builder_tools import BUILDER_TOOL_DEFS, BuilderToolExecutor
    from agent_core.domain import Flow
    from agent_core.interpreter import AgentFinal, AgentStepResult, GenerateResult, Stop
    from testing.builders import principal
    from testing.fakes.agent import ScriptedAgent
    from tests.m02.harness import World as FlowWorld
    from tests.m02.harness import slot
    from tests.registry.helpers import bot, prompt_draft
    from tests.registry.service_world import World as RegistryWorld

    registry = RegistryWorld()
    w = FlowWorld()
    w.add(*BUILDER_TOOL_DEFS.values())
    executor = BuilderToolExecutor(registry.service, bot(), w.ids)
    raw = (E2E / "flows" / "construir@1.0.0.yaml").read_text("utf-8")
    raw = re.sub(r"([a-z_/]+)@1(?!\.)", r"\g<1>@1.0.0", raw)  # el motor solo corre referencias exactas
    raw = re.sub(r"(prompt_ref|template_ref): (t/[a-z_]+)$", r"\g<1>: \g<2>@1.0.0", raw, flags=re.M)
    flow = Flow.model_validate(yaml.safe_load(raw))
    draft = prompt_draft().model_dump(mode="json")
    agent = ScriptedAgent([AgentStepResult(AgentFinal(output={"changes": [draft]}), tokens=10,
                                           cost_usd=Decimal("0.001"))])
    supervisor = principal(type="builder", id="ana", roles=["constructor", "aprobador"],
                           attrs={"actor": "human"})
    state = w.persist(w.state(flow, node_id="redactar", principal=supervisor,
                              slots={"agente": slot("atencion"), "objetivo": slot("acortar el resumen")}))
    draft_ready = Message(kind="generated", text="Propuesta en borrador.", locale="es")
    w.responder.push(GenerateResult(message=draft_ready))
    done = w.step(state, tools=executor, agents=agent)

    assert done.stop is Stop.terminal and done.end_outcome is Outcome.resolved
    assert [a.state for a in done.state.actions] == [ActionState.verified, ActionState.verified]
    assert all(a.confirm_node_id is None and a.write_node_id for a in done.state.actions)  # sin confirm
    pid = done.state.facts["propuesta"].value["proposal_id"]  # type: ignore[index]
    detail = registry.service.get_proposal(str(pid))
    assert [(c.kind, c.id) for c in detail.changes] == [("prompt", "p/resumen_radicado")]
    assert detail.proposal.origin.value == "builder_chat"
    assert detail.proposal.state.value == "draft"  # el agente nunca aprueba ni publica
    assert done.state.facts["validacion"].value["valid"] is True  # type: ignore[index]


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
