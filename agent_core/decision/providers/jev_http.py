"""Transporte HTTP real de JEV (`POST /v1/systemone`) sobre la stdlib, sin dependencias nuevas.

La API key llega como un `Callable[[], str]` que inyecta la composición (única que lee el entorno): aquí solo
va en el header, y nunca en `repr`, excepciones ni logs. `timeout_ms` es el presupuesto total de `send`,
reintentos incluidos. Reintenta con backoff exponencial solo en 429/529 (respeta `Retry-After`); 401/422 y
cualquier otro error no se reintentan."""

import time
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit

from agent_core.decision.providers.jev import JevTransportError
from agent_core.decision.types import DecisionConfigError
from agent_core.domain import JsonValue, dumps, loads
from agent_core.ports import Clock

DEFAULT_BASE_URL = "https://api.typesafe.ai"
_PATH = "/v1/systemone"
_RETRYABLE = frozenset({429, 529})
_LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
_MAX_RESPONSE_BYTES = 1_048_576
_NS_PER_S = 1_000_000_000


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Un redirect nunca se sigue: reenviaría el header `Authorization` a otro destino."""

    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> None:
        return None


def _check_base_url(base_url: str) -> str:
    parts = urlsplit(base_url)
    local = parts.scheme == "http" and (parts.hostname or "") in _LOCAL_HOSTS
    if not (parts.scheme == "https" or local) or not parts.netloc:
        raise DecisionConfigError("jev: base_url debe ser https (http solo hacia localhost)")
    return base_url.rstrip("/")


def _retry_after_seconds(raw: str | None) -> float | None:
    if raw is None:
        return None
    try:
        seconds = float(raw)
    except ValueError:
        return None  # fecha HTTP u otro formato: se usa el backoff
    return seconds if seconds >= 0 else None


class HttpJevTransport:
    """Cumple `JevTransport`. `sleep` y `clock` son inyectables para probar el backoff sin esperar."""

    def __init__(self, api_key: Callable[[], str], clock: Clock, *, base_url: str = DEFAULT_BASE_URL,
                 max_retries: int = 3, backoff_base_ms: int = 250,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self._api_key = api_key
        self._clock = clock
        self._url = _check_base_url(base_url) + _PATH
        self._max_retries = max_retries
        self._backoff_base_s = backoff_base_ms / 1000
        self._sleep = sleep
        self._opener = urllib.request.build_opener(_NoRedirect)

    def __repr__(self) -> str:
        return "HttpJevTransport()"

    def send(self, request: dict[str, JsonValue], timeout_ms: int) -> dict[str, JsonValue]:
        key = self._api_key().strip()
        if not key:
            raise DecisionConfigError("jev: la API key está vacía")
        body = dumps(request).encode("utf-8")
        deadline = self._clock.monotonic_ns() + timeout_ms * 1_000_000
        attempt = 0
        while True:
            remaining_ns = deadline - self._clock.monotonic_ns()
            if remaining_ns <= 0:
                raise TimeoutError
            status, retry_after, payload = self._post(body, key, remaining_ns / _NS_PER_S)
            if status == 200:
                return _decode(payload)
            if status in _RETRYABLE and attempt < self._max_retries:
                wait = retry_after if retry_after is not None else self._backoff_base_s * 2**attempt
                if wait * _NS_PER_S >= deadline - self._clock.monotonic_ns():
                    raise TimeoutError
                self._sleep(wait)
                attempt += 1
                continue
            raise JevTransportError(status) from None

    def _post(self, body: bytes, key: str, timeout_s: float) -> tuple[int, float | None, bytes]:
        """`(status, retry_after, cuerpo)`; nunca propaga el cuerpo ni el request en una excepción."""
        http_request = urllib.request.Request(  # esquema validado en el constructor
            self._url, data=body, method="POST",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json",
                     "Accept": "application/json"})
        try:
            with self._opener.open(http_request, timeout=timeout_s) as response:
                return response.status, None, response.read(_MAX_RESPONSE_BYTES + 1)
        except urllib.error.HTTPError as exc:
            retry_after = _retry_after_seconds(exc.headers.get("Retry-After") if exc.headers else None)
            exc.close()
            return exc.code, retry_after, b""
        except TimeoutError:
            raise TimeoutError from None
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, TimeoutError):
                raise TimeoutError from None
            raise JevTransportError from None
        except OSError:
            raise JevTransportError from None


def _decode(payload: bytes) -> dict[str, JsonValue]:
    if len(payload) > _MAX_RESPONSE_BYTES:
        raise JevTransportError from None
    try:
        decoded = loads(payload)
    except ValueError:
        raise JevTransportError from None
    if not isinstance(decoded, dict):
        raise JevTransportError from None
    return decoded
