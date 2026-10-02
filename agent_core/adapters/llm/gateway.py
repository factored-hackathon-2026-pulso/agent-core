"""`OpenAICompatGateway`: `LLMGateway` sobre el SDK `openai` (spec del gateway §3)."""

import contextvars
import logging
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from decimal import Decimal
from typing import Any

import openai
from openai import OpenAI
from opentelemetry import trace
from opentelemetry.trace import Span, Status, StatusCode, Tracer

from agent_core.adapters.llm.config import EndpointConfig, default_client
from agent_core.adapters.llm.cost import price_of
from agent_core.adapters.llm.output import OutputError, parse_output
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
    canonical_bytes,
)
from agent_core.ports import GenerationResult, RegistryPort
from agent_telemetry import correlation as telemetry_correlation
from agent_telemetry import tracer as telemetry_tracer

_LOG = logging.getLogger("agent_core.adapters.llm")
SCHEMA_INSTRUCTION = (
    "\n\nResponde únicamente con un objeto JSON que cumpla este JSON Schema, sin texto adicional ni "
    "bloques de código:\n")

ClientFactory = Callable[[EndpointConfig, str, int], OpenAI]


class OpenAICompatGateway:
    """Gateway de generación sobre cualquier endpoint compatible con la API de OpenAI."""

    def __init__(self, registry: RegistryPort, endpoints: dict[str, EndpointConfig], env: Mapping[str, str],
                 client_factory: ClientFactory = default_client, tracer: Tracer | None = None) -> None:
        self._registry = registry
        self._endpoints = endpoints
        self._env = env
        self._client_factory = client_factory
        self._tracer = tracer  # None: agent_telemetry's current provider, resolved per call (F3/I5)

    def _active_tracer(self) -> Tracer:
        return self._tracer if self._tracer is not None else telemetry_tracer("agent_core.adapters.llm")

    def generate(self, prompt: EntityRef, inputs_model_view: dict[str, JsonValue], locale: Locale,
                 schema: dict[str, JsonValue] | None = None) -> GenerationResult:
        """Genera una respuesta del modelo del perfil del prompt; solo lanza `GatewayError` (spec §3.2)."""
        prompt_def = self._registry.get(prompt, Prompt)
        profile = self._registry.get(prompt_def.model_profile.require_exact(), ModelProfile)
        text = prompt_def.locales.get(locale)
        if text is None:
            raise SchemaError(f"el prompt {prompt} no tiene el locale {locale}")
        with self._active_tracer().start_as_current_span(
                f"chat {profile.model}", record_exception=False, set_status_on_exception=False) as span:
            span.set_attributes(dict(telemetry_correlation()))  # ADR 0003 #4: the bound turn's ids
            span.set_attribute("gen_ai.operation.name", "chat")
            span.set_attribute("gen_ai.provider.name", "openai")  # the wire protocol of the SDK (F14)
            span.set_attribute("agentcore.endpoint_alias", profile.endpoint_alias)
            span.set_attribute("gen_ai.request.model", profile.model)
            span.set_attribute("agentcore.prompt", str(prompt))
            span.set_attribute("agentcore.model_profile", str(prompt_def.model_profile.require_exact()))
            try:
                result = self._call(profile, text, inputs_model_view, schema)
            except GatewayError as error:
                # Solo tipo y uso: sin evento `exception` (mensaje/stacktrace) y con estado sin descripción.
                span.set_status(Status(StatusCode.ERROR))
                span.set_attribute("agentcore.gateway.error_kind", error.kind.value)
                _set_usage(span, error.model, error.tokens_in, error.tokens_out)
                raise
            _set_usage(span, result.model, result.tokens_in, result.tokens_out)
            return result

    def _call(self, profile: ModelProfile, text: str, inputs: dict[str, JsonValue],
              schema: dict[str, JsonValue] | None) -> GenerationResult:
        """Resuelve endpoint y key, hace la request y traduce el resultado (spec §3.1)."""
        endpoint = self._endpoints.get(profile.endpoint_alias)
        if endpoint is None:
            _LOG.error("alias de endpoint sin configurar alias=%s", profile.endpoint_alias)
            raise GatewayError(GatewayErrorKind.unavailable, model=profile.model)
        api_key = self._env.get(endpoint.api_key_env, "").strip()
        if not api_key:
            _LOG.error("variable de key vacía alias=%s", endpoint.alias)
            raise GatewayError(GatewayErrorKind.unavailable, model=profile.model)
        try:
            kwargs = _request(profile, text, inputs, schema)
        except (TypeError, ValueError):  # no serializable: error de programación
            raise SchemaError("las entradas del prompt no son JSON canónico") from None
        try:
            client = self._client_factory(endpoint, api_key, profile.timeout_s)
            response = _create(client, kwargs, profile, endpoint.alias)
            return _result(response, profile, schema)
        except GatewayError:
            raise
        except Exception as error:  # nada más sale del adaptador; nunca se registra `str(error)`
            why = type(error).__name__
            raise _error(GatewayErrorKind.unavailable, endpoint.alias, profile, why) from None


def _set_usage(span: Span, model: str | None, tokens_in: int | None, tokens_out: int | None) -> None:
    """Deja en el span el modelo real y el uso, cuando se conocen."""
    for name, value in (("gen_ai.response.model", model), ("gen_ai.usage.input_tokens", tokens_in),
                        ("gen_ai.usage.output_tokens", tokens_out)):
        if value is not None:
            span.set_attribute(name, value)


