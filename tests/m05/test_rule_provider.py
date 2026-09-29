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
