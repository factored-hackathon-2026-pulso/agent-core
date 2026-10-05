import pytest

from agent_core.decision.providers.rule import RuleProvider
from agent_core.decision.service import DecisionService
from agent_core.decision.types import DecisionConfigError, ProviderError
from agent_core.domain import JsonValue, ProviderSpec
from testing.fakes.provider import Timeout
from tests.m05.helpers import make_service, model_def, ref

CONFIG: dict[str, JsonValue] = {
    "cases": [
        {"when": {"path": "confirm_pending", "equals": True}, "value": {"command": "affirm"}},
        {"when": {"path": "current_node", "equals": "pedir_monto"}, "value": {"command": "deny"}},
    ],
    "default": {"command": "cancel"},
}


def _predict(inputs: dict[str, JsonValue], config: dict[str, JsonValue] = CONFIG):  # type: ignore[no-untyped-def]
    return RuleProvider().predict(ProviderSpec(provider="rule", config=config), inputs, {}, "es")


def test_matching_case_gives_p_one() -> None:
    raw = _predict({"confirm_pending": True})
    assert raw.value == {"command": "affirm"} and raw.p_raw == {"command": 1.0}
    assert raw.model_version == "rule-1"


def test_first_matching_case_wins() -> None:
    raw = _predict({"confirm_pending": True, "current_node": "pedir_monto"})
    assert raw.value == {"command": "affirm"}


def test_no_case_uses_default_with_p_zero() -> None:
    raw = _predict({"confirm_pending": False})
    assert raw.value == {"command": "cancel"} and raw.p_raw == {"command": 0.0}


def test_no_case_and_no_default_is_provider_error() -> None:
    with pytest.raises(ProviderError):
        _predict({"x": 1}, {"cases": CONFIG["cases"]})


def test_equality_is_strict_between_bool_and_int() -> None:
    case: JsonValue = {"when": {"path": "n", "equals": 1}, "value": {"command": "affirm"}}
    config: dict[str, JsonValue] = {"cases": [case], "default": {"command": "deny"}}
    assert _predict({"n": True}, config).value == {"command": "deny"}
    assert _predict({"n": 1}, config).value == {"command": "affirm"}


@pytest.mark.parametrize("config", [
    {},
    {"cases": "x"},
    {"cases": [{"when": {"path": "a"}, "value": {}}]},
    {"cases": [{"when": {"path": "a", "equals": 1}, "value": 3}]},
    {"cases": [], "default": 3},
])
def test_malformed_config_is_a_config_error(config: dict[str, JsonValue]) -> None:
    with pytest.raises(DecisionConfigError):
        _predict({"a": 1}, config)


def test_rule_inside_the_service_after_a_timeout() -> None:
    definition = model_def(providers=("jev", "rule"), calibration_method="none", calibration_run=None,
                           thresholds_from=None)
    definition = definition.model_copy(update={"providers": [
        definition.providers[0], ProviderSpec(provider="rule", config=CONFIG)]})
    rig = make_service(definition)
    rig.providers["jev"].push(Timeout())
    service = DecisionService(rig.registry, {"jev": rig.providers["jev"], "rule": RuleProvider()},
                              rig.sources, rig.clock, rig.ids)
    out = service.decide_output(ref(definition), {"confirm_pending": True}, "es", rig.vault)
    assert out.provider_used == "rule" and out.fallback_depth == 1 and out.p_raw == {"command": 1.0}


# --- conteo de una lista y valores derivados de la entrada (`match-cargo` con un proveedor real) --

CANDIDATES = "facts.candidatas.value"
MATCH: dict[str, JsonValue] = {
    "cases": [
        {"when": {"path": CANDIDATES, "count": 1}, "value": {"match": "unica"},
         "value_from": {"transaction": {"path": CANDIDATES, "at": [0, "transaction_id"]}}},
        {"when": {"path": CANDIDATES, "count_min": 2}, "value": {"match": "varias"}},
    ],
    "default": {"match": "ninguna"},
}
ROW = {"transaction_id": "TRX-1", "amount": "10.00"}


def test_a_single_candidate_is_the_unique_match_and_carries_its_id() -> None:
    raw = _predict({CANDIDATES: [ROW]}, MATCH)
    assert raw.value == {"match": "unica", "transaction": "TRX-1"}
    assert raw.p_raw["match"] == 1.0


def test_several_candidates_are_several() -> None:
    assert _predict({CANDIDATES: [ROW, ROW]}, MATCH).value == {"match": "varias"}


def test_no_candidates_falls_to_the_default() -> None:
    raw = _predict({CANDIDATES: []}, MATCH)
    assert raw.value == {"match": "ninguna"} and raw.p_raw["match"] == 0.0


def test_a_missing_or_non_list_input_never_matches_a_count() -> None:
    assert _predict({}, MATCH).value == {"match": "ninguna"}
    assert _predict({CANDIDATES: "texto"}, MATCH).value == {"match": "ninguna"}


def test_value_from_that_does_not_resolve_is_a_provider_error_not_a_made_up_value() -> None:
    config: dict[str, JsonValue] = {"cases": [
        {"when": {"path": CANDIDATES, "count": 1}, "value": {"match": "unica"},
         "value_from": {"transaction": {"path": CANDIDATES, "at": [0, "no_existe"]}}}]}
    with pytest.raises(ProviderError):
        _predict({CANDIDATES: [ROW]}, config)


