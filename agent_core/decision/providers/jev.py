"""Adaptador de JEV sobre un transporte inyectable (spec §3.3, ADR 0005), contra el contrato real de
`POST https://api.typesafe.ai/v1/systemone` (https://docs.typesafe.ai/api).

    request  = {"state": {"locale", "input": <vista model>}, "model": str, "questions": {id: pregunta}}
    response = {"model": <id versionado>, "answers": {id: respuesta}, "usage": {input_tokens, output_tokens}}

JEV no extrae valores libres: solo responde preguntas tipadas, así que el esquema de salida se traduce a
preguntas (spec §3.3.1):

    string + enum -> `choice`     boolean -> `noul`     array de enum -> un `noul` por opción (`multi_flow`)
    escala        -> `score` (solo declarada en `config.questions`)     object libre (slots) -> se omite

El `confidence` de JEV no es `p_cal` ni `p_raw`: se descarta y la calibración de M5 va encima de `p_raw`.
La API key nunca pasa por aquí: la añade el transporte real. Este adaptador nunca la ve, ni la registra."""

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal, Protocol

from agent_core.decision.types import (
    DecisionConfigError,
    ProviderError,
    ProviderTimeout,
    RawPrediction,
    value_label,
)
from agent_core.domain import JsonValue, Locale, ProviderSpec

_MAX_CHOICES = 255
_MIN_LEVELS, _MAX_LEVELS = 2, 10
_TOP_K = 5
_HALF = Decimal("0.5")
_PER_MILLION = Decimal(1_000_000)

Kind = Literal["choice", "noul", "score", "multi"]


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


@dataclass(frozen=True, slots=True)
class _Field:
    """Un campo del esquema y la(s) pregunta(s) JEV que lo cubren."""
    name: str
    kind: Kind
    options: tuple[str, ...]              # choice/multi: enum; score: índices "0", "1", …
    levels: tuple[JsonValue, ...] = ()    # score: valor de salida de cada nivel

    def question_ids(self) -> list[str]:
        return [f"{self.name}__{option}" for option in self.options] if self.kind == "multi" else [self.name]


class JevProvider:
    name = "jev"

    def __init__(self, transport: JevTransport, capture: _Recorder | None = None) -> None:
        self._transport = transport
        self._capture = capture

    def __repr__(self) -> str:
        return "JevProvider()"

    def predict(self, spec: ProviderSpec, inputs_model_view: dict[str, JsonValue],
                schema: dict[str, JsonValue], locale: Locale) -> RawPrediction:
        config = spec.config
        model, timeout_ms = _model(config), _timeout(config)
        multi_flow, rate = _multi_flow(config), _rate(config)
        fields, questions = _questions(schema, config, multi_flow)
        request: dict[str, JsonValue] = {
            "state": {"locale": locale, "input": inputs_model_view}, "model": model, "questions": questions}
        if self._capture is not None:
            self._capture.record(request)
        try:
            response = self._transport.send(request, timeout_ms)
        except TimeoutError:
            raise ProviderTimeout("jev: timeout") from None
        except DecisionConfigError:
            raise
        except JevTransportError as exc:
            raise ProviderError(str(exc)) from None  # solo el código HTTP
        except Exception as exc:  # el mensaje original puede traer el request: solo el tipo
            raise ProviderError(f"jev: error de transporte ({type(exc).__name__})") from None
        return _parse(response, fields, rate)


def _model(config: Mapping[str, JsonValue]) -> str:
    model = config.get("model")
    if not isinstance(model, str) or not model:
        raise DecisionConfigError("jev: config.model es obligatorio (string)")
    return model


def _timeout(config: Mapping[str, JsonValue]) -> int:
    timeout_ms = config.get("timeout_ms")
    if isinstance(timeout_ms, bool) or not isinstance(timeout_ms, int) or timeout_ms <= 0:
        raise DecisionConfigError("jev: config.timeout_ms es obligatorio (entero > 0)")
    return timeout_ms


def _multi_flow(config: Mapping[str, JsonValue]) -> bool:
    multi_flow = config.get("multi_flow", False)
    if not isinstance(multi_flow, bool):
        raise DecisionConfigError("jev: config.multi_flow debe ser booleano")
    return multi_flow


def _rate(config: Mapping[str, JsonValue]) -> Decimal:
    """USD por millón de tokens de entrada (la salida de JEV no se cobra); 0 si no se configura."""
    rate = config.get("input_usd_per_mtok", 0)
    if isinstance(rate, bool) or not isinstance(rate, int | Decimal) or rate < 0:
        raise DecisionConfigError("jev: config.input_usd_per_mtok debe ser un número >= 0")
    return Decimal(rate)


