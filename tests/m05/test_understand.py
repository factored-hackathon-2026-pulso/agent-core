"""T-M5-07: Understand: slots `claimed`, `additional_flows` sin umbral, esquema por release."""

import dataclasses
from decimal import Decimal

import pytest

from agent_core.decision.calibration.artifact import CalibrationArtifact, Target
from agent_core.decision.service import DecisionService
from agent_core.decision.types import DecisionProvider, RawPrediction
from agent_core.decision.understand import UnderstandContext, UnderstandResult, UnderstandService
from agent_core.domain import Command, DecisionMade, DecisionModelDef, JsonValue
from testing.capture import RequestCapture
from testing.fakes.provider import Failure, ScriptedProvider, Timeout
from tests.m05.helpers import (
    Rig,
    artifact,
    identity_map,
    make_service,
    model_def,
    ref,
    scope,
)

FIELDS = ("command", "flow", "interrupt")
CLASSIFIER = "classifier"


def _art() -> CalibrationArtifact:
    thresholds = {
        ("command", "start_flow", CLASSIFIER, "es"): 0.6,
        ("command", "interrupt", CLASSIFIER, "es"): 0.6,
        ("command", "affirm", CLASSIFIER, "es"): 0.6,
        ("flow", "disputa", CLASSIFIER, "es"): 0.7,
        ("interrupt", "cancelar", CLASSIFIER, "es"): 0.3,  # recall: umbral bajo
    }
    calibrators = {(name, CLASSIFIER, "es"): identity_map() for name in FIELDS}
    return artifact(thresholds=thresholds, calibrators=calibrators,
                    target={"command": Target(metric="precision", value=0.9),
                            "interrupt": Target(metric="recall", value=0.95)})


def _rig(providers: tuple[str, ...] = (CLASSIFIER,)) -> Rig:
    return make_service(model_def(providers=providers, calibrated=FIELDS), artifacts=[_art()])


def _context(rig: Rig, **over: object) -> UnderstandContext:
    base: dict[str, object] = dict(
        model_ref=ref(model_def(providers=tuple(rig.providers), calibrated=FIELDS)),
        flows=["disputa", "saldo"], interrupts=["cancelar", "humano"], current_node="pedir_monto",
        confirm_pending=False, recent_turns=["hola", "quiero disputar un cargo"], token_vault=rig.vault,
        scope=scope())
    return UnderstandContext(**{**base, **over})  # type: ignore[arg-type]


def _raw(value: dict[str, JsonValue], **p: float | None) -> RawPrediction:
    return RawPrediction(value=value, p_raw={name: p.get(name, 0.9) for name in FIELDS})


def _run(rig: Rig, text: str = "quiero disputar",
         **over: object) -> tuple[UnderstandResult, list[DecisionMade]]:
    return UnderstandService(rig.service).run(text, _context(rig, **over), "es")


def test_t_m5_07_start_flow_marks_command_and_flow_only() -> None:
    rig = _rig()
    token = rig.vault.tokenize("500000", "amount", "monto")
    rig.providers[CLASSIFIER].push(_raw({"command": "start_flow", "flow": "disputa",
                                         "additional_flows": ["saldo"], "slots": {"monto": token}}))
    result, events = _run(rig)
    assert result.command is Command.start_flow and result.flow == "disputa"
    assert set(result.above_threshold) == {"command", "flow"}
    assert result.above_threshold == {"command": True, "flow": True}
    assert set(result.p_cal) == {"command", "flow"}
    assert result.additional_flows == ["saldo"]  # sin clave de umbral
    assert result.slots == {"monto": token}      # se entregan tal cual (claimed), sin validar
    assert result.interrupt is None
    assert len(events) == 1 and events[0].type == "decision_made"
    assert result.decision_id == events[0].payload.decision_id


def test_interrupt_marks_command_and_interrupt_using_the_recall_threshold() -> None:
    rig = _rig()
    rig.providers[CLASSIFIER].push(_raw({"command": "interrupt", "interrupt": "cancelar"}, interrupt=0.35))
    result, _ = _run(rig)
    assert set(result.above_threshold) == {"command", "interrupt"}
    assert result.above_threshold == {"command": True, "interrupt": True}  # 0.35 >= 0.3 (recall)
    assert result.interrupt == "cancelar" and result.flow is None


def test_other_commands_only_mark_command() -> None:
    rig = _rig()
    rig.providers[CLASSIFIER].push(_raw({"command": "affirm"}))
    result, _ = _run(rig)
    assert set(result.above_threshold) == {"command"} and result.above_threshold["command"] is True
    assert result.additional_flows == [] and result.slots == {}


def test_a_single_model_call_per_run() -> None:
    rig = _rig()
    rig.providers[CLASSIFIER].push(_raw({"command": "affirm"}))
    _, events = _run(rig)
    assert len(rig.providers[CLASSIFIER].calls) == 1 and len(events) == 1


