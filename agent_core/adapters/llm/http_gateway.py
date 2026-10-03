"""`HttpLLMGateway`: `LLMGateway` over the standalone llm-gateway service (HTTP, see its api/openapi.yaml).

The registry still owns the `Prompt` and its `ModelProfile` (they are part of the release); each call sends
them to the service, which keeps no state, makes one provider call and prices it from the price sent here.
"""

import logging
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx
from opentelemetry.propagate import inject

from agent_core.domain import (
    EntityRef,
    GatewayError,
    GatewayErrorKind,
    JsonValue,
    Locale,
    ModelProfile,
    Prompt,
    SchemaError,
    StructuredMode,
    dumps,
    loads,
)
from agent_core.ports import GenerationResult, RegistryPort
from agent_telemetry import correlation as telemetry_correlation

_LOG = logging.getLogger("agent_core.adapters.llm")

LLM_GATEWAY_URL_ENV = "AGENTCORE_LLM_GATEWAY_URL"
LLM_GATEWAY_TOKEN_ENV = "AGENTCORE_LLM_GATEWAY_TOKEN"

# The service enforces `profile.timeout_s`; the HTTP client waits a little longer so the service's own typed
# `timeout` answer arrives instead of a client-side timeout.
CLIENT_MARGIN_S = 5.0

_CORRELATION_LABELS = {"run_id": "run_id", "turn_id": "turn_id", "session_id": "session_id",
                       "agentcore.release": "release", "agentcore.agent": "agent"}
_KINDS = {kind.value: kind for kind in GatewayErrorKind}


class HttpLLMGateway:
    """Gateway de generación sobre el servicio llm-gateway; solo lanza `GatewayError` (salvo error de uso)."""

    def __init__(self, registry: RegistryPort, base_url: str, token: str, *,
                 client: httpx.Client | None = None) -> None:
        self._registry = registry
        self._url = base_url.rstrip("/") + "/v1/generate"
        self._headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        self._client = client if client is not None else httpx.Client()

    def close(self) -> None:
        self._client.close()

    def generate(self, prompt: EntityRef, inputs_model_view: dict[str, JsonValue], locale: Locale,
                 schema: dict[str, JsonValue] | None = None) -> GenerationResult:
        prompt_ref = prompt
        prompt_def = self._registry.get(prompt_ref, Prompt)
        profile_ref = prompt_def.model_profile.require_exact()
        profile = self._registry.get(profile_ref, ModelProfile)
        text = prompt_def.locales.get(locale)
        if text is None:
            raise SchemaError(f"el prompt {prompt_ref} no tiene el locale {locale}")
        try:
            body = dumps(_request(text, inputs_model_view, schema, profile, prompt_ref, profile_ref))
        except (TypeError, ValueError):  # not serializable: a programming error
            raise SchemaError("las entradas del prompt no son JSON canónico") from None
        try:
            response = self._client.post(self._url, content=body.encode(), headers=self._outgoing_headers(),
                                         timeout=httpx.Timeout(profile.timeout_s + CLIENT_MARGIN_S))
        except httpx.TimeoutException:
            raise _error(GatewayErrorKind.timeout, profile, "client timeout") from None
        except Exception as error:  # connection, transport or anything unforeseen; its text is never logged
            raise _error(GatewayErrorKind.unavailable, profile, type(error).__name__) from None
        try:
            return _result(response, profile)
        except GatewayError:
            raise
        except Exception as error:  # nothing else leaves the adapter
            raise _error(GatewayErrorKind.unavailable, profile, type(error).__name__) from None

    def _outgoing_headers(self) -> dict[str, str]:
        headers = dict(self._headers)
        inject(headers)  # W3C traceparent: the service's `chat` span nests under the caller's span
        return headers


def _request(text: str, inputs: dict[str, JsonValue], schema: dict[str, JsonValue] | None,
             profile: ModelProfile, prompt: EntityRef, profile_ref: EntityRef) -> dict[str, JsonValue]:
    labels: dict[str, JsonValue] = {"prompt": str(prompt), "model_profile": str(profile_ref)}
    for key, value in telemetry_correlation().items():
        if key in _CORRELATION_LABELS:
            labels[_CORRELATION_LABELS[key]] = value
    body: dict[str, JsonValue] = {"prompt": text, "inputs": inputs}
    if schema is not None:
        body["schema"] = schema
    body["profile"] = {
        "endpoint_alias": profile.endpoint_alias, "model": profile.model,
        "temperature": profile.temperature, "max_tokens": profile.max_tokens, "timeout_s": profile.timeout_s,
        "structured": "native" if profile.structured is StructuredMode.native else "prompted",
        "price": {"input_per_mtok": format(profile.price.input_per_mtok, "f"),
                  "output_per_mtok": format(profile.price.output_per_mtok, "f")}}
    body["labels"] = labels
    return body


