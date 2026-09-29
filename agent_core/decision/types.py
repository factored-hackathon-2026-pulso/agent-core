"""Tipos de M5 (spec §2): salida cruda de un proveedor, salida de `decide` y errores.

`DecisionOutput.as_decision()` entrega el `Decision` de M0; el adaptador hacia `DecisionPort` de M2 no vive
aquí (el contrato `decision` de `.importlinter` no permite importar `interpreter`)."""

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Protocol

from agent_core.domain import Decision, JsonValue, Locale, Probability, ProviderSpec


class ProviderTimeout(Exception):
    """El proveedor no respondió a tiempo; `decide` pasa al siguiente de la cadena."""


class ProviderError(Exception):
    """El proveedor falló; el mensaje nunca lleva la entrada, la salida ni claves."""


class DecisionConfigError(Exception):
    """Error de configuración (`DecisionModelDef`, esquema, artefacto); no es `low_confidence`."""


@dataclass(frozen=True, slots=True)
class RawPrediction:
    """Salida de un proveedor antes de calibrar."""
    value: dict[str, JsonValue]
    p_raw: dict[str, float | None]
    top_k: dict[str, list[tuple[str, float]]] = field(default_factory=dict)
    latency_ms: int = 0
    tokens: int = 0
    cost_usd: Decimal = Decimal("0")
    model_version: str = "unknown"


class DecisionProvider(Protocol):
    """Adaptador de un proveedor; `name` coincide con `ProviderSpec.provider`."""
    name: str

    def predict(self, spec: ProviderSpec, inputs_model_view: dict[str, JsonValue],
                schema: dict[str, JsonValue], locale: Locale) -> RawPrediction: ...


@dataclass(frozen=True, slots=True)
class DecisionOutput:
    """Resultado de `decide`. `model_calls` cuenta las llamadas `predict` (con reintentos y fallbacks)."""
    value: dict[str, JsonValue]
    p_cal: dict[str, float | None]
    p_raw: dict[str, float | None]
    top_k: dict[str, list[tuple[str, float]]]
    above_threshold: dict[str, bool]
    provider_used: str
    model_version: str
    fallback_depth: int
    latency_ms: int
    tokens: int
    cost_usd: Decimal
    decision_id: str
    model_calls: int

    def as_decision(self) -> Decision:
        p_cal: dict[str, Probability | None] = dict(self.p_cal)
        return Decision(decision_id=self.decision_id, value=self.value, p_cal=p_cal,
                        provider_used=self.provider_used, model_version=self.model_version)

    def __repr__(self) -> str:
        # Sin `value`: puede llevar contenido del usuario (tokens o slots).
        return (f"DecisionOutput(decision_id={self.decision_id!r}, provider_used={self.provider_used!r}, "
                f"model_version={self.model_version!r}, fallback_depth={self.fallback_depth}, "
                f"model_calls={self.model_calls}, above_threshold={self.above_threshold!r})")
