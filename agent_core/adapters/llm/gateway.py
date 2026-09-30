"""`OpenAICompatGateway`: `LLMGateway` sobre el SDK `openai` (spec del gateway §3)."""

import logging
from collections.abc import Callable, Mapping
from decimal import Decimal
from typing import Any

from openai import OpenAI

from agent_core.adapters.llm.config import EndpointConfig, default_client
from agent_core.adapters.llm.cost import price_of
from agent_core.adapters.llm.output import parse_output
from agent_core.domain import (
    EntityRef,
    JsonValue,
    Locale,
    ModelProfile,
    Prompt,
    SchemaError,
    StructuredMode,
    canonical_bytes,
)
from agent_core.ports import GenerationResult, RegistryPort

_LOG = logging.getLogger("agent_core.adapters.llm")
SCHEMA_INSTRUCTION = (
    "\n\nResponde únicamente con un objeto JSON que cumpla este JSON Schema, sin texto adicional ni "
    "bloques de código:\n")

ClientFactory = Callable[[EndpointConfig, str, int], OpenAI]


class OpenAICompatGateway:
    """Gateway de generación sobre cualquier endpoint compatible con la API de OpenAI."""

    def __init__(self, registry: RegistryPort, endpoints: dict[str, EndpointConfig], env: Mapping[str, str],
                 client_factory: ClientFactory = default_client) -> None:
        self._registry = registry
        self._endpoints = endpoints
        self._env = env
        self._client_factory = client_factory

    def generate(self, prompt: EntityRef, inputs_model_view: dict[str, JsonValue], locale: Locale,
                 schema: dict[str, JsonValue] | None = None) -> GenerationResult:
        """Genera una respuesta del modelo del perfil del prompt (camino feliz; errores en la Task 4)."""
        prompt_def = self._registry.get(prompt, Prompt)
        profile = self._registry.get(prompt_def.model_profile.require_exact(), ModelProfile)
        text = prompt_def.locales.get(locale)
        if text is None:
            raise SchemaError(f"el prompt {prompt} no tiene el locale {locale}")
        endpoint = self._endpoints[profile.endpoint_alias]  # los errores de alias llegan en la Task 4
        client = self._client_factory(endpoint, self._env["OPENROUTER_API_KEY"], profile.timeout_s)
        kwargs = _request(profile, text, inputs_model_view, schema)
        response = client.chat.completions.create(**kwargs)
        return _result(response, profile, schema)


def _request(profile: ModelProfile, text: str, inputs: dict[str, JsonValue],
             schema: dict[str, JsonValue] | None) -> dict[str, Any]:
    system = text
    if schema is not None and profile.structured is StructuredMode.prompted:
        system += SCHEMA_INSTRUCTION + canonical_bytes(schema).decode()
    kwargs: dict[str, Any] = {
        "model": profile.model,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": canonical_bytes(inputs).decode()}],
        "temperature": float(profile.temperature),  # el SDK exige float; el costo nunca usa float
        "max_tokens": profile.max_tokens,
    }
    if schema is not None and profile.structured is StructuredMode.native:
        kwargs["response_format"] = {
            "type": "json_schema", "json_schema": {"name": "output", "schema": schema, "strict": True}}
    return kwargs


def _result(response: Any, profile: ModelProfile, schema: dict[str, JsonValue] | None) -> GenerationResult:
    choice = response.choices[0]
    content = choice.message.content or ""
    output: JsonValue = parse_output(content, schema) if schema is not None else content
    usage = response.usage
    if usage is None:
        _LOG.warning("respuesta sin usage model=%s", response.model)
        tokens_in = tokens_out = 0
        cost = Decimal("0")
    else:
        tokens_in, tokens_out = usage.prompt_tokens, usage.completion_tokens
        cost = price_of(profile.price, tokens_in, tokens_out)
    return GenerationResult(output=output, tokens_in=tokens_in, tokens_out=tokens_out, cost_usd=cost,
                            model=response.model or profile.model)
