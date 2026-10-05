"""Comprobaciones de `/readyz` de `serve`: qué dependencias se miran y cuáles bloquean la disponibilidad.

Postgres y las claves siempre bloquean. El llm-gateway bloquea si está configurado (apágalo con
`AGENTCORE_READY_REQUIRE_LLM_GATEWAY=0`). El tool-service solo se informa salvo que se pida con
`AGENTCORE_READY_REQUIRE_TOOL_SERVICE=1`. Una dependencia sin URL configurada no se registra."""

from collections.abc import Callable, Iterable, Mapping
from typing import Any

import httpx

from agent_core.adapters.llm.http_gateway import LLM_GATEWAY_URL_ENV
from agent_core.adapters.tools.http_executor import TOOL_SERVICE_URL_ENV

READY_LLM_GATEWAY_ENV = "AGENTCORE_READY_REQUIRE_LLM_GATEWAY"
READY_TOOL_SERVICE_ENV = "AGENTCORE_READY_REQUIRE_TOOL_SERVICE"
PROBE_TIMEOUT_S = 0.8  # menor que el plazo de `/readyz`: una dependencia lenta falla, no cuelga la sonda

Probe = Callable[[str], bool]
Check = tuple[str, Callable[[], bool]]


def http_probe(base_url: str) -> bool:
    """`True` si `GET {base_url}/healthz` responde 200. No sigue redirecciones ni lanza."""
    try:
        return httpx.get(base_url.rstrip("/") + "/healthz", timeout=PROBE_TIMEOUT_S).status_code == 200
    except Exception:
        return False


def _keys_loaded(verifiers: Iterable[Any]) -> bool:
    """Las claves de identidad se cargaron y la última recarga no falló (un verificador sin ese dato vale)."""
    return all(getattr(v, "last_reload_error", None) is None for v in verifiers)


def _answers(probe: Probe, url: str) -> bool:
    try:
        return probe(url)
    except Exception:  # its message may carry a host or a credential
        return False


def build_readiness(env: Mapping[str, str], *, postgres: Callable[[], bool], verifiers: Iterable[Any],
                    probe: Probe = http_probe,
                    schema: Callable[[], bool] | None = None) -> tuple[tuple[Check, ...], frozenset[str]]:
    checks: list[Check] = [("postgres", postgres), ("keys", lambda: _keys_loaded(list(verifiers)))]
    if schema is not None:
        checks.append(("schema", schema))
    optional: set[str] = set()
    gateway = (env.get(LLM_GATEWAY_URL_ENV) or "").strip()
    if gateway:
        checks.append(("llm_gateway", lambda: _answers(probe, gateway)))
        if env.get(READY_LLM_GATEWAY_ENV) == "0":
            optional.add("llm_gateway")
    tools = (env.get(TOOL_SERVICE_URL_ENV) or "").strip()
    if tools:
        checks.append(("tool_service", lambda: _answers(probe, tools)))
        if env.get(READY_TOOL_SERVICE_ENV) != "1":
            optional.add("tool_service")
    return tuple(checks), frozenset(optional)
