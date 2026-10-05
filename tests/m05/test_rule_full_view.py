"""ADR 0027: `rule` con `compare_on: full` COMPARA en la vista `full` y devuelve solo valores de la `model`.

Cubre el defecto real: el detector de PII tokeniza las cifras de 6 o más dígitos que escribe el cliente (un
monto en pesos), y `match-cargo` no podía identificar el cargo por su monto."""

import pytest

from agent_core.decision.providers.rule import RuleProvider
from agent_core.decision.types import DecisionConfigError, RawPrediction
from agent_core.domain import DecisionModelDef, EntityRef, JsonValue, ProviderSpec
from agent_core.views.untrusted import tokenize_free_text
from testing.fakes.ids import FakeIds
from tests.m05 import helpers
from tests.m05.helpers import make_service

# El `match-cargo@2.0.0` de la semilla `registry-e2e`: comercio y monto contra la lista de candidatas.
_CASES: list[JsonValue] = [
    {"when": {"path": "facts.candidatas.value", "count": 1,
              "narrow": {"text_from": "slots.descripcion_cargo", "fields": ["merchant_name", "amount"]}},
     "value": {"match": "unica"},
     "value_from": {"transaction": {"path": "facts.candidatas.value", "at": [0, "transaction_id"]}}},
    {"when": {"path": "facts.candidatas.value", "count_min": 2,
              "narrow": {"text_from": "slots.descripcion_cargo", "fields": ["merchant_name", "amount"]}},
     "value": {"match": "varias"}},
]
_FULL_CONFIG: dict[str, JsonValue] = {"compare_on": "full", "cases": _CASES, "default": {"match": "ninguna"}}
_MODEL_CONFIG: dict[str, JsonValue] = {"cases": _CASES, "default": {"match": "ninguna"}}

ROWS: list[JsonValue] = [
    {"transaction_id": "TRX-1", "merchant_name": "Ferretería", "amount": "344456.72", "currency": "COP"},
    {"transaction_id": "TRX-2", "merchant_name": "Ferretería", "amount": "1038345.96", "currency": "COP"},
    {"transaction_id": "TRX-3", "merchant_name": "Super Ahorro", "amount": "717687.24", "currency": "COP"},
]


def _spec(config: dict[str, JsonValue]) -> ProviderSpec:
    return ProviderSpec(provider="rule", config=config)


def _inputs(said: str, rows: list[JsonValue] = ROWS) -> tuple[dict[str, JsonValue], dict[str, JsonValue]]:
    """(vista `model`, vista `full`) de lo que dijo el cliente, como las arma el motor: el texto de la vista
    `model` pasa por el detector de PII; el de la vista `full` es el original."""
    vault = helpers.vault(FakeIds())
    model: dict[str, JsonValue] = {"slots.descripcion_cargo": tokenize_free_text(said, vault),
                                   "facts.candidatas.value": rows}
    full: dict[str, JsonValue] = {"slots.descripcion_cargo": said, "facts.candidatas.value": rows}
    return model, full


def _compare_in_full(said: str, rows: list[JsonValue] = ROWS) -> RawPrediction:
    model, full = _inputs(said, rows)
    return RuleProvider().predict_full(_spec(_FULL_CONFIG), model, full, {}, "es")


# --- el defecto real ---------------------------------------------------------------------------------------

SAID = "El cargo fue en Ferretería por 344456.72 pesos"


def test_the_amount_the_customer_typed_is_tokenized_in_the_model_view() -> None:
    model, _ = _inputs(SAID)
    assert "344456" not in str(model["slots.descripcion_cargo"])  # el detector la cambió por un token


def test_comparing_in_the_model_view_cannot_tell_the_two_ferreterias_apart() -> None:
    model, _ = _inputs(SAID)
    raw = RuleProvider().predict(_spec(_MODEL_CONFIG), model, {}, "es")
    assert raw.value == {"match": "varias"}  # el defecto: el monto no se ve, el comercio se repite


def test_comparing_in_the_full_view_identifies_the_charge_by_its_amount() -> None:
    raw = _compare_in_full(SAID)
    assert raw.value == {"match": "unica", "transaction": "TRX-1"}


@pytest.mark.parametrize("said", [
    "fue en Ferretería de 344456.72",            # punto decimal
    "fue en Ferretería de 344.456,72 pesos",     # miles con punto y decimal con coma
    "fue en Ferretería de 344,456.72 pesos",     # miles con coma y decimal con punto
    "fue en Ferretería de $344456.72",
])
def test_the_amount_is_read_in_the_common_ways_of_writing_it(said: str) -> None:
    assert _compare_in_full(said).value == {"match": "unica", "transaction": "TRX-1"}


def test_an_amount_without_decimals_and_with_thousands_separators() -> None:
    rows: list[JsonValue] = [
        {"transaction_id": "TRX-9", "merchant_name": "Taxi Seguro", "amount": "1183379.00"},
        {"transaction_id": "TRX-8", "merchant_name": "Cable TV", "amount": "1135591.84"}]
    expected = {"match": "unica", "transaction": "TRX-9"}
    assert _compare_in_full("cobro de 1.183.379 pesos", rows).value == expected
    assert _compare_in_full("cobro de 1183379", rows).value == expected