def _request(profile: ModelProfile, text: str, inputs: dict[str, JsonValue],
             schema: dict[str, JsonValue] | None) -> dict[str, Any]:
    system = text
    if schema is not None and profile.structured is StructuredMode.prompted:
        system += SCHEMA_INSTRUCTION + canonical_bytes(schema).decode()
    kwargs: dict[str, Any] = {
        "model": profile.model,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": canonical_bytes(inputs).decode()}],
        "temperature": float(profile.temperature),  # el SDK exige float; el costo nunca usa float
        "max_tokens": profile.max_tokens,
    }
    if schema is not None and profile.structured is StructuredMode.native:
        kwargs["response_format"] = {
            "type": "json_schema", "json_schema": {"name": "output", "schema": schema, "strict": True}}
    return kwargs


def _create(client: OpenAI, kwargs: dict[str, Any], profile: ModelProfile, alias: str) -> Any:
    """Una request con plazo total de `timeout_s` y errores traducidos (spec §3.1 paso 5 y §3.2)."""
    # Los timeouts por fase del SDK son por chunk: un endpoint que gotea mantendría vivo el hilo. Al
    # vencer el plazo total se cierra el cliente desde el hilo que espera; la lectura bloqueada falla y
    # el hilo abandonado termina (los workers de ThreadPoolExecutor no son daemon y se unen al salir).
    pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="llm-gateway")
    context = contextvars.copy_context()  # el contexto de OpenTelemetry sigue al hilo
    future = pool.submit(context.run, lambda: client.chat.completions.create(**kwargs))
    try:
        return future.result(timeout=profile.timeout_s)
    except FutureTimeout:
        try:
            client.close()
        except Exception:  # el hilo está abandonado; un fallo al cerrar no cambia el error
            pass
        raise _error(GatewayErrorKind.timeout, alias, profile, "plazo total") from None
    except openai.APITimeoutError:
        raise _error(GatewayErrorKind.timeout, alias, profile, "timeout") from None
    except openai.RateLimitError:
        raise _error(GatewayErrorKind.rate_limited, alias, profile, "429") from None
    except openai.APIStatusError as error:
        raise _error(GatewayErrorKind.unavailable, alias, profile, f"http {error.status_code}") from None
    except Exception as error:  # conexión, transporte o algo no previsto: nada más sale del adaptador
        raise _error(GatewayErrorKind.unavailable, alias, profile, type(error).__name__) from None
    finally:
        pool.shutdown(wait=False, cancel_futures=True)


def _error(kind: GatewayErrorKind, alias: str, profile: ModelProfile, why: str) -> GatewayError:
    """Registra solo alias, tipo y motivo corto (nunca el mensaje de la excepción) y arma el error."""
    _LOG.warning("gateway %s alias=%s model=%s causa=%s", kind.value, alias, profile.model, why)
    return GatewayError(kind, model=profile.model)


def _clip(text: str, limit: int = 80) -> str:
    """Acota el motivo antes de registrarlo: puede incluir nombres de propiedad elegidos por el modelo."""
    return text if len(text) <= limit else text[:limit] + "..."


def _result(response: Any, profile: ModelProfile, schema: dict[str, JsonValue] | None) -> GenerationResult:
    """Traduce la respuesta del proveedor a `GenerationResult` o a `GatewayError` con el uso informado."""
    usage = response.usage
    tokens_in: int | None = getattr(usage, "prompt_tokens", None)
    tokens_out: int | None = getattr(usage, "completion_tokens", None)
    cost: Decimal | None = None
    if tokens_in is None or tokens_out is None:  # uso ausente o incompleto = no informado
        tokens_in = tokens_out = None
    else:
        cost = price_of(profile.price, tokens_in, tokens_out)
    model = response.model or profile.model

    def fail(kind: GatewayErrorKind, why: str) -> GatewayError:
        _LOG.warning("gateway %s model=%s causa=%s", kind.value, model, why)
        return GatewayError(kind, tokens_in=tokens_in, tokens_out=tokens_out, cost_usd=cost, model=model)

    if not response.choices:
        raise fail(GatewayErrorKind.invalid_output, "sin choices")
    choice = response.choices[0]
    if choice.finish_reason:  # `_result` runs in the caller's thread, inside the `chat` span
        trace.get_current_span().set_attribute("gen_ai.response.finish_reasons", [str(choice.finish_reason)])
    if choice.finish_reason == "content_filter" or getattr(choice.message, "refusal", None):
        raise fail(GatewayErrorKind.refused, "rechazo del modelo")
    content = choice.message.content or ""
    output: JsonValue = content
    if schema is not None:
        if choice.finish_reason == "length":
            raise fail(GatewayErrorKind.invalid_output, "salida truncada")
        try:
            output = parse_output(content, schema)
        except OutputError as error:
            raise fail(GatewayErrorKind.invalid_output, _clip(error.reason)) from None
    if cost is None:
        _LOG.warning("respuesta sin usage model=%s", model)
    return GenerationResult(output=output, tokens_in=tokens_in or 0, tokens_out=tokens_out or 0,
                            cost_usd=cost if cost is not None else Decimal("0"), model=model,
                            usage_known=cost is not None)
