"""`grant_active` over HTTP: the assignment service (the platform) says whether a delegation is still valid.

A delegation (`on_behalf_of`) is signed for a few minutes but its grant can end earlier (the case moved to
someone else, the analyst left): on every call that carries one the engine asks
`grant_active(grant_ref, now)`.
The platform answers `GET {url}/api/v1/internal/grants/{grantRef}` with `{"active": true|false}` behind a
shared bearer secret. It **fails closed**: any error, timeout, bad status or non-boolean answer is "not
active". Only a positive answer is cached, briefly (`ttl`), so a revocation takes at most that long to bite.
"""

import logging
import os
from collections import OrderedDict
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta
from urllib.parse import quote, urlsplit

import httpx

from agent_core.domain import SchemaError, loads

_LOG = logging.getLogger("agent_core.adapters.grants")

GRANTS_URL_ENV = "AGENTCORE_GRANTS_URL"
GRANTS_TOKEN_ENV = "AGENTCORE_GRANTS_TOKEN"
GRANTS_TIMEOUT_ENV = "AGENTCORE_GRANTS_TIMEOUT_S"
GRANTS_CACHE_TTL_ENV = "AGENTCORE_GRANTS_CACHE_TTL_S"
DEFAULT_TIMEOUT_S = 3.0
DEFAULT_CACHE_TTL_S = 5.0
MAX_CACHED = 512


class HttpGrantActive:
    def __init__(self, base_url: str, token: str, *, timeout_s: float = DEFAULT_TIMEOUT_S,
                 cache_ttl_s: float = DEFAULT_CACHE_TTL_S, client: httpx.Client | None = None) -> None:
        self._base = base_url.rstrip("/") + "/api/v1/internal/grants/"
        self._headers = {"Authorization": f"Bearer {token}"}
        self._timeout = httpx.Timeout(timeout_s)
        self._ttl = timedelta(seconds=cache_ttl_s)
        self._client = client if client is not None else httpx.Client()
        self._active_until: OrderedDict[str, datetime] = OrderedDict()

    def close(self) -> None:
        self._client.close()

    def __call__(self, grant_ref: str, now: datetime) -> bool:
        until = self._active_until.get(grant_ref)
        if until is not None and now < until:
            return True
        self._active_until.pop(grant_ref, None)
        if not self._ask(grant_ref):
            return False
        if self._ttl > timedelta(0):
            self._active_until[grant_ref] = now + self._ttl
            while len(self._active_until) > MAX_CACHED:
                self._active_until.popitem(last=False)
        return True

    def _ask(self, grant_ref: str) -> bool:
        try:
            response = self._client.get(self._base + quote(grant_ref, safe=""), headers=self._headers,
                                        timeout=self._timeout)
            if response.status_code != 200:
                _LOG.warning("grants service answered http %d", response.status_code)
                return False
            data = loads(response.content.decode())
        except Exception as error:  # transport, decoding or anything else: not active (text not logged)
            _LOG.warning("grants service unreachable or unreadable: %s", type(error).__name__)
            return False
        return isinstance(data, dict) and data.get("active") is True


def from_env(env: Mapping[str, str]) -> HttpGrantActive:
    url = (env.get(GRANTS_URL_ENV) or "").strip()
    token = (env.get(GRANTS_TOKEN_ENV) or "").strip()
    if not url or not token:
        raise SchemaError(f"{GRANTS_URL_ENV} y {GRANTS_TOKEN_ENV} van juntas: falta alguna")
    parsed = urlsplit(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise SchemaError(f"{GRANTS_URL_ENV} debe ser una URL http(s) con host")
    timeout = _number(env, GRANTS_TIMEOUT_ENV, DEFAULT_TIMEOUT_S, positive=True)
    ttl = _number(env, GRANTS_CACHE_TTL_ENV, DEFAULT_CACHE_TTL_S, positive=False)
    return HttpGrantActive(url, token, timeout_s=timeout, cache_ttl_s=ttl)


def _number(env: Mapping[str, str], name: str, default: float, *, positive: bool) -> float:
    try:
        value = float(env.get(name) or default)
    except ValueError:
        raise SchemaError(f"{name} debe ser un número") from None
    if value < 0 or (positive and value == 0):
        raise SchemaError(f"{name} debe ser {'positivo' if positive else 'no negativo'}")
    return value


def http_grant_active(ctx: object) -> Callable[[str, datetime], bool]:
    """Factory for `serve --grant-active agent_core.adapters.grants:http_grant_active`."""
    return from_env(os.environ)
