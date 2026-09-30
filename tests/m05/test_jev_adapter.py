"""Adaptador de JEV contra el contrato real (`POST /v1/systemone`, preguntas tipadas)."""

from decimal import Decimal

import pytest

from agent_core.decision.providers.jev import JevProvider, JevTransport, JevTransportError
from agent_core.decision.types import DecisionConfigError, ProviderError, ProviderTimeout
from agent_core.domain import JsonValue, ProviderSpec
from testing.capture import RequestCapture

CONFIG: dict[str, JsonValue] = {"model": "jev-latest", "timeout_ms": 800}
SPEC = ProviderSpec(provider="jev", config=CONFIG)
INPUT: dict[str, JsonValue] = {"text": "sí, adelante", "recent_turns": ["hola"]}
SCHEMA: dict[str, JsonValue] = {
    "type": "object", "additionalProperties": False, "required": ["command"],
    "properties": {
        "command": {"type": "string", "enum": ["affirm", "deny", "start_flow"],
                    "description": "Qué quiere hacer la persona"},
        "flow": {"type": "string", "enum": ["pay", "block"]},
        "urgent": {"type": "boolean"},
        "additional_flows": {"type": "array", "items": {"type": "string", "enum": ["pay", "block"]}},
        "slots": {"type": "object", "additionalProperties": True},
    },
}


def spec(**extra: JsonValue) -> ProviderSpec:
    return ProviderSpec(provider="jev", config={**CONFIG, **extra})


def choice(pick: str, probabilities: dict[str, JsonValue], confidence: JsonValue = 0.5) -> JsonValue:
    return {"type": "choice", "choice": pick, "probabilities": probabilities, "confidence": confidence}


def response(answers: dict[str, JsonValue], model: str = "jev-1.13.0", tokens: tuple[int, int] = (300, 20)
             ) -> dict[str, JsonValue]:
    return {"model": model, "answers": answers,
            "usage": {"input_tokens": tokens[0], "output_tokens": tokens[1]}}


GOOD = response({
    "command": choice("affirm", {"affirm": Decimal("0.93"), "deny": Decimal("0.05"),
                                 "start_flow": Decimal("0.02")}),
    "flow": choice("pay", {"pay": 0.6, "block": 0.4}),
    "urgent": {"type": "noul", "noul": 0.25},
})


class FakeTransport:
    def __init__(self, result: dict[str, JsonValue] | Exception) -> None:
        self.result = result
        self.sent: list[tuple[dict[str, JsonValue], int]] = []

    def send(self, request: dict[str, JsonValue], timeout_ms: int) -> dict[str, JsonValue]:
        self.sent.append((request, timeout_ms))
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def run(result: dict[str, JsonValue] | Exception, provider_spec: ProviderSpec = SPEC,
        schema: dict[str, JsonValue] = SCHEMA, capture: RequestCapture | None = None) -> FakeTransport:
    transport = FakeTransport(result)
    JevProvider(transport, capture).predict(provider_spec, INPUT, schema, "es")
    return transport


def test_request_follows_the_real_contract() -> None:
    transport = FakeTransport(GOOD)
    JevProvider(transport).predict(SPEC, INPUT, SCHEMA, "pt")
    request, timeout_ms = transport.sent[0]
    assert timeout_ms == 800
    assert set(request) == {"state", "model", "questions"}
    assert request["model"] == "jev-latest"
    assert request["state"] == {"locale": "pt", "input": INPUT}
    questions = request["questions"]
    assert isinstance(questions, dict)
    assert set(questions) == {"command", "flow", "urgent"}  # object y array quedan fuera
    assert questions["command"] == {
        "type": "choice", "instructions": "Qué quiere hacer la persona",
        "criteria": {"affirm": None, "deny": None, "start_flow": None}}
    flow = questions["flow"]
    assert isinstance(flow, dict) and flow["type"] == "choice" and "flow" in str(flow["instructions"])
    assert questions["urgent"]["type"] == "noul"  # type: ignore[index]


