"""`HttpGrantActive` against a fake platform (httpx MockTransport; synthetic data, no network)."""

import logging
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from agent_core.adapters.grants import GRANTS_TOKEN_ENV, GRANTS_URL_ENV, HttpGrantActive, from_env
from agent_core.domain import SchemaError

BASE = "https://platform.test"
TOKEN = "grants-token-SECRETO-0001"
NOW = datetime(2026, 10, 4, 12, 0, tzinfo=UTC)
REF = "CASE-01J0000000000000000000000:STF-01J0000000000000000000000"


class Platform:
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.answer = httpx.Response(200, json={"active": True})

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.answer


@pytest.fixture
def platform() -> Platform:
    return Platform()


def _grants(platform: Platform, **kw: float) -> HttpGrantActive:
    return HttpGrantActive(BASE, TOKEN, client=httpx.Client(transport=httpx.MockTransport(platform)), **kw)


def test_it_asks_the_platform_with_the_bearer_and_the_encoded_grant(platform: Platform) -> None:
    assert _grants(platform)(REF, NOW) is True

    request = platform.requests[0]
    assert request.method == "GET" and request.headers["authorization"] == f"Bearer {TOKEN}"
    assert request.url.raw_path.decode() == "/api/v1/internal/grants/" + REF.replace(":", "%3A")


def test_a_positive_answer_is_cached_briefly_and_then_asked_again(platform: Platform) -> None:
    grants = _grants(platform, cache_ttl_s=5.0)

    assert grants(REF, NOW) and grants(REF, NOW + timedelta(seconds=4))
    assert len(platform.requests) == 1
    platform.answer = httpx.Response(200, json={"active": False})  # revoked meanwhile
    assert grants(REF, NOW + timedelta(seconds=6)) is False
    assert len(platform.requests) == 2


def test_a_negative_answer_is_never_cached_so_a_regrant_is_seen_at_once(platform: Platform) -> None:
    platform.answer = httpx.Response(200, json={"active": False})
    grants = _grants(platform)

    assert grants(REF, NOW) is False
    platform.answer = httpx.Response(200, json={"active": True})
    assert grants(REF, NOW) is True


def test_a_zero_ttl_never_caches(platform: Platform) -> None:
    grants = _grants(platform, cache_ttl_s=0.0)

    grants(REF, NOW)
    grants(REF, NOW)

    assert len(platform.requests) == 2


@pytest.mark.parametrize("answer", [
    httpx.Response(200, json={"active": "true"}),
    httpx.Response(200, json={"active": 1}),
    httpx.Response(200, json={}),
    httpx.Response(200, json=[True]),
    httpx.Response(200, content=b"<html>"),
    httpx.Response(401, json={"active": True}),
    httpx.Response(404, json={"active": True}),
    httpx.Response(500, json={"active": True}),
], ids=["string", "int", "missing", "list", "html", "401", "404", "500"])
def test_anything_but_an_explicit_true_is_not_active(platform: Platform, answer: httpx.Response) -> None:
    platform.answer = answer

    assert _grants(platform)(REF, NOW) is False


def test_an_unreachable_platform_fails_closed_and_logs_no_secret(caplog: pytest.LogCaptureFixture) -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"refused {TOKEN}", request=request)

    grants = HttpGrantActive(BASE, TOKEN, client=httpx.Client(transport=httpx.MockTransport(boom)))
    caplog.set_level(logging.DEBUG)

    assert grants(REF, NOW) is False
    assert TOKEN not in caplog.text and REF not in caplog.text


def test_the_cache_is_bounded(platform: Platform) -> None:
    grants = _grants(platform)

    for index in range(600):
        grants(f"CASE-{index}:STF-1", NOW)

    assert len(grants._active_until) <= 512


def test_the_factory_wants_url_and_token_together_and_valid() -> None:
    assert from_env({GRANTS_URL_ENV: "http://platform:8000", GRANTS_TOKEN_ENV: "t"}) is not None
    for env in ({}, {GRANTS_URL_ENV: "http://x"}, {GRANTS_TOKEN_ENV: "t"},
                {GRANTS_URL_ENV: "ftp://x", GRANTS_TOKEN_ENV: "t"},
                {GRANTS_URL_ENV: "http://x", GRANTS_TOKEN_ENV: "t", "AGENTCORE_GRANTS_TIMEOUT_S": "0"},
                {GRANTS_URL_ENV: "http://x", GRANTS_TOKEN_ENV: "t", "AGENTCORE_GRANTS_CACHE_TTL_S": "-1"},
                {GRANTS_URL_ENV: "http://x", GRANTS_TOKEN_ENV: "t", "AGENTCORE_GRANTS_CACHE_TTL_S": "x"}):
        with pytest.raises(SchemaError):
            from_env(env)
