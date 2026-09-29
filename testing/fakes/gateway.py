"""Doble de `LLMGateway` (M8): resultados guionados en orden. No valida ni genera texto."""

from collections import deque
from collections.abc import Sequence
from copy import deepcopy
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING

from agent_core.domain import EntityRef, GatewayError, JsonValue, Locale
from agent_core.ports import GenerationResult
from testing.capture import RequestCapture

ScriptItem = GenerationResult | GatewayError


@dataclass(frozen=True, slots=True)
class GatewayCall:
    prompt: EntityRef
    inputs: dict[str, JsonValue]
    locale: Locale
    schema: dict[str, JsonValue] | None


def gen(text: str, citations: list[str], *, tokens_in: int = 10, tokens_out: int = 5, cost: str = "0.001",
        model: str = "scripted-1") -> GenerationResult:
    """Borrador sintético con la forma `{text, citations}` que M8 espera del gateway."""
    output: JsonValue = {"text": text, "citations": list(citations)}
    return GenerationResult(output=output, tokens_in=tokens_in, tokens_out=tokens_out,
                            cost_usd=Decimal(cost), model=model)


class ScriptedGateway:
    def __init__(self, script: Sequence[ScriptItem] = (), *, capture: RequestCapture | None = None) -> None:
        self._script: deque[ScriptItem] = deque(script)
        self._capture = capture
        self.calls: list[GatewayCall] = []

    def push(self, *items: ScriptItem) -> None:
        self._script.extend(items)

    def generate(self, prompt: EntityRef, inputs_model_view: dict[str, JsonValue], locale: Locale,
                 schema: dict[str, JsonValue] | None = None) -> GenerationResult:
        inputs = deepcopy(inputs_model_view)
        self.calls.append(GatewayCall(prompt, inputs, locale, deepcopy(schema)))
        if self._capture is not None:
            self._capture.record(
                {"gateway": "llm", "prompt": str(prompt), "locale": locale, "inputs": inputs})
        if not self._script:
            raise AssertionError("ScriptedGateway sin resultado guionado")
        item = self._script.popleft()
        if isinstance(item, GatewayError):
            raise item
        return item


if TYPE_CHECKING:
    from agent_core.ports import LLMGateway

    def _conforms(x: ScriptedGateway) -> LLMGateway:
        return x