def _overrides(config: Mapping[str, JsonValue]) -> dict[str, dict[str, JsonValue]]:
    raw = config.get("questions", {})
    if not isinstance(raw, dict) or not all(isinstance(v, dict) for v in raw.values()):
        raise DecisionConfigError("jev: config.questions debe ser {campo: {instructions, criteria, …}}")
    return {name: item for name, item in raw.items() if isinstance(item, dict)}


def _enum_options(prop: Mapping[str, JsonValue], where: str) -> tuple[str, ...]:
    enum = prop.get("enum")
    if (not isinstance(enum, list) or not enum or not all(isinstance(o, str) for o in enum)
            or len(set(enum)) != len(enum) or len(enum) > _MAX_CHOICES):
        raise DecisionConfigError(f"jev: {where}: enum de 1 a {_MAX_CHOICES} strings distintos")
    return tuple(o for o in enum if isinstance(o, str))


def _text(*candidates: JsonValue) -> str | None:
    return next((c for c in candidates if isinstance(c, str) and c), None)


def _questions(schema: Mapping[str, JsonValue], config: Mapping[str, JsonValue], multi_flow: bool
               ) -> tuple[list[_Field], dict[str, JsonValue]]:
    """Traduce el esquema a campos y preguntas JEV; sin ningún campo preguntable es error de configuración."""
    properties = schema.get("properties")
    overrides = _overrides(config)
    fields: list[_Field] = []
    wire: dict[str, JsonValue] = {}
    for name, prop in (properties.items() if isinstance(properties, dict) else []):
        if not isinstance(prop, dict):
            continue
        override = overrides.get(name, {})
        override_type = override.get("type")
        if override_type not in (None, "score"):
            raise DecisionConfigError(f"jev: config.questions.{name}.type solo admite 'score'")
        instructions = _text(override.get("instructions"), prop.get("description"), prop.get("title"))
        ptype = prop.get("type")
        if override_type == "score":
            fields.append(_score(name, override, instructions, wire))
        elif ptype == "string" and "enum" in prop:
            options = _enum_options(prop, name)
            criteria = _criteria(name, options, override.get("criteria"))
            wire[name] = {"type": "choice", "criteria": criteria,
                          "instructions": instructions
                          or f"Elige el valor de '{name}' que mejor describe el mensaje."}
            fields.append(_Field(name, "choice", options))
        elif ptype == "boolean":
            wire[name] = {"type": "noul",
                          "instructions": instructions or f"¿Se cumple '{name}' en el mensaje?"}
            fields.append(_Field(name, "noul", ()))
        elif ptype == "array" and multi_flow and isinstance(prop.get("items"), dict):
            items = prop["items"]
            options = _enum_options(items, f"{name}[]") if isinstance(items, dict) else ()
            base = instructions or f"¿El mensaje también pide esta opción de '{name}'?"
            for option in options:
                wire[f"{name}__{option}"] = {"type": "noul", "instructions": f"{base} Opción: {option}"}
            fields.append(_Field(name, "multi", options))
    if not fields:
        raise DecisionConfigError("jev: el esquema no tiene campos preguntables (enum, boolean o score)")
    return fields, wire


def _criteria(name: str, options: tuple[str, ...], raw: JsonValue) -> dict[str, JsonValue]:
    if raw is None:
        return dict.fromkeys(options)
    if (not isinstance(raw, dict) or set(raw) != set(options)
            or not all(v is None or isinstance(v, str) for v in raw.values())):
        raise DecisionConfigError(f"jev: config.questions.{name}.criteria debe cubrir exactamente el enum")
    return {option: raw[option] for option in options}


def _score(name: str, override: Mapping[str, JsonValue], instructions: str | None,
           wire: dict[str, JsonValue]) -> _Field:
    levels = override.get("levels")
    if (not isinstance(levels, list) or not _MIN_LEVELS <= len(levels) <= _MAX_LEVELS
            or not all(isinstance(lv, dict) and "value" in lv and isinstance(lv.get("description"), str)
                       for lv in levels)):
        raise DecisionConfigError(
            f"jev: config.questions.{name}.levels: de {_MIN_LEVELS} a {_MAX_LEVELS} "
            "niveles {value, description}")
    steps = [lv for lv in levels if isinstance(lv, dict)]
    wire[name] = {"type": "score", "criteria": [str(lv["description"]) for lv in steps],
                  "instructions": instructions or f"Puntúa '{name}' según la escala."}
    return _Field(name, "score", tuple(str(i) for i in range(len(steps))),
                  tuple(lv["value"] for lv in steps))


# --- respuesta ---------------------------------------------------------------------------------------

