"""M5 — DecisionModel y Understand (spec `docs/specs/motor/m05-decision-model.md`).

Interfaz pública: `DecisionService.decide`, `UnderstandService.run`, los proveedores y sus tipos. La
calibración offline vive en `agent_core.decision.calibration`. M2 y M4 solo deben usar esto."""

from agent_core.decision.providers.classifier import ArtifactLoader, ClassifierProvider
from agent_core.decision.providers.jev import JevProvider, JevTransport, JevTransportError
from agent_core.decision.providers.jev_http import HttpJevTransport
from agent_core.decision.providers.llm_structured import LlmStructuredProvider
from agent_core.decision.providers.rule import RuleProvider
from agent_core.decision.service import DecisionService
from agent_core.decision.types import (
    DecisionConfigError,
    DecisionOutput,
    DecisionProvider,
    EventScope,
    ProviderError,
    ProviderTimeout,
    RawPrediction,
)
from agent_core.decision.understand import UnderstandContext, UnderstandResult, UnderstandService

__all__ = [
    "ArtifactLoader", "ClassifierProvider", "DecisionConfigError", "DecisionOutput", "DecisionProvider",
    "DecisionService", "EventScope", "HttpJevTransport", "JevProvider", "JevTransport", "JevTransportError",
    "LlmStructuredProvider", "ProviderError", "ProviderTimeout", "RawPrediction", "RuleProvider",
    "UnderstandContext", "UnderstandResult", "UnderstandService",
]
