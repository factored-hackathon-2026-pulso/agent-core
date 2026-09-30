"""Adaptador de JEV sobre un transporte inyectable (spec §3.3, ADR 0005).

La forma del request/response es **provisional** (P0b: el contrato real de JEV no está verificado):

    request  = {"model": str, "input": <vista model>, "schema": {...}, "locale": "es"}
    response = {"value": {...}, "probabilities": {campo: p}, "top_k": {campo: [[valor, p], ...]},
                "latency_ms": int}

La API key nunca pasa por aquí: la añade el transporte real. Este adaptador nunca la ve, ni la registra."""

from collections.abc import Mapping
from decimal import Decimal
from typing import Protocol

from agent_core.decision.types import DecisionConfigError, ProviderError, ProviderTimeout, RawPrediction
from agent_core.domain import JsonValue, Locale, ProviderSpec


class JevTransportError(Exception):
    """Falla del transporte HTTP. Lleva solo el código HTTP (`None` = sin respuesta); nunca el request, el
    cuerpo de la respuesta ni la key."""

    def __init__(self, status: int | None = None) -> None:
        super().__init__(f"jev: HTTP {status}" if status is not None else "jev: sin respuesta HTTP")
        self.status = status


class JevTransport(Protocol):
    """Envía el request y devuelve la respuesta ya decodificada. `TimeoutError` = se agotó `timeout_ms`."""

    def send(self, request: dict[str, JsonValue], timeout_ms: int) -> dict[str, JsonValue]: ...


class _Recorder(Protocol):
    def record(self, payload: object) -> None: ...


class JevProvider:
    name = "jev"

    def __init__(self, transport: JevTransport, capture: _Recorder | None = None) -> None:
        self._transport = transport
        self._capture = capture

    def __repr__(self) -> str:
        return "JevProvider()"

    def predict(self, spec: ProviderSpec, inputs_model_view: dict[str, JsonValue],
                schema: dict[str, JsonValue], locale: Locale) -> RawPrediction:
        model = spec.config.get("model")
        timeout_ms = spec.config.get("timeout_ms")
        if not isinstance(model, str) or not model:
            raise DecisionConfigError("jev: config.model es obligatorio (string)")
        if isinstance(timeout_ms, bool) or not isinstance(timeout_ms, int) or timeout_ms <= 0:
            raise DecisionConfigError("jev: config.timeout_ms es obligatorio (entero > 0)")
        request: dict[str, JsonValue] = {"model": model, "input": inputs_model_view, "schema": schema,
                                         "locale": locale}
        if self._capture is not None:
            self._capture.record(request)
        try:
            response = self._transport.send(request, timeout_ms)
        except TimeoutError:
            raise ProviderTimeout("jev: timeout") from None
        except Exception as exc:  # el mensaje original puede traer el request: solo el tipo
            raise ProviderError(f"jev: error de transporte ({type(exc).__name__})") from None
        return _parse(response, model)


def _parse(response: object, model: str) -> RawPrediction:
    if not isinstance(response, dict) or not isinstance(response.get("value"), dict):
        raise ProviderError("jev: respuesta sin 'value' objeto")
    value: dict[str, JsonValue] = response["value"]
    probabilities = response.get("probabilities")
    if probabilities is not None and not isinstance(probabilities, dict):
        raise ProviderError("jev: 'probabilities' mal formado")
    given = probabilities or {}
    p_raw: dict[str, float | None] = {field: None for field in value}
    for field, p in given.items():
        p_raw[field] = _probability(p)
    return RawPrediction(value=value, p_raw=p_raw, top_k=_top_k(response.get("top_k")),
                         latency_ms=_latency(response.get("latency_ms")), model_version=f"jev:{model}")


def _probability(raw: JsonValue) -> float:
    if isinstance(raw, bool) or not isinstance(raw, int | Decimal | float):
        raise ProviderError("jev: probabilidad no numérica")
    p = float(raw)
    if not 0.0 <= p <= 1.0:
        raise ProviderError("jev: probabilidad fuera de [0, 1]")
    return p


def _top_k(raw: JsonValue) -> dict[str, list[tuple[str, float]]]:
    if raw is None:
        return {}
    if not isinstance(raw, Mapping):
        raise ProviderError("jev: 'top_k' mal formado")
    result: dict[str, list[tuple[str, float]]] = {}
    for field, pairs in raw.items():
        if not isinstance(pairs, list):
            raise ProviderError("jev: 'top_k' mal formado")
        items: list[tuple[str, float]] = []
        for pair in pairs:
            if not isinstance(pair, list) or len(pair) != 2 or not isinstance(pair[0], str):
                raise ProviderError("jev: 'top_k' mal formado")
            items.append((pair[0], _probability(pair[1])))
        result[field] = items
    return result


def _latency(raw: JsonValue) -> int:
    if raw is None:
        return 0
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < 0:
        raise ProviderError("jev: 'latency_ms' inválido")
    return raw