def test_flow_outside_the_release_enum_retries_once_then_falls_back() -> None:
    rig = _rig(("jev", CLASSIFIER))
    rig.providers["jev"].push(_raw({"command": "start_flow", "flow": "inventado"}),
                              _raw({"command": "start_flow", "flow": "inventado"}))
    rig.providers[CLASSIFIER].push(_raw({"command": "start_flow", "flow": "saldo"}))
    result, events = _run(rig)
    assert len(rig.providers["jev"].calls) == 2
    assert result.flow == "saldo" and events[0].payload.fallback_depth == 1


def test_additional_flows_outside_the_enum_are_rejected_by_the_schema() -> None:
    rig = _rig()
    rig.providers[CLASSIFIER].push(_raw({"command": "affirm", "additional_flows": ["inventado"]}),
                                   _raw({"command": "affirm"}))
    result, _ = _run(rig)
    assert result.command is Command.affirm and len(rig.providers[CLASSIFIER].calls) == 2


def test_unknown_token_in_slots_puts_everything_below_threshold() -> None:
    rig = _rig()
    rig.providers[CLASSIFIER].push(_raw({"command": "start_flow", "flow": "disputa",
                                         "slots": {"monto": "⟦monto:9⟧"}}))
    result, _ = _run(rig)
    assert result.above_threshold == {"command": False, "flow": False}


def test_provider_input_carries_context_and_no_clear_text() -> None:
    rig = _rig()
    rig.providers[CLASSIFIER].push(_raw({"command": "affirm"}))
    _run(rig, "sí, ⟦doc:1⟧", confirm_pending=True)
    inputs = rig.providers[CLASSIFIER].calls[0][1]
    assert inputs == {"text": "sí, ⟦doc:1⟧", "recent_turns": ["hola", "quiero disputar un cargo"],
                      "current_node": "pedir_monto", "confirm_pending": True}
    capture = RequestCapture()
    capture.record(inputs)
    assert capture.leaks(["1234567890"]) == []


def test_schema_is_built_per_release_without_mutating_the_registered_model() -> None:
    rig = _rig()
    rig.providers[CLASSIFIER].push(_raw({"command": "affirm"}))
    definition = model_def(providers=(CLASSIFIER,), calibrated=FIELDS)
    _run(rig)
    schema = rig.providers[CLASSIFIER].schemas[0]
    properties = schema["properties"]
    assert isinstance(properties, dict)
    assert properties["command"] == {"type": "string", "enum": [c.value for c in Command]}
    assert properties["flow"] == {"type": "string", "enum": ["disputa", "saldo"]}
    assert properties["interrupt"] == {"type": "string", "enum": ["cancelar", "humano"]}
    assert properties["additional_flows"] == {"type": "array",
                                              "items": {"type": "string", "enum": ["disputa", "saldo"]}}
    assert properties["slots"] == {"type": "object", "additionalProperties": True}
    assert schema["additionalProperties"] is False and schema["required"] == ["command"]
    assert rig.registry.get(ref(definition), DecisionModelDef).output_schema == definition.output_schema


def test_exhausted_chain_yields_neutral_clarify_below_threshold() -> None:  # P3
    rig = _rig()
    rig.providers[CLASSIFIER].push(Timeout())
    result, events = _run(rig)
    assert result.command is Command.clarify and result.above_threshold == {"command": False}
    assert result.flow is None and result.slots == {} and result.additional_flows == []
    assert events[0].payload.provider_used == "none"


def test_provider_error_is_handled_like_exhaustion() -> None:
    rig = _rig()
    rig.providers[CLASSIFIER].push(Failure("x"))
    result, _ = _run(rig)
    assert result.command is Command.clarify


def test_repr_hides_slots() -> None:
    rig = _rig()
    rig.providers[CLASSIFIER].push(_raw({"command": "affirm", "slots": {"nota": "SECRETO-XYZ"}}))
    result, _ = _run(rig)
    assert "SECRETO-XYZ" not in repr(result)


# --- Segunda llamada: slots por `llm_structured` solo con `start_flow` (ADR 0005, enmienda 2026-09-29) ---

SLOTS_MODEL = DecisionModelDef.model_validate({
    "id": "understand-slots", "version": "1.0.0",
    "output_schema": {"type": "object", "additionalProperties": True},
    "calibrated_fields": [], "input_view": [], "providers": [{"provider": "llm_structured"}],
    "calibration": {"method": "none"}, "thresholds_from": None,
})
SLOTS_REF = ref(SLOTS_MODEL)


def _slots_rig(*, first: tuple[str, ...] = (CLASSIFIER,)) -> tuple[Rig, ScriptedProvider]:
    """Rig con el modelo de Understand y un segundo modelo de slots servido por `llm_structured`."""
    rig = _rig(first)
    rig.registry.add(SLOTS_MODEL)
    slots_provider = ScriptedProvider("llm_structured", clock=rig.clock)
    providers: dict[str, DecisionProvider] = {**rig.providers, "llm_structured": slots_provider}
    service = DecisionService(rig.registry, providers, rig.sources, rig.clock, rig.ids)
    return dataclasses.replace(rig, service=service), slots_provider


