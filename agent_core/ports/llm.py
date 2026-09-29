from decimal import Decimal
from typing import Protocol

from pydantic import Field

from agent_core.domain.base import Locale, Model
from agent_core.domain.json import JsonValue
from agent_core.domain.refs import EntityRef


class GenerationResult(Model):
    """Resultado de una generación del LLM: salida, tokens, costo y modelo (M0 §2.9)."""
    output: JsonValue
    tokens_in: int = Field(ge=0)
    tokens_out: int = Field(ge=0)
    cost_usd: Decimal = Field(ge=0)
    model: str


class LLMGateway(Protocol):
    """Puerto del gateway de LLM; solo recibe la vista `model` (M0 §2.9)."""
    def generate(self, prompt: EntityRef, inputs_model_view: dict[str, JsonValue], locale: Locale,
                 schema: dict[str, JsonValue] | None = None) -> GenerationResult:
        """Solo recibe la vista `model` (nunca `full`). Falla con `GatewayError` (M0 §2.11)."""
        ...