def test_config_questions_override_instructions_and_criteria() -> None:
    override: dict[str, JsonValue] = {"command": {"instructions": "¿Qué pide?",
                                                  "criteria": {"affirm": "acepta", "deny": "rechaza",
                                                               "start_flow": "quiere iniciar algo"}}}
    transport = run(GOOD, spec(questions=override))
    questions = transport.sent[0][0]["questions"]
    assert isinstance(questions, dict)
    assert questions["command"] == {"type": "choice", "instructions": "¿Qué pide?", "criteria": {
        "affirm": "acepta", "deny": "rechaza", "start_flow": "quiere iniciar algo"}}


def test_choice_maps_to_value_p_raw_and_top_k_ignoring_confidence() -> None:
    raw = JevProvider(FakeTransport(GOOD)).predict(SPEC, INPUT, SCHEMA, "es")
    assert raw.value == {"command": "affirm", "flow": "pay", "urgent": False}
    # p_raw es la probabilidad de la opción elegida; `confidence` (0.5 en GOOD) no interviene.
    assert raw.p_raw == {"command": pytest.approx(0.93), "flow": pytest.approx(0.6),
                         "urgent": pytest.approx(0.75)}
    assert raw.top_k["command"] == [("affirm", pytest.approx(0.93)), ("deny", pytest.approx(0.05)),
                                    ("start_flow", pytest.approx(0.02))]
    assert all(isinstance(p, float) for p in raw.p_raw.values() if p is not None)
    assert raw.latency_ms == 0


def test_top_k_is_sorted_by_p_then_label_and_capped() -> None:
    schema: dict[str, JsonValue] = {"type": "object", "additionalProperties": False, "properties": {
        "flow": {"type": "string", "enum": [f"f{i}" for i in range(8)]}}}
    probabilities: dict[str, JsonValue] = {f"f{i}": 0.1 for i in range(8)}
    probabilities["f3"] = 0.3
    result = response({"flow": choice("f3", probabilities)})
    raw = JevProvider(FakeTransport(result)).predict(SPEC, INPUT, schema, "es")
    assert [label for label, _ in raw.top_k["flow"]] == ["f3", "f0", "f1", "f2", "f4"]


@pytest.mark.parametrize(("noul", "value", "p_raw"), [(0.9, True, 0.9), (0.5, True, 0.5), (0.2, False, 0.8)])
def test_noul_maps_to_boolean_with_the_probability_of_the_chosen_value(
        noul: float, value: bool, p_raw: float) -> None:
    schema: dict[str, JsonValue] = {"type": "object", "additionalProperties": False, "properties": {
        "urgent": {"type": "boolean"}}}
    result = response({"urgent": {"type": "noul", "noul": noul}})
    raw = JevProvider(FakeTransport(result)).predict(SPEC, INPUT, schema, "es")
    assert raw.value == {"urgent": value} and raw.p_raw == {"urgent": pytest.approx(p_raw)}


def test_model_version_is_the_versioned_id_returned_not_the_alias() -> None:
    raw = JevProvider(FakeTransport(response(GOOD["answers"], model="jev-1.13.0"))).predict(  # type: ignore[arg-type]
        SPEC, INPUT, SCHEMA, "es")
    assert raw.model_version == "jev:jev-1.13.0"


def test_tokens_and_cost_come_from_usage() -> None:
    priced = spec(input_usd_per_mtok=Decimal("0.042"))
    raw = JevProvider(FakeTransport(GOOD)).predict(priced, INPUT, SCHEMA, "es")
    assert raw.tokens == 320 and raw.cost_usd == Decimal("300") * Decimal("0.042") / Decimal(1_000_000)
    free = JevProvider(FakeTransport(GOOD)).predict(SPEC, INPUT, SCHEMA, "es")
    assert free.tokens == 320 and free.cost_usd == Decimal("0")


