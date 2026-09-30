"""Unidad 5 — LLM gateway (docs/specs/2026-09-28-llm-gateway-design.md). Interfaz pública."""

from agent_core.adapters.llm.config import EndpointConfig, default_client, load_endpoints
from agent_core.adapters.llm.cost import price_of
from agent_core.adapters.llm.gateway import OpenAICompatGateway

__all__ = ["EndpointConfig", "OpenAICompatGateway", "default_client", "load_endpoints", "price_of"]