def _parse(response: object, fields: list[_Field], rate: Decimal) -> RawPrediction:
    if not isinstance(response, dict):
        raise ProviderError("jev: respuesta mal formada")
    model = response.get("model")
    tokens, input_tokens = _usage(response.get("usage"))
    cost = Decimal(input_tokens) * rate / _PER_MILLION
    try:
        if not isinstance(model, str) or not model:
            raise ProviderError("jev: respuesta sin 'model'")
        answers = response.get("answers")
        if not isinstance(answers, dict):
            raise ProviderError("jev: respuesta sin 'answers' objeto")
        value: dict[str, JsonValue] = {}
        p_raw: dict[str, float | None] = {}
        top_k: dict[str, list[tuple[str, float]]] = {}
        for field in fields:
            _map(field, answers, value, p_raw, top_k)
    except ProviderError as exc:
        raise ProviderError(str(exc), tokens=tokens, cost_usd=cost) from None
    return RawPrediction(value=value, p_raw=p_raw, top_k=top_k, latency_ms=0, tokens=tokens, cost_usd=cost,
                         model_version=f"jev:{model}")


def _usage(raw: JsonValue) -> tuple[int, int]:
    """`(tokens totales, tokens de entrada)`; un `usage` ausente o inválido es una respuesta inválida."""
    if isinstance(raw, dict):
        tokens_in, tokens_out = raw.get("input_tokens"), raw.get("output_tokens")
        if (isinstance(tokens_in, int) and isinstance(tokens_out, int) and not isinstance(tokens_in, bool)
                and not isinstance(tokens_out, bool) and tokens_in >= 0 and tokens_out >= 0):
            return tokens_in + tokens_out, tokens_in
    raise ProviderError("jev: 'usage' inválido")


def _number(raw: JsonValue) -> Decimal:
    if isinstance(raw, bool) or not isinstance(raw, int | Decimal | float):
        raise ProviderError("jev: probabilidad no numérica")
    p = raw if isinstance(raw, Decimal) else Decimal(str(raw))
    if not 0 <= p <= 1:
        raise ProviderError("jev: probabilidad fuera de [0, 1]")
    return p


def _answer(answers: Mapping[str, JsonValue], question_id: str, kind: str) -> Mapping[str, JsonValue]:
    answer = answers.get(question_id)
    if not isinstance(answer, dict) or answer.get("type") != kind:
        raise ProviderError(f"jev: respuesta ausente o de otro tipo para '{question_id}'")
    return answer


def _noul(answers: Mapping[str, JsonValue], question_id: str) -> Decimal:
    return _number(_answer(answers, question_id, "noul").get("noul"))


def _distribution(answer: Mapping[str, JsonValue], keys: tuple[str, ...]) -> dict[str, Decimal]:
    raw = answer.get("probabilities")
    if not isinstance(raw, dict) or not raw or not set(raw) <= set(keys):
        raise ProviderError("jev: 'probabilities' ausente o con claves fuera de lo pedido")
    return {key: _number(p) for key, p in raw.items()}


def _top(distribution: Mapping[str, Decimal], label: Mapping[str, str]) -> list[tuple[str, float]]:
    pairs = sorted(((label[key], p) for key, p in distribution.items()), key=lambda item: (-item[1], item[0]))
    return [(name, float(p)) for name, p in pairs[:_TOP_K]]


def _map(field: _Field, answers: Mapping[str, JsonValue], value: dict[str, JsonValue],
         p_raw: dict[str, float | None], top_k: dict[str, list[tuple[str, float]]]) -> None:
    name = field.name
    match field.kind:
        case "choice":
            answer = _answer(answers, name, "choice")
            picked = answer.get("choice")
            distribution = _distribution(answer, field.options)
            if not isinstance(picked, str) or picked not in field.options or picked not in distribution:
                raise ProviderError("jev: 'choice' fuera de las opciones pedidas")
            value[name], p_raw[name] = picked, float(distribution[picked])
            top_k[name] = _top(distribution, {key: key for key in distribution})
        case "noul":
            p = _noul(answers, name)
            chosen = p >= _HALF
            value[name], p_raw[name] = chosen, float(p if chosen else 1 - p)
            top_k[name] = _top({"true": p, "false": 1 - p}, {"true": "true", "false": "false"})
        case "multi":
            picked_options = [option for option in field.options
                              if _noul(answers, f"{name}__{option}") >= _HALF]
            value[name], p_raw[name] = list(picked_options), None  # sin calibrar: nunca lleva umbral
        case "score":
            answer = _answer(answers, name, "score")
            distribution = _distribution(answer, field.options)
            best = max(distribution, key=lambda key: (distribution[key], -int(key)))
            value[name], p_raw[name] = field.levels[int(best)], float(distribution[best])
            labels = {key: value_label(field.levels[int(key)]) for key in distribution}
            top_k[name] = _top(distribution, labels)