@pytest.mark.parametrize("when", [
    {"path": CANDIDATES, "count": -1},
    {"path": CANDIDATES, "count": True},
    {"path": CANDIDATES, "count": 1, "equals": 1},
    {"path": CANDIDATES},
    {"path": CANDIDATES, "count_min": "dos"},
])
def test_malformed_count_conditions_are_a_config_error(when: dict[str, JsonValue]) -> None:
    with pytest.raises(DecisionConfigError):
        _predict({CANDIDATES: [ROW]}, {"cases": [{"when": when, "value": {"match": "unica"}}]})


@pytest.mark.parametrize("value_from", [
    {"transaction": "TRX"}, {"transaction": {"path": CANDIDATES}},
    {"transaction": {"path": CANDIDATES, "at": [1.5]}}, {"transaction": {"path": CANDIDATES, "at": [True]}}])
def test_malformed_value_from_is_a_config_error(value_from: dict[str, JsonValue]) -> None:
    case: dict[str, JsonValue] = {"when": {"path": CANDIDATES, "count": 1}, "value": {"match": "unica"},
                                  "value_from": value_from}
    with pytest.raises(DecisionConfigError):
        _predict({CANDIDATES: [ROW]}, {"cases": [case]})


# --- `narrow`: quedarse con los elementos que el texto del cliente menciona --------------------------------

TEXT = "slots.descripcion_cargo"
ROWS = [
    {"transaction_id": "TRX-1", "merchant_name": "Boutique Moda", "amount": "108.07"},
    {"transaction_id": "TRX-2", "merchant_name": "Estación de Servicio", "amount": "141.28"},
    {"transaction_id": "TRX-3", "merchant_name": "Mercado Central", "amount": "428.30"},
]
NARROWED: dict[str, JsonValue] = {
    "cases": [
        {"when": {"path": CANDIDATES, "count": 1,
                  "narrow": {"text_from": TEXT, "fields": ["merchant_name", "amount"]}},
         "value": {"match": "unica"},
         "value_from": {"transaction": {"path": CANDIDATES, "at": [0, "transaction_id"]}}},
        {"when": {"path": CANDIDATES, "count_min": 2,
                  "narrow": {"text_from": TEXT, "fields": ["merchant_name", "amount"]}},
         "value": {"match": "varias"}},
    ],
    "default": {"match": "ninguna"},
}


def _narrow(text: str):  # type: ignore[no-untyped-def]
    return _predict({CANDIDATES: ROWS, TEXT: text}, NARROWED)


@pytest.mark.parametrize("text", [
    "no reconozco el cargo de Boutique Moda", "BOUTIQUE MODA", "fue de 108.07 dólares", "fueron 108,07",
    '<datos_no_confiables fuente="slot">el de boutique moda</datos_no_confiables>'])
def test_the_text_mentioning_one_candidate_picks_it(text: str) -> None:
    raw = _narrow(text)
    assert raw.value == {"match": "unica", "transaction": "TRX-1"}


def test_accents_and_case_do_not_matter() -> None:
    assert _narrow("el de ESTACION DE SERVICIO").value == {"match": "unica", "transaction": "TRX-2"}


def test_two_mentioned_candidates_are_several() -> None:
    assert _narrow("Boutique Moda y Mercado Central").value == {"match": "varias"}


def test_a_text_that_mentions_none_is_no_match() -> None:
    assert _narrow("no sé de qué comercio es").value == {"match": "ninguna"}


def test_a_missing_text_is_no_match() -> None:
    assert _predict({CANDIDATES: ROWS}, NARROWED).value == {"match": "ninguna"}


def test_an_amount_must_be_equal_not_a_prefix() -> None:
    assert _narrow("fueron 108.0 dólares").value == {"match": "ninguna"}


@pytest.mark.parametrize("narrow", [
    {"text_from": TEXT}, {"text_from": TEXT, "fields": []}, {"text_from": "", "fields": ["merchant_name"]},
    {"fields": ["merchant_name"]}, {"text_from": TEXT, "fields": [1]}])
def test_malformed_narrow_is_a_config_error(narrow: dict[str, JsonValue]) -> None:
    case: dict[str, JsonValue] = {"when": {"path": CANDIDATES, "count": 1, "narrow": narrow},
                                  "value": {"match": "unica"}}
    with pytest.raises(DecisionConfigError):
        _predict({CANDIDATES: ROWS, TEXT: "x"}, {"cases": [case]})


TAXIS = [
    {"transaction_id": "TRX-A", "merchant_name": "Taxi Seguro", "amount": "322.86"},
    {"transaction_id": "TRX-B", "merchant_name": "Taxi Seguro", "amount": "50.00"},
    {"transaction_id": "TRX-C", "merchant_name": "Taxi Seguro", "amount": "12.10"},
    {"transaction_id": "TRX-D", "merchant_name": "Super Ahorro", "amount": "79.36"},
]


def _taxis(text: str):  # type: ignore[no-untyped-def]
    return _predict({CANDIDATES: TAXIS, TEXT: text}, NARROWED)


def test_a_mentioned_amount_refines_a_merchant_that_matches_several() -> None:
    assert _taxis("el de Taxi Seguro por 322.86").value == {"match": "unica", "transaction": "TRX-A"}


def test_a_merchant_alone_that_matches_several_stays_several() -> None:
    assert _taxis("el de Taxi Seguro").value == {"match": "varias"}


def test_an_amount_that_is_nobody_s_does_not_veto_the_merchant() -> None:
    assert _taxis("Super Ahorro de 99.00").value == {"match": "unica", "transaction": "TRX-D"}


def test_fields_that_disagree_fall_back_to_the_union() -> None:
    assert _taxis("Taxi Seguro y los 79.36").value == {"match": "varias"}