def test_additional_flows_are_off_by_default_and_one_noul_per_flow_when_enabled() -> None:
    off = run(GOOD)
    questions = off.sent[0][0]["questions"]
    assert isinstance(questions, dict) and not any(name.startswith("additional_flows") for name in questions)
    answers = dict(GOOD["answers"])  # type: ignore[call-overload]
    answers |= {"additional_flows__pay": {"type": "noul", "noul": 0.1},
                "additional_flows__block": {"type": "noul", "noul": 0.7}}
    on = FakeTransport(response(answers))
    raw = JevProvider(on).predict(spec(multi_flow=True), INPUT, SCHEMA, "es")
    asked = on.sent[0][0]["questions"]
    assert isinstance(asked, dict) and {"additional_flows__pay", "additional_flows__block"} <= set(asked)
    assert raw.value["additional_flows"] == ["block"]
    assert "additional_flows" not in raw.p_raw or raw.p_raw["additional_flows"] is None


def test_score_from_config_maps_the_argmax_level() -> None:
    schema: dict[str, JsonValue] = {"type": "object", "additionalProperties": False, "properties": {
        "urgency": {"type": "string", "enum": ["low", "mid", "high"]}}}
    levels: list[JsonValue] = [{"value": "low", "description": "no urge"},
                               {"value": "mid", "description": "algo"},
                               {"value": "high", "description": "ya"}]
    questions: dict[str, JsonValue] = {"urgency": {"type": "score", "instructions": "¿Urgencia?",
                                                   "levels": levels}}
    answer: JsonValue = {"type": "score", "score": 1.7, "legend": {"0": "no urge", "1": "algo", "2": "ya"},
                         "probabilities": {"0": 0.1, "1": 0.3, "2": 0.6}, "confidence": 0.7}
    transport = FakeTransport(response({"urgency": answer}))
    raw = JevProvider(transport).predict(spec(questions=questions), INPUT, schema, "es")
    sent = transport.sent[0][0]["questions"]
    assert isinstance(sent, dict) and sent["urgency"] == {
        "type": "score", "instructions": "¿Urgencia?", "criteria": ["no urge", "algo", "ya"]}
    assert raw.value == {"urgency": "high"} and raw.p_raw == {"urgency": pytest.approx(0.6)}
    assert raw.top_k["urgency"][0] == ("high", pytest.approx(0.6))


def test_capture_records_the_request_before_sending() -> None:
    capture = RequestCapture()
    with pytest.raises(ProviderTimeout):
        run(TimeoutError(), capture=capture)
    assert len(capture.requests) == 1 and "sí, adelante" in capture.requests[0]


def test_transport_timeout_becomes_provider_timeout() -> None:
    with pytest.raises(ProviderTimeout):
        run(TimeoutError("lento"))


def test_http_error_becomes_provider_error_with_only_the_status() -> None:
    with pytest.raises(ProviderError) as info:
        run(JevTransportError(429))
    assert "429" in str(info.value) and info.value.__cause__ is None and info.value.__suppress_context__


def test_arbitrary_transport_error_becomes_provider_error_without_leaking_the_request() -> None:
    boom = RuntimeError("fallo con SECRETO-XYZ y Bearer sk-sintetica")
    with pytest.raises(ProviderError) as info:
        run(boom)
    text = str(info.value)
    assert "SECRETO-XYZ" not in text and "sk-sintetica" not in text and "sí, adelante" not in text
    assert info.value.__cause__ is None and info.value.__suppress_context__


def test_config_error_from_the_transport_is_not_swallowed() -> None:
    with pytest.raises(DecisionConfigError):
        run(DecisionConfigError("jev: la API key está vacía"))


def _answers(**overrides: JsonValue) -> dict[str, JsonValue]:
    answers = dict(GOOD["answers"])  # type: ignore[call-overload]
    answers.update(overrides)
    return response(answers)


