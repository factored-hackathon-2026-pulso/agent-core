"""Ayudantes de las pruebas del gateway. Solo datos sintéticos; sin red (respx sobre httpx)."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from agent_core.adapters.llm.config import EndpointConfig
from agent_core.adapters.llm.gateway import OpenAICompatGateway
from agent_core.domain import EntityRef, JsonValue, ModelPrice, ModelProfile, Prompt, StructuredMode
from testing.fakes.registry import InMemoryRegistry

BASE_URL = "https://openrouter.test/api/v1"
CHAT = f"{BASE_URL}/chat/completions"
KEY = "sk-test-SECRETO-0001"
PROMPT = EntityRef.parse("resumen@1.0.0")
PROFILE = EntityRef.parse("perfil@1.0.0")
PROMPT_ES = "Resume la disputa."
INPUTS: dict[str, JsonValue] = {"cliente": {"nombre": "⟦name:1⟧"}, "asunto": "CONTENIDO-SENSIBLE"}
DRAFT: dict[str, JsonValue] = {
    "type": "object", "additionalProperties": False, "required": ["text", "citations"],
    "properties": {"text": {"type": "string"}, "citations": {"type": "array", "items": {"type": "string"}}}}
GOOD_JSON = '{"text": "hola", "citations": []}'
ENDPOINTS = {"openrouter": EndpointConfig("openrouter", BASE_URL, "OPENROUTER_API_KEY")}
ENV = {"OPENROUTER_API_KEY": KEY}


def completion(content: str | None, *, finish: str = "stop", usage: tuple[int, int] | None = (120, 30),
               model: str = "vendor/modelo-x-2026", refusal: str | None = None) -> dict[str, Any]:
    """Cuerpo de una respuesta de chat.completions."""
    body: dict[str, Any] = {
        "id": "c1", "object": "chat.completion", "created": 1, "model": model,
        "choices": [{"index": 0, "finish_reason": finish,
                     "message": {"role": "assistant", "content": content, "refusal": refusal}}]}
    if usage is not None:
        body["usage"] = {"prompt_tokens": usage[0], "completion_tokens": usage[1],
                         "total_tokens": usage[0] + usage[1]}
    return body


def install_llm_entities(registry: InMemoryRegistry, *, structured: StructuredMode = StructuredMode.prompted,
                         timeout_s: int = 8) -> None:
    """Registra el perfil de modelo y el prompt `resumen` (es/pt) usados por las pruebas."""
    registry.add(
        ModelProfile(
            id="perfil", version="1.0.0", endpoint_alias="openrouter", model="vendor/modelo-x",
            temperature=Decimal("0.2"), max_tokens=300, timeout_s=timeout_s, structured=structured,
            price=ModelPrice(input_per_mtok=Decimal("3.00"), output_per_mtok=Decimal("15.00"),
                             source="prueba", as_of=date(2026, 9, 30))),
        Prompt.model_validate({"id": "resumen", "version": "1.0.0",
                               "locales": {"es": PROMPT_ES, "pt": "Resuma a disputa."},
                               "model_profile": "perfil@1.0.0"}))


def build_gateway(registry: InMemoryRegistry, **kwargs: Any) -> OpenAICompatGateway:
    """Construye el gateway con endpoints y entorno de prueba; `kwargs` los sobrescribe."""
    return OpenAICompatGateway(registry, kwargs.pop("endpoints", ENDPOINTS), kwargs.pop("env", ENV), **kwargs)


@dataclass
class World:
    registry: InMemoryRegistry
    gateway: OpenAICompatGateway


def make_world(*, structured: StructuredMode = StructuredMode.prompted, timeout_s: int = 8,
               **gateway_kwargs: Any) -> World:
    """Registro con las entidades de prueba más un gateway listo para usar."""
    registry = InMemoryRegistry()
    install_llm_entities(registry, structured=structured, timeout_s=timeout_s)
    return World(registry, build_gateway(registry, **gateway_kwargs))