def test_a_merchant_mentioned_with_an_amount_nobody_has_still_points_at_the_merchant() -> None:
    raw = _compare_in_full("fue en Super Ahorro por 5 pesos")
    assert raw.value == {"match": "unica", "transaction": "TRX-3"}


def test_nothing_mentioned_keeps_nothing() -> None:
    assert _compare_in_full("me cobraron algo raro").value == {"match": "ninguna"}


# --- la salida es de la vista `model`, nunca de la `full` ---------------------------------------------------

def test_what_the_rule_returns_comes_from_the_model_view_not_from_the_full_view() -> None:
    model, full = _inputs(SAID)
    tokenized: list[JsonValue] = [{**row, "transaction_id": f"⟦ref:{i}⟧"}  # type: ignore[dict-item]
                                  for i, row in enumerate(ROWS)]
    model["facts.candidatas.value"] = tokenized
    raw = RuleProvider().predict_full(_spec(_FULL_CONFIG), model, full, {}, "es")
    assert raw.value == {"match": "unica", "transaction": "⟦ref:0⟧"}  # no "TRX-1" (la vista `full`)


def test_lists_of_different_length_fail_closed() -> None:
    model, full = _inputs(SAID)
    model["facts.candidatas.value"] = ROWS[:2]  # la vista `model` perdió una fila
    raw = RuleProvider().predict_full(_spec(_FULL_CONFIG), model, full, {}, "es")
    assert raw.value == {"match": "ninguna"}


def test_an_invalid_compare_on_is_a_config_error() -> None:
    model, full = _inputs(SAID)
    with pytest.raises(DecisionConfigError):
        RuleProvider().predict_full(_spec({**_FULL_CONFIG, "compare_on": "todo"}), model, full, {}, "es")


def test_without_the_full_view_a_compare_on_full_rule_falls_back_to_the_model_view() -> None:
    model, _ = _inputs(SAID)
    assert RuleProvider().predict(_spec(_FULL_CONFIG), model, {}, "es").value == {"match": "varias"}


# --- el servicio entrega la vista `full` solo a `rule` con el opt-in ----------------------------------------

class _Spy:
    """Proveedor que anota qué vistas recibió; implementa `predict_full` aunque no debería usarlo."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.seen_full: list[object] = []
        self.predict_calls = 0

    def predict(self, spec: ProviderSpec, inputs_model_view: dict[str, JsonValue],
                schema: dict[str, JsonValue], locale: str) -> RawPrediction:
        self.predict_calls += 1
        return RawPrediction(value={"match": "ninguna"}, p_raw={"match": 1.0})

    def predict_full(self, spec: ProviderSpec, inputs_model_view: dict[str, JsonValue],
                     inputs_full: dict[str, JsonValue], schema: dict[str, JsonValue],
                     locale: str) -> RawPrediction:
        self.seen_full.append(inputs_full)
        return RawPrediction(value={"match": "ninguna"}, p_raw={"match": 1.0})


def _decide_with(provider: str, config: dict[str, JsonValue]) -> _Spy:
    definition = DecisionModelDef.model_validate({
        "id": "match", "version": "1.0.0", "output_schema": {
            "type": "object", "additionalProperties": False,
            "properties": {"match": {"type": "string"}}},
        "calibrated_fields": ["match"], "input_view": ["slots.descripcion_cargo"],
        "providers": [{"provider": provider, "config": config}],
        "calibration": {"method": "none"}})
    rig = make_service(definition)
    spy = _Spy(provider)
    rig.service._providers[provider] = spy  # el espía reemplaza al proveedor guionado
    rig.service.decide_output(EntityRef(id="match", version="1.0.0"), {"slots.descripcion_cargo": "x"}, "es",
                              rig.vault, inputs_full={"slots.descripcion_cargo": "dato en claro"})
    return spy


def test_the_service_hands_the_full_view_to_a_rule_that_asks_for_it() -> None:
    spy = _decide_with("rule", {"compare_on": "full"})
    assert spy.seen_full == [{"slots.descripcion_cargo": "dato en claro"}] and spy.predict_calls == 0


def test_the_service_does_not_hand_the_full_view_to_a_rule_that_does_not_ask() -> None:
    spy = _decide_with("rule", {})
    assert spy.seen_full == [] and spy.predict_calls >= 1


@pytest.mark.parametrize("provider", ["jev", "classifier", "llm_structured"])
def test_no_other_provider_gets_the_full_view_even_if_its_config_asks(provider: str) -> None:
    spy = _decide_with(provider, {"compare_on": "full"})
    assert spy.seen_full == [] and spy.predict_calls >= 1


def test_the_full_view_keys_are_limited_to_the_input_view() -> None:
    definition = helpers.model_def(providers=("classifier",), input_view=("slots.descripcion_cargo",))
    rig = make_service(definition)
    with pytest.raises(DecisionConfigError):
        rig.service.decide_output(helpers.ref(definition), {"slots.descripcion_cargo": "x"}, "es", rig.vault,
                                  inputs_full={"slots.otra": "y"})