@pytest.mark.parametrize("result", [
    {},                                                                     # sin answers
    {"model": "jev-1.13.0", "answers": [], "usage": {"input_tokens": 1, "output_tokens": 1}},
    {**GOOD, "model": ""},                                                  # sin modelo
    {**GOOD, "usage": {"input_tokens": -1, "output_tokens": 1}},
    {**GOOD, "usage": {"input_tokens": True, "output_tokens": 1}},
    {k: v for k, v in GOOD.items() if k != "usage"},
    _answers(command={"type": "noul", "noul": 0.5}),                        # tipo distinto del pedido
    _answers(command=choice("otra", {"affirm": 0.5, "otra": 0.5})),         # opción fuera del enum
    _answers(command={"type": "choice", "choice": "affirm"}),               # sin probabilities
    _answers(command=choice("affirm", {"affirm": 1.5, "deny": 0.0})),       # p fuera de [0, 1]
    _answers(command=choice("affirm", {"affirm": True, "deny": 0.0})),
    _answers(command=choice("affirm", {"affirm": "0.9", "deny": 0.0})),
    _answers(command=choice("affirm", {"affirm": 0.5, "zzz": 0.5})),        # clave fuera del enum
    _answers(command=choice("affirm", {"deny": 1.0})),                      # falta la elegida
    _answers(urgent={"type": "noul", "noul": 1.5}),
    _answers(urgent={"type": "noul", "noul": "sí"}),
    _answers(flow=None, urgent=None),                                       # faltan respuestas pedidas
])
def test_malformed_response_is_provider_error(result: dict[str, JsonValue]) -> None:
    with pytest.raises(ProviderError):
        run(result)


def test_provider_error_carries_the_partial_usage_when_it_can() -> None:
    bad = _answers(command=choice("otra", {"otra": 1.0}))
    with pytest.raises(ProviderError) as info:
        run(bad, spec(input_usd_per_mtok=1))
    assert info.value.tokens == 320 and info.value.cost_usd == Decimal("0.0003")


@pytest.mark.parametrize("config", [
    {"model": "m"},                                  # sin timeout_ms
    {"model": "m", "timeout_ms": 0},
    {"model": "m", "timeout_ms": -5},
    {"model": "m", "timeout_ms": True},
    {"model": "m", "timeout_ms": "800"},
    {"timeout_ms": 800},                             # sin model
    {"model": 3, "timeout_ms": 800},
    {"model": "m", "timeout_ms": 800, "multi_flow": "sí"},
    {"model": "m", "timeout_ms": 800, "input_usd_per_mtok": -1},
    {"model": "m", "timeout_ms": 800, "questions": []},
    {"model": "m", "timeout_ms": 800,
     "questions": {"command": {"criteria": {"affirm": "a"}}}},   # no cubre el enum
])
def test_bad_config_is_a_config_error(config: dict[str, JsonValue]) -> None:
    transport = FakeTransport(GOOD)
    with pytest.raises(DecisionConfigError):
        JevProvider(transport).predict(ProviderSpec(provider="jev", config=config), INPUT, SCHEMA, "es")
    assert transport.sent == []


@pytest.mark.parametrize("schema", [
    {"type": "object", "additionalProperties": True},                                           # sin campos
    {"type": "object", "additionalProperties": False, "properties": {"slots": {"type": "object",
                                                                              "additionalProperties": True}}},
    {"type": "object", "additionalProperties": False, "properties": {"c": {"type": "string"}}},  # sin enum
    {"type": "object", "additionalProperties": False, "properties": {"c": {"type": "string", "enum": []}}},
    {"type": "object", "additionalProperties": False,
     "properties": {"c": {"type": "string", "enum": [1, 2]}}},
    {"type": "object", "additionalProperties": False, "properties": {
        "c": {"type": "string", "enum": [f"o{i}" for i in range(256)]}}},   # > 255 opciones
])
def test_schema_that_cannot_be_asked_is_a_config_error(schema: dict[str, JsonValue]) -> None:
    transport = FakeTransport(GOOD)
    with pytest.raises(DecisionConfigError):
        JevProvider(transport).predict(SPEC, INPUT, schema, "es")
    assert transport.sent == []


def test_repr_hides_transport_internals() -> None:
    assert "SECRETO" not in repr(JevProvider(FakeTransport(GOOD)))


def test_name_and_protocol() -> None:
    assert JevProvider(FakeTransport(GOOD)).name == "jev"
    transport: JevTransport = FakeTransport(GOOD)
    assert transport is not None
