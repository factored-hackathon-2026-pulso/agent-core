"""Helpers for the HTTP LLM gateway client tests. Synthetic data only; no network (respx over httpx)."""

from datetime import date
from decimal import Decimal
from typing import Any

from agent_core.adapters.llm.http_gateway import HttpLLMGateway
from agent_core.domain import (
    EntityRef,
    JsonValue,
    ModelPrice,
    ModelProfile,
    Prompt,
    StructuredMode,
    dumps,
)
from testing.fakes.registry import InMemoryRegistry

BASE_URL = "https://llm-gateway.test"
GENERATE = f"{BASE_URL}/v1/generate"
TOKEN = "gw-token-SECRETO-0001"
PROMPT = EntityRef.parse("resumen@1.0.0")
PROFILE = EntityRef.parse("perfil@1.0.0")
PROMPT_ES = "Resume la disputa."
INPUTS: dict[str, JsonValue] = {"cliente": {"nombre": "⟦name:1⟧"}, "asunto": "CONTENIDO-SENSIBLE"}
DRAFT: dict[str, JsonValue] = {
    "type": "object", "additionalProperties": False, "required": ["text", "citations"],
    "properties": {"text": {"type": "string"}, "citations": {"type": "array", "items": {"type": "string"}}}}


def success(output: Any = "hola", *, tokens: tuple[int, int] = (120, 30), cost: str = "0.000810",
            model: str = "vendor/modelo-x-2026", usage_known: bool = True) -> str:
    """A 200 body as the gateway writes it: text, so decimals in `output` keep their scale."""
    flag = "true" if usage_known else "false"
    return ('{"output":' + dumps(output) + ',"model":' + dumps(model) + f',"tokens_in":{tokens[0]},'
            f'"tokens_out":{tokens[1]},"cost_usd":"{cost}","usage_known":{flag}}}')


def failure(kind: str, *, model: str | None = "vendor/modelo-x", tokens: tuple[int, int] | None = None,
            cost: str | None = None) -> dict[str, Any]:
    """An error body as the gateway writes it."""
    error: dict[str, Any] = {"kind": kind, "message": f"gateway error: {kind}"}
    if model:
        error["model"] = model
    if tokens is not None:
        error["tokens_in"], error["tokens_out"] = tokens
    if cost is not None:
        error["cost_usd"] = cost
    return {"error": error}


def install_llm_entities(registry: InMemoryRegistry, *, structured: StructuredMode = StructuredMode.prompted,
                         timeout_s: int = 8) -> None:
    registry.add(
        ModelProfile(
            id="perfil", version="1.0.0", endpoint_alias="openrouter", model="vendor/modelo-x",
            temperature=Decimal("0.2"), max_tokens=300, timeout_s=timeout_s, structured=structured,
            price=ModelPrice(input_per_mtok=Decimal("3.00"), output_per_mtok=Decimal("15.00"),
                             source="prueba", as_of=date(2026, 9, 30))),
        Prompt.model_validate({"id": "resumen", "version": "1.0.0",
                               "locales": {"es": PROMPT_ES, "pt": "Resuma a disputa."},
                               "model_profile": "perfil@1.0.0"}))


def make_gateway(*, structured: StructuredMode = StructuredMode.prompted, timeout_s: int = 8,
                 **kwargs: Any) -> HttpLLMGateway:
    registry = InMemoryRegistry()
    install_llm_entities(registry, structured=structured, timeout_s=timeout_s)
    return HttpLLMGateway(registry, kwargs.pop("base_url", BASE_URL), kwargs.pop("token", TOKEN), **kwargs)
