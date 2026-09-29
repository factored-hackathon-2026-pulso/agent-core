"""Proveedor `llm_structured` vía `LLMGateway` (spec §3.3): solo baseline en evaluación, sin umbral.

El puerto no expone logprobs, así que `p_raw` es `None` en cada campo de la salida y, por §3.1.4, todo queda
bajo umbral. La entrada ya viene en vista `model`; el gateway nunca recibe `full`."""

from decimal import Decimal

from agent_core.decision.types import DecisionConfigError, ProviderError, RawPrediction
from agent_core.domain import EntityRef, GatewayError, InvalidRuntimeRef, JsonValue, Locale, ProviderSpec
from agent_core.ports import LLMGateway


class LlmStructuredProvider:
    name = "llm_structured"

    def __init__(self, gateway: LLMGateway) -> None:
        self._gateway = gateway

    def __repr__(self) -> str:
        return "LlmStructuredProvider()"

    def predict(self, spec: ProviderSpec, inputs_model_view: dict[str, JsonValue],
                schema: dict[str, JsonValue], locale: Locale) -> RawPrediction:
        prompt = _prompt_ref(spec)
        try:
            result = self._gateway.generate(prompt, inputs_model_view, locale, schema)
        except GatewayError as exc:  # su mensaje solo trae `kind` y `model`
            raise ProviderError(f"llm_structured: {exc}", tokens=(exc.tokens_in or 0) + (exc.tokens_out or 0),
                                cost_usd=exc.cost_usd if exc.cost_usd is not None else Decimal("0")) from None
        tokens = result.tokens_in + result.tokens_out
        if not isinstance(result.output, dict):
            raise ProviderError("llm_structured: la salida no es un objeto", tokens=tokens,
                                cost_usd=result.cost_usd)
        return RawPrediction(value=result.output, p_raw=dict.fromkeys(result.output), tokens=tokens,
                             cost_usd=result.cost_usd, model_version=result.model)


def _prompt_ref(spec: ProviderSpec) -> EntityRef:
    raw = spec.config.get("prompt")
    if not isinstance(raw, str):
        raise DecisionConfigError("llm_structured: config.prompt es obligatorio (id@versión)")
    try:
        return EntityRef.parse(raw)
    except InvalidRuntimeRef:
        raise DecisionConfigError("llm_structured: config.prompt no es un id@versión válido") from None

