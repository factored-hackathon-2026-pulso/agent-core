"""JEV through the llm-gateway service: `GatewayJevTransport` implements `JevTransport` over `POST /v1/jev`.

The service holds the JEV key and does the retrying (429/529, one total budget); this side sends the request
and maps the typed answer back to the exceptions `JevProvider` already understands. The provider, which builds
the questions and reads the answers, is unchanged.
"""

import logging
from typing import Any

import httpx

from agent_core.decision.providers.jev import JevTransportError
from agent_core.decision.types import DecisionConfigError
from agent_core.domain import JsonValue, dumps, loads
from agent_telemetry import correlation as telemetry_correlation

_LOG = logging.getLogger("agent_core.adapters.llm")

# The service enforces `timeout_ms`; the client waits a little longer so the typed `timeout` answer arrives.
CLIENT_MARGIN_S = 2.0
_CORRELATION_LABELS = {"run_id": "run_id", "turn_id": "turn_id", "session_id": "session_id",
                       "agentcore.release": "release", "agentcore.agent": "agent"}


class GatewayJevTransport:
    """Cumple `JevTransport`. `retryable_responses` cuenta los 429/529 que el servicio vio y reintentó."""

    def __init__(self, base_url: str, token: str, *, client: httpx.Client | None = None) -> None:
        self._url = base_url.rstrip("/") + "/v1/jev"
        self._headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        self._client = client if client is not None else httpx.Client()
        self.retryable_responses: dict[int, int] = {}

    def __repr__(self) -> str:
        return "GatewayJevTransport()"

    def close(self) -> None:
        self._client.close()

    def send(self, request: dict[str, JsonValue], timeout_ms: int) -> dict[str, JsonValue]:
        envelope: dict[str, JsonValue] = {"request": request, "timeout_ms": timeout_ms}
        labels: dict[str, JsonValue] = {
            _CORRELATION_LABELS[k]: v for k, v in telemetry_correlation().items() if k in _CORRELATION_LABELS}
        if labels:
            envelope["labels"] = labels
        try:
            response = self._client.post(
                self._url, content=dumps(envelope).encode(), headers=self._outgoing(),
                timeout=httpx.Timeout(timeout_ms / 1000 + CLIENT_MARGIN_S))
        except httpx.TimeoutException:
            raise TimeoutError from None
        except Exception as error:  # connection, transport or anything unforeseen; its text is never logged
            _LOG.warning("jev via llm-gateway: %s", type(error).__name__)
            raise JevTransportError from None
        return self._interpret(response)

    def _outgoing(self) -> dict[str, str]:
        from opentelemetry.propagate import inject

        headers = dict(self._headers)
        inject(headers)  # the service's `jev systemone` span nests under the caller's span
        return headers

    def _interpret(self, response: httpx.Response) -> dict[str, JsonValue]:
        try:
            data = loads(response.content.decode())
        except (ValueError, UnicodeDecodeError):
            data = None
        if response.status_code == 200:
            return self._success(data)
        error: Any = data.get("error") if isinstance(data, dict) else None
        kind = error.get("kind") if isinstance(error, dict) else None
        if kind == "timeout":
            raise TimeoutError
        if kind == "unauthorized":
            raise DecisionConfigError("jev: el llm-gateway rechazó el token del consumidor")
        upstream = error.get("upstream_status") if isinstance(error, dict) else None
        _LOG.warning("jev via llm-gateway: http %s kind %s", response.status_code,
                     kind if isinstance(kind, str) and kind.isidentifier() else "?")
        status = upstream if isinstance(upstream, int) and not isinstance(upstream, bool) else None
        raise JevTransportError(status)

    def _success(self, data: JsonValue) -> dict[str, JsonValue]:
        payload = data.get("response") if isinstance(data, dict) else None
        retried = data.get("retried") if isinstance(data, dict) else None
        if not isinstance(payload, dict) or not isinstance(retried, list):
            raise JevTransportError from None
        for status in retried:
            if isinstance(status, int) and not isinstance(status, bool):
                self.retryable_responses[status] = self.retryable_responses.get(status, 0) + 1
        return payload


class UnconfiguredJevTransport:
    """Stands in when no llm-gateway is configured: a call is a configuration error, like a missing key."""

    def send(self, request: dict[str, JsonValue], timeout_ms: int) -> dict[str, JsonValue]:
        raise DecisionConfigError(
            "falta AGENTCORE_LLM_GATEWAY_URL/AGENTCORE_LLM_GATEWAY_TOKEN: JEV va por el llm-gateway")
