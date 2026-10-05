"""Comprobaciones de `/readyz` que `serve` registra (brief A3): qué bloquea y qué solo se informa."""

from collections.abc import Mapping
from types import SimpleNamespace
from typing import Any

from agent_core.composition.readiness import READY_LLM_GATEWAY_ENV, READY_TOOL_SERVICE_ENV, build_readiness

URL = {"AGENTCORE_LLM_GATEWAY_URL": "http://gw.internal", "AGENTCORE_TOOL_SERVICE_URL": "http://tools.internal"}


def _build(env: Mapping[str, str], *, up: bool | dict[str, bool] = True,
           verifiers: tuple[Any, ...] = ()) -> tuple[dict[str, Any], frozenset[str], list[str]]:
    seen: list[str] = []

    def probe(url: str) -> bool:
        seen.append(url)
        return up if isinstance(up, bool) else up[url]

    checks, optional = build_readiness(env, postgres=lambda: True, verifiers=verifiers, probe=probe)
    return dict(checks), optional, seen


def test_postgres_and_keys_are_always_registered_and_required() -> None:
    checks, optional, _ = _build({})
    assert set(checks) == {"postgres", "keys"} and optional == frozenset()


def test_the_llm_gateway_is_checked_and_required_when_configured() -> None:
    checks, optional, seen = _build(URL)
    assert "llm_gateway" in checks and "llm_gateway" not in optional
    assert checks["llm_gateway"]() is True
    assert "http://gw.internal" in seen[0]


def test_the_llm_gateway_can_be_made_optional() -> None:
    _, optional, _ = _build({**URL, READY_LLM_GATEWAY_ENV: "0"})
    assert "llm_gateway" in optional


def test_the_tool_service_is_optional_by_default_and_required_on_request() -> None:
    checks, optional, _ = _build(URL)
    assert "tool_service" in checks and "tool_service" in optional
    _, optional, _ = _build({**URL, READY_TOOL_SERVICE_ENV: "1"})
    assert "tool_service" not in optional


def test_a_dependency_that_does_not_answer_fails_closed() -> None:
    checks, _, _ = _build(URL, up=False)
    assert checks["llm_gateway"]() is False and checks["tool_service"]() is False


def test_a_probe_that_raises_fails_closed_without_leaking() -> None:
    def probe(url: str) -> bool:
        raise OSError("token=abc host=secret.internal")

    checks, _ = build_readiness(URL, postgres=lambda: True, verifiers=(), probe=probe)
    assert dict(checks)["llm_gateway"]() is False


def test_keys_fail_when_a_key_file_reload_failed() -> None:
    broken = SimpleNamespace(last_reload_error="SchemaError")
    healthy = SimpleNamespace(last_reload_error=None)
    checks, _, _ = _build({}, verifiers=(healthy, broken))
    assert checks["keys"]() is False
    checks, _, _ = _build({}, verifiers=(healthy, object()))  # a verifier without the attribute is fine
    assert checks["keys"]() is True


def test_the_schema_check_is_registered_and_required_when_given() -> None:
    checks, optional = build_readiness({}, postgres=lambda: True, verifiers=(), schema=lambda: False)
    assert dict(checks)["schema"]() is False and "schema" not in optional


def test_without_a_schema_check_none_is_registered() -> None:
    checks, _ = build_readiness({}, postgres=lambda: True, verifiers=())
    assert "schema" not in dict(checks)