def _error(kind: GatewayErrorKind, profile: ModelProfile, why: str, **usage: Any) -> GatewayError:
    """Logs only the kind, model and a short cause (never a body or exception text) and builds the error."""
    _LOG.warning("llm-gateway %s model=%s cause=%s", kind.value, profile.model, why)
    return GatewayError(kind, **usage)


def _result(response: httpx.Response, profile: ModelProfile) -> GenerationResult:
    try:
        data = loads(response.content.decode())
    except (ValueError, UnicodeDecodeError):
        raise _error(GatewayErrorKind.unavailable, profile, f"undecodable body (http {response.status_code})",
                     model=profile.model) from None
    if not isinstance(data, dict):
        raise _error(GatewayErrorKind.unavailable, profile, "unexpected body", model=profile.model)
    if response.status_code == 200:
        return _success(data, profile)
    raise _failure(data, response.status_code, profile)


def _success(data: dict[str, JsonValue], profile: ModelProfile) -> GenerationResult:
    try:
        model, tokens_in, tokens_out = data["model"], data["tokens_in"], data["tokens_out"]
        cost, known = data["cost_usd"], data["usage_known"]
        if (not isinstance(model, str) or "output" not in data or not isinstance(known, bool)
                or not isinstance(cost, str) or not _is_int(tokens_in) or not _is_int(tokens_out)):
            raise ValueError("shape")
        return GenerationResult(output=data["output"], tokens_in=tokens_in, tokens_out=tokens_out,  # type: ignore[arg-type]
                                cost_usd=Decimal(cost), model=model, usage_known=known)
    except (KeyError, ValueError, InvalidOperation):
        raise _error(GatewayErrorKind.unavailable, profile, "unexpected success body",
                     model=profile.model) from None


def _failure(data: dict[str, JsonValue], status: int, profile: ModelProfile) -> GatewayError:
    error = data.get("error")
    kind_name = error.get("kind") if isinstance(error, dict) else None
    kind = _KINDS.get(kind_name) if isinstance(kind_name, str) else None
    if kind is None or not isinstance(error, dict):
        # bad_request, unauthorized, limits, an unknown kind: all mean this deployment is misconfigured or
        # out of step with the service, not a model failure.
        why = f"service rejected the call (http {status}, kind {_safe(kind_name)})"
        return _error(GatewayErrorKind.unavailable, profile, why, model=profile.model)
    model = error.get("model")
    tokens_in, tokens_out, cost = error.get("tokens_in"), error.get("tokens_out"), error.get("cost_usd")
    usage: dict[str, Any] = {"model": model if isinstance(model, str) else profile.model}
    if _is_int(tokens_in) and _is_int(tokens_out) and isinstance(cost, str):
        try:
            usage.update(tokens_in=tokens_in, tokens_out=tokens_out, cost_usd=Decimal(cost))
        except InvalidOperation:
            pass
    return _error(kind, profile, f"http {status}", **usage)


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _safe(value: object) -> str:
    """A short, printable form of an untrusted kind name for the log."""
    return value[:40] if isinstance(value, str) and value.isidentifier() else "?"


class UnconfiguredLLMGateway:
    """Stands in when no llm-gateway is configured: every generation fails as `unavailable`, so M8 falls
    back to its templates exactly as it does when the provider is down."""

    def generate(self, prompt: EntityRef, inputs_model_view: dict[str, JsonValue], locale: Locale,
                 schema: dict[str, JsonValue] | None = None) -> GenerationResult:
        _LOG.warning("llm-gateway not configured (AGENTCORE_LLM_GATEWAY_URL is unset)")
        raise GatewayError(GatewayErrorKind.unavailable)


def gateway_is_up(base_url: str, *, timeout_s: float = 3.0) -> bool:
    """Whether the service answers `GET /healthz` (public, no token). Never raises."""
    try:
        return httpx.get(base_url.rstrip("/") + "/healthz", timeout=timeout_s).status_code == 200
    except Exception:
        return False
