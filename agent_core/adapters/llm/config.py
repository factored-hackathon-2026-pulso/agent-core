"""Endpoints del gateway (`LLM_ENDPOINTS`) y construcción del cliente `openai` (spec §2)."""

from collections.abc import Mapping
from dataclasses import dataclass

import httpx
from openai import OpenAI

from agent_core.domain import SchemaError, loads

ENDPOINTS_ENV = "LLM_ENDPOINTS"


@dataclass(frozen=True, slots=True)
class EndpointConfig:
    """Endpoint OpenAI-compatible identificado por alias."""

    alias: str
    base_url: str
    api_key_env: str  # nombre de la variable, nunca la key


def load_endpoints(env: Mapping[str, str]) -> dict[str, EndpointConfig]:
    """Lee `LLM_ENDPOINTS` (JSON `{alias: {base_url, api_key_env}}`); vacío o ausente => sin endpoints."""
    raw = env.get(ENDPOINTS_ENV)
    if not raw:
        return {}
    try:
        data = loads(raw)
    except ValueError:
        raise SchemaError(f"{ENDPOINTS_ENV} no es JSON válido") from None
    if not isinstance(data, dict):
        raise SchemaError(f"{ENDPOINTS_ENV} debe ser un objeto {{alias: {{base_url, api_key_env}}}}")
    endpoints: dict[str, EndpointConfig] = {}
    for alias, entry in data.items():
        base_url = entry.get("base_url") if isinstance(entry, dict) else None
        key_env = entry.get("api_key_env") if isinstance(entry, dict) else None
        if not isinstance(base_url, str) or not base_url or not isinstance(key_env, str) or not key_env:
            raise SchemaError(f"{ENDPOINTS_ENV}[{alias!r}] necesita base_url y api_key_env")
        endpoints[alias] = EndpointConfig(alias, base_url, key_env)
    return endpoints


def default_client(endpoint: EndpointConfig, api_key: str, timeout_s: int) -> OpenAI:
    """Cliente sin reintentos: una llamada a `generate` hace a lo sumo una request (spec §4)."""
    return OpenAI(base_url=endpoint.base_url, api_key=api_key, timeout=httpx.Timeout(float(timeout_s)),
                  max_retries=0)
