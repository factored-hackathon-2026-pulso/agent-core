from decimal import Decimal

import pytest

from agent_core.decision.providers.llm_structured import LlmStructuredProvider
from agent_core.decision.types import DecisionConfigError, ProviderError
from agent_core.domain import EntityRef, GatewayError, GatewayErrorKind, JsonValue, Locale, ProviderSpec
from agent_core.ports import GenerationResult

SPEC = ProviderSpec(provider="llm_structured", config={"prompt": "understand-prompt@1.0.0"})
INPUT: dict[str, JsonValue] = {"text": "cancela eso"}
SCHEMA: dict[str, JsonValue] = {"type": "object", "additionalProperties": True}


class ScriptedGateway:
    def __init__(self, result: GenerationResult | Exception) -> None:
        self.result = result
        self.calls: list[tuple[EntityRef, dict[str, JsonValue], str, dict[str, JsonValue] | None]] = []

    def generate(self, prompt: EntityRef, inputs_model_view: dict[str, JsonValue], locale: Locale,
                 schema: dict[str, JsonValue] | None = None) -> GenerationResult:
        self.calls.append((prompt, inputs_model_view, locale, schema))
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def _result(output: JsonValue = None) -> GenerationResult:
    return GenerationResult(output={"command": "cancel"} if output is None else output, tokens_in=30,
                            tokens_out=12, cost_usd=Decimal("0.0021"), model="llm-sint-1")


def test_maps_the_generation_and_never_reports_probabilities() -> None:
    gateway = ScriptedGateway(_result({"command": "cancel", "flow": "f"}))
    raw = LlmStructuredProvider(gateway).predict(SPEC, INPUT, SCHEMA, "es")
    assert raw.value == {"command": "cancel", "flow": "f"}
    assert raw.p_raw == {"command": None, "flow": None}
    assert raw.tokens == 42 and raw.cost_usd == Decimal("0.0021") and isinstance(raw.cost_usd, Decimal)
    assert raw.model_version == "llm-sint-1"
    prompt, inputs, locale, schema = gateway.calls[0]
    assert prompt == EntityRef.parse("understand-prompt@1.0.0")
    assert inputs == INPUT and locale == "es" and schema == SCHEMA


def test_gateway_error_becomes_provider_error_keeping_partial_usage() -> None:
    error = GatewayError(GatewayErrorKind.timeout, tokens_in=10, tokens_out=None,
                         cost_usd=Decimal("0.001"), model="llm-sint-1")
    with pytest.raises(ProviderError) as info:
        LlmStructuredProvider(ScriptedGateway(error)).predict(SPEC, INPUT, SCHEMA, "es")
    assert info.value.tokens == 10 and info.value.cost_usd == Decimal("0.001")
    assert "cancela" not in str(info.value)


def test_gateway_error_without_usage_defaults_to_zero() -> None:
    with pytest.raises(ProviderError) as info:
        LlmStructuredProvider(ScriptedGateway(GatewayError(GatewayErrorKind.refused))).predict(
            SPEC, INPUT, SCHEMA, "es")
    assert info.value.tokens == 0 and info.value.cost_usd == Decimal("0")


def test_non_object_output_is_provider_error_with_usage() -> None:
    with pytest.raises(ProviderError) as info:
        LlmStructuredProvider(ScriptedGateway(_result("texto libre"))).predict(SPEC, INPUT, SCHEMA, "es")
    assert info.value.tokens == 42


@pytest.mark.parametrize("config", [{}, {"prompt": 3}, {"prompt": "sin-version"}])
def test_bad_prompt_ref_is_a_config_error(config: dict[str, JsonValue]) -> None:
    with pytest.raises(DecisionConfigError):
        LlmStructuredProvider(ScriptedGateway(_result())).predict(
            ProviderSpec(provider="llm_structured", config=config), INPUT, SCHEMA, "es")


def test_name() -> None:
    assert LlmStructuredProvider(ScriptedGateway(_result())).name == "llm_structured"