def _slots_raw(slots: dict[str, JsonValue], tokens: int = 7, cost: str = "0.0002") -> RawPrediction:
    return RawPrediction(value={"slots": slots}, p_raw={}, tokens=tokens, cost_usd=Decimal(cost),
                         model_version="llm-sint-1")


def test_start_flow_makes_a_second_llm_call_and_fills_slots_as_claimed() -> None:
    rig, slots_provider = _slots_rig()
    token = rig.vault.tokenize("500000", "amount", "monto")
    rig.providers[CLASSIFIER].push(_raw({"command": "start_flow", "flow": "disputa"}))
    slots_provider.push(_slots_raw({"monto": token}))
    result, events = _run(rig, slots_model_ref=SLOTS_REF)
    assert result.slots == {"monto": token}
    assert result.above_threshold == {"command": True, "flow": True}  # los slots nunca llevan umbral
    assert [e.payload.provider_used for e in events] == [CLASSIFIER, "llm_structured"]
    assert events[1].payload.above_threshold == {} and events[1].payload.model == SLOTS_REF
    assert result.decision_id == events[0].payload.decision_id  # la decisión de Understand es la primera
    assert result.model_calls == 2 and result.tokens == 7 and result.cost_usd == Decimal("0.0002")


def test_slots_call_input_carries_the_flow_and_no_clear_text() -> None:
    rig, slots_provider = _slots_rig()
    rig.providers[CLASSIFIER].push(_raw({"command": "start_flow", "flow": "disputa"}))
    slots_provider.push(_slots_raw({}))
    _run(rig, "disputo ⟦amount:1⟧", slots_model_ref=SLOTS_REF)
    inputs = slots_provider.calls[0][1]
    assert inputs == {"text": "disputo ⟦amount:1⟧", "recent_turns": ["hola", "quiero disputar un cargo"],
                      "current_node": "pedir_monto", "confirm_pending": False, "flow": "disputa"}
    assert slots_provider.schemas[0]["properties"] == {
        "slots": {"type": "object", "additionalProperties": True}}


@pytest.mark.parametrize("value", [{"command": "affirm"}, {"command": "interrupt", "interrupt": "cancelar"},
                                   {"command": "clarify"}])
def test_other_commands_keep_a_single_call(value: dict[str, JsonValue]) -> None:
    rig, slots_provider = _slots_rig()
    rig.providers[CLASSIFIER].push(_raw(value))
    result, events = _run(rig, slots_model_ref=SLOTS_REF)
    assert slots_provider.calls == [] and len(events) == 1 and result.model_calls == 1


def test_without_a_slots_model_there_is_no_second_call() -> None:
    rig, slots_provider = _slots_rig()
    rig.providers[CLASSIFIER].push(_raw({"command": "start_flow", "flow": "disputa"}))
    result, events = _run(rig)
    assert slots_provider.calls == [] and len(events) == 1 and result.slots == {}


def test_exhausted_first_call_never_reaches_the_slots_model() -> None:
    rig, slots_provider = _slots_rig()
    rig.providers[CLASSIFIER].push(Timeout())
    result, events = _run(rig, slots_model_ref=SLOTS_REF)
    assert result.command is Command.clarify and slots_provider.calls == [] and len(events) == 1


def test_slots_call_failure_keeps_the_command_and_leaves_slots_empty() -> None:
    rig, slots_provider = _slots_rig()
    rig.providers[CLASSIFIER].push(_raw({"command": "start_flow", "flow": "disputa"}))
    slots_provider.push(Failure("x"))
    result, events = _run(rig, slots_model_ref=SLOTS_REF)
    assert result.command is Command.start_flow and result.above_threshold["command"] is True
    assert result.slots == {} and len(events) == 2 and events[1].payload.provider_used == "none"
    assert result.model_calls == 2


def test_slots_from_the_second_call_replace_those_of_the_first() -> None:
    rig, slots_provider = _slots_rig()
    rig.providers[CLASSIFIER].push(_raw({"command": "start_flow", "flow": "disputa",
                                         "slots": {"viejo": "x"}}))
    slots_provider.push(_slots_raw({"nuevo": "y"}))
    result, _ = _run(rig, slots_model_ref=SLOTS_REF)
    assert result.slots == {"nuevo": "y"}


@pytest.mark.parametrize("p", [{"command": 0.5}, {"flow": 0.5}])
def test_slots_call_is_skipped_when_command_or_flow_is_below_threshold(p: dict[str, float]) -> None:
    rig, slots_provider = _slots_rig()
    rig.providers[CLASSIFIER].push(_raw({"command": "start_flow", "flow": "disputa"}, **p))
    result, events = _run(rig, slots_model_ref=SLOTS_REF)
    assert slots_provider.calls == [] and len(events) == 1 and result.model_calls == 1
    assert result.slots == {} and False in result.above_threshold.values()


def test_slots_call_is_skipped_without_a_flow() -> None:
    rig, slots_provider = _slots_rig()
    rig.providers[CLASSIFIER].push(_raw({"command": "start_flow"}))
    _run(rig, slots_model_ref=SLOTS_REF)
    assert slots_provider.calls == []
