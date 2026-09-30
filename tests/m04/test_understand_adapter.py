"""Fase B: `DecisionUnderstand` cablea `UnderstandPort` a `UnderstandService` de M5 (real)."""

from decimal import Decimal
from typing import TYPE_CHECKING

import pytest

from agent_core.decision import (
    DecisionConfigError,
    DecisionProvider,
    DecisionService,
    RawPrediction,
    UnderstandService,
)
from agent_core.decision.calibration import (
    CalibrationArtifact,
    InMemoryCalibrationSource,
    IsotonicMap,
    Target,
)
from agent_core.domain import (
    Agent,
    Command,
    DecisionMade,
    DecisionModelDef,
    JsonValue,
    RunState,
    TranscriptEntry,
)
from agent_core.turn import DecisionUnderstand, TurnEngine, UnderstandRequest
from testing.fakes.provider import ScriptedProvider
from testing.fakes.transcript import InMemoryTranscript
from tests.m04.harness import RUN_ID, World

if TYPE_CHECKING:
    from agent_core.turn import UnderstandPort

    def _conforms(x: DecisionUnderstand) -> UnderstandPort:
        return x

FIELDS = ("command", "flow", "interrupt")
UNDERSTAND = DecisionModelDef.model_validate({
    "id": "understand", "version": "1.0.0",
    "output_schema": {"type": "object", "additionalProperties": True},
    "calibrated_fields": list(FIELDS), "input_view": [], "providers": [{"provider": "classifier"}],
    "calibration": {"method": "isotonic", "run": "cal-1"}, "thresholds_from": "cal-1",
})
SLOTS = DecisionModelDef.model_validate({
    "id": "understand-slots", "version": "1.0.0",
    "output_schema": {"type": "object", "additionalProperties": True},
    "calibrated_fields": [], "input_view": [], "providers": [{"provider": "llm_structured"}],
    "calibration": {"method": "none"}, "thresholds_from": None,
})
ARTIFACT = CalibrationArtifact(
    run_id="cal-1", split_hash="b" * 64, method="isotonic",
    calibrators={(f, "classifier", "es"): IsotonicMap(xs=[0.0, 1.0], ys=[0.0, 1.0]) for f in FIELDS},
    thresholds={("command", "start_flow", "classifier", "es"): 0.6,
                ("flow", "disputa", "classifier", "es"): 0.6},
    target={"command": Target(metric="precision", value=0.9)})


class Rig:
    def __init__(self, *, slots: bool = True, understand: str | None = "understand@1.0.0",
                 recent_turns: int = 2) -> None:
        self.w = World(agent_over={"understand": understand,
                                   "slots_model": "understand-slots@1.0.0" if slots else None},
                       extra=(UNDERSTAND, SLOTS))
        self.classifier = ScriptedProvider("classifier", clock=self.w.clock)
        self.llm = ScriptedProvider("llm_structured", clock=self.w.clock)
        providers: dict[str, DecisionProvider] = {"classifier": self.classifier, "llm_structured": self.llm}
        service = DecisionService(self.w.registry, providers, InMemoryCalibrationSource({"cal-1": ARTIFACT}),
                                  self.w.clock, self.w.ids)
        self.transcript = InMemoryTranscript()
        self.adapter = DecisionUnderstand(
            UnderstandService(service), self.transcript, recent_turns=recent_turns)

    def request(self, text: str = "[model]quiero disputar") -> UnderstandRequest:
        w = self.w
        state: RunState = w.open_run()
        runtime = w.runtimes.open(state, w.principal, None)
        return UnderstandRequest(
            text_model=text, state=state, release=w.release(), agent=w.registry.get(state.agent, Agent),
            locale="es", awaiting_confirmation=False, current_node=None, turn_id="turn-0002",
            step=runtime.step)

    def say(self, role: str, text: str, turn: str = "turn-0001") -> None:
        self.transcript.append(TranscriptEntry.model_validate(
            {"run_id": RUN_ID, "turn_id": turn, "role": role, "text_model": text, "reason": None}))


def _raw(value: dict[str, JsonValue]) -> RawPrediction:
    return RawPrediction(value=value, p_raw={f: 0.9 for f in FIELDS}, tokens=5, cost_usd=Decimal("0.001"))


def test_traduce_el_resultado_de_m5_al_outcome_de_m4() -> None:
    rig = Rig(slots=False)
    rig.classifier.push(_raw({"command": "start_flow", "flow": "disputa"}))
    outcome = rig.adapter.run(rig.request())
    assert outcome.command is Command.start_flow and outcome.flow == "disputa"
    assert outcome.above_threshold == {"command": True, "flow": True}
    (event,) = outcome.events
    assert isinstance(event, DecisionMade) and outcome.decision_id == event.payload.decision_id
    assert outcome.cost_usd == Decimal("0.001") and outcome.model_calls == 1 and outcome.tokens == 5


def test_el_contexto_sale_de_la_release_del_agente_y_del_transcript() -> None:
    rig = Rig(slots=False)
    rig.say("user", "hola")
    rig.say("rejected_draft", "borrador")
    rig.say("assistant", "buenas")
    rig.say("user", "quiero ayuda")
    rig.classifier.push(_raw({"command": "affirm"}))
    rig.adapter.run(rig.request())
    _, inputs, _ = rig.classifier.calls[0]
    assert inputs["recent_turns"] == ["buenas", "quiero ayuda"]  # n = 2, sin borradores rechazados
    schema = rig.classifier.schemas[0]["properties"]
    assert isinstance(schema, dict)
    assert schema["flow"] == {"type": "string", "enum": sorted(rig.w.release().entities["flow"])}  # type: ignore[index]
    assert schema["interrupt"] == {"type": "string", "enum": ["fraude", "reemplazo"]}


def test_segunda_llamada_de_slots_con_el_modelo_fijado_por_la_release() -> None:
    rig = Rig()
    rig.classifier.push(_raw({"command": "start_flow", "flow": "disputa"}))
    rig.llm.push(RawPrediction(value={"slots": {"monto": "100"}}, p_raw={}, tokens=7,
                               cost_usd=Decimal("0.002")))
    outcome = rig.adapter.run(rig.request())
    assert outcome.slots == {"monto": "100"} and len(outcome.events) == 2
    assert outcome.model_calls == 2 and outcome.tokens == 12 and outcome.cost_usd == Decimal("0.003")
    assert rig.llm.calls[0][1]["flow"] == "disputa"


def test_scope_lleva_run_release_turno_y_sesion() -> None:
    rig = Rig(slots=False)
    rig.classifier.push(_raw({"command": "affirm"}))
    outcome = rig.adapter.run(rig.request())
    event = outcome.events[0]
    assert (event.run_id, event.turn_id, event.release) == (RUN_ID, "turn-0002", rig.w.release_id)
    assert event.session_id is not None


def test_agente_sin_understand_es_error_de_configuracion() -> None:
    rig = Rig(understand=None)
    with pytest.raises(DecisionConfigError):
        rig.adapter.run(rig.request())


def test_el_motor_usa_el_adaptador_real_en_el_paso_8() -> None:
    rig = Rig(slots=False)
    w = rig.w
    w.understand = rig.adapter  # type: ignore[assignment]
    w.engine = TurnEngine(**w.engine_kwargs())
    w.open_run()
    rig.classifier.push(_raw({"command": "out_of_scope"}))
    rig.classifier.push()  # sin más pasos
    result = w.turn("hola")
    assert result.messages and "command_emitted" in w.event_types() and "decision_made" in w.event_types()
