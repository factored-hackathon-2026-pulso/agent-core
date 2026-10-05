"""Proveedor `rule` (spec §3.3): decisiones triviales con p ∈ {0, 1}. Propuesta P7, confirmada.

    config = {"cases": [{"when": {"path": "<clave de la entrada>", "equals": <json>}, "value": {...}}],
              "default": {...}}

El primer caso que coincide gana (`p_raw = 1.0` por campo); sin caso, `default` con `p_raw = 0.0`; sin
`default`, `ProviderError`. Sin JSON Logic (M2 lo tiene y no se puede importar).

Dos extensiones, ambas opcionales y deterministas:

- `when` puede ser `{"path", "count": n}` (la entrada es una lista de exactamente `n` elementos) o
  `{"path", "count_min": n}` (al menos `n`). Una entrada ausente o que no es lista nunca coincide.
- `when.narrow = {"text_from": <clave(s) de la entrada>, "fields": [campo, ...]}` deja, de la lista de `path`,
  solo los elementos que el texto menciona: el valor de un campo de texto aparece en el texto (sin mayúsculas
  ni tildes) o el de un campo numérico es igual a un número del texto. Si el texto menciona valores de varios
  campos, el elemento tiene que coincidir en todos; si eso no deja ninguno, valen los que coinciden en alguno.
  Un campo que ningún elemento satisface no veta (quien recuerda mal un monto sigue señalando el comercio).
  `count`/`count_min` y `value_from` de ese caso ven la lista ya reducida; sin texto no queda ningún elemento.
- un caso puede traer `value_from: {campo: {"path": <clave de la entrada>, "at": [índice | clave, ...]}}`:
  completa `value[campo]` con lo que hay en esa posición de la entrada. Si no resuelve, `ProviderError`
  (nunca se inventa un valor). Sirve para "si hay un único candidato, ese es": `match-cargo`."""

import re
import unicodedata
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from agent_core.decision.types import DecisionConfigError, ProviderError, RawPrediction
from agent_core.domain import JsonValue, Locale, ProviderSpec

type _Step = str | int


@dataclass(frozen=True)
class _Narrow:
    text_from: list[str]
    fields: list[str]


_NUMBER = re.compile(r"\d[\d.,]*\d|\d")


def _fold(text: str) -> str:
    """Minúsculas, sin tildes y con espacios simples: lo que se compara con lo que escribió la persona."""
    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    return " ".join(plain.casefold().split())


def _decimal(text: str) -> Decimal | None:
    try:
        return Decimal(text)
    except InvalidOperation:  # pragma: no cover - el patrón solo deja dígitos y un punto
        return None


def _readings(token: str) -> set[Decimal]:
    """Lecturas razonables de una cifra escrita con `.` y/o `,` (`344456.72`, `344.456,72`, `1,586,612.76`,
    `1.038.345`). Una ambigua (`1.038`: mil o un decimal) da las dos."""
    seps = [i for i, ch in enumerate(token) if ch in ".,"]
    if not seps:
        value = _decimal(token)
        return {value} if value is not None else set()
    head, tail = token[:seps[-1]], token[seps[-1] + 1:]
    kinds = {token[i] for i in seps}
    readings: list[str] = []
    if len(kinds) == 2 or (len(seps) > 1 and len(tail) != 3):
        readings.append(re.sub(r"[.,]", "", head) + "." + tail)  # el último separador es el decimal
    if len(seps) > 1 and len(kinds) == 1:
        readings.append(re.sub(r"[.,]", "", token))  # 1.038.345: solo miles
    if len(seps) == 1:
        readings += [head + tail, head + "." + tail] if len(tail) == 3 else [head + "." + tail]
    return {value for r in readings if (value := _decimal(r)) is not None}


def _numbers(text: str) -> set[Decimal]:
    found: set[Decimal] = set()
    for raw in _NUMBER.findall(text):
        found |= _readings(raw)
    return found


def _mentions(row: JsonValue, text: str, numbers: set[Decimal], fields: list[str]) -> bool:
    if not isinstance(row, dict):
        return False
    for field in fields:
        value = row.get(field)
        if isinstance(value, bool) or value is None:
            continue
        if isinstance(value, str) and not _is_number(value):
            folded = _fold(value)
            if len(folded) >= 3 and folded in text:
                return True
        elif _is_number(value) and Decimal(str(value)) in numbers:
            return True
    return False


def _is_number(value: JsonValue) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float, Decimal)):
        return True
    try:
        Decimal(str(value))
    except InvalidOperation:
        return False
    return isinstance(value, str)


@dataclass(frozen=True)
class _Case:
    path: str
    equals: JsonValue
    count: int | None
    count_min: int | None
    value: dict[str, JsonValue]
    value_from: dict[str, tuple[str, list[_Step]]]
    narrow: _Narrow | None = None

    def view(self, inputs: dict[str, JsonValue], path: str,
             full: dict[str, JsonValue] | None = None) -> JsonValue:
        """La entrada de `path` (vista `model`); la lista de `self.path` ya reducida por `narrow`, si lo trae.

        Con `full` (ADR 0027) las filas se eligen COMPARANDO en la vista `full` (el texto y los valores en
        claro), pero lo que se devuelve son las filas de la vista `model`, en las mismas posiciones: la salida
        nunca lleva un dato en claro. Si las dos listas no miden lo mismo, no queda ninguna fila (falla
        cerrada)."""
        found = inputs.get(path)
        if self.narrow is None or path != self.path:
            return found
        basis = inputs if full is None else full
        rows = basis.get(path)
        texts = [basis.get(k) for k in self.narrow.text_from]
        if (not isinstance(found, list) or not isinstance(rows, list) or len(rows) != len(found)
                or not all(isinstance(t, str) for t in texts)):
            return [] if isinstance(found, list) else found
        text = _fold(" ".join(t for t in texts if isinstance(t, str)))
        numbers = _numbers(text)
        by_field = [{i for i, row in enumerate(rows) if _mentions(row, text, numbers, [field])}
                    for field in self.narrow.fields]
        active = [indexes for indexes in by_field if indexes]  # los campos que el texto sí menciona
        if not active:
            return []
        every = [i for i in range(len(rows)) if all(i in indexes for indexes in active)]
        keep = every or [i for i in range(len(rows)) if any(i in indexes for indexes in active)]
        return [found[i] for i in keep]

    def matches(self, inputs: dict[str, JsonValue], full: dict[str, JsonValue] | None = None) -> bool:
        if self.path not in inputs:
            return False
        found = self.view(inputs, self.path, full)
        if self.count is None and self.count_min is None:
            return _same(found, self.equals)
        if not isinstance(found, list):
            return False
        return len(found) == self.count if self.count is not None else len(found) >= (self.count_min or 0)


class RuleProvider:
    name = "rule"

    def predict(self, spec: ProviderSpec, inputs_model_view: dict[str, JsonValue],
                schema: dict[str, JsonValue], locale: Locale) -> RawPrediction:
        return self._run(spec, inputs_model_view, None)

    def predict_full(self, spec: ProviderSpec, inputs_model_view: dict[str, JsonValue],
                     inputs_full: dict[str, JsonValue], schema: dict[str, JsonValue],
                     locale: Locale) -> RawPrediction:
        """Con `compare_on: full` (ADR 0027): compara en `inputs_full`; devuelve solo valores de la vista
        `model`."""
        return self._run(spec, inputs_model_view, inputs_full)

    @staticmethod
    def _run(spec: ProviderSpec, inputs_model_view: dict[str, JsonValue],
             full: dict[str, JsonValue] | None) -> RawPrediction:
        if spec.config.get("compare_on", "model") not in ("model", "full"):
            raise DecisionConfigError("rule: config.compare_on debe ser 'model' o 'full'")
        cases = _cases(spec.config)
        default = spec.config.get("default")
        if default is not None and not isinstance(default, dict):
            raise DecisionConfigError("rule: config.default debe ser un objeto")
        for case in cases:
            if case.matches(inputs_model_view, full):
                value = {**case.value, **_derived(case, inputs_model_view, full)}
                return RawPrediction(value=value, p_raw=dict.fromkeys(value, 1.0), model_version="rule-1")
        if default is None:
            raise ProviderError("rule: ningún caso coincide y no hay default")
        return RawPrediction(value=default, p_raw=dict.fromkeys(default, 0.0), model_version="rule-1")


def _derived(case: _Case, inputs: dict[str, JsonValue],
             full: dict[str, JsonValue] | None) -> dict[str, JsonValue]:
    out: dict[str, JsonValue] = {}
    for field, (path, at) in case.value_from.items():
        current: JsonValue = case.view(inputs, path, full)
        for step in at:
            if isinstance(step, int) and isinstance(current, list) and -len(current) <= step < len(current):
                current = current[step]
            elif isinstance(step, str) and isinstance(current, dict) and step in current:
                current = current[step]
            else:
                raise ProviderError("rule: value_from no resolvió en la entrada")
        out[field] = current
    return out


def _count(raw: JsonValue, name: str) -> int | None:
    if raw is None:
        return None
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < 0:
        raise DecisionConfigError(f"rule: when.{name} debe ser un entero >= 0")
    return raw


def _steps(raw: JsonValue) -> list[_Step]:
    if not isinstance(raw, list) or not raw:
        raise DecisionConfigError("rule: cada value_from necesita path (texto) y at (lista de pasos)")
    steps: list[_Step] = []
    for step in raw:
        if isinstance(step, bool) or not isinstance(step, (str, int)):
            raise DecisionConfigError("rule: cada value_from necesita path (texto) y at (lista de pasos)")
        steps.append(step)
    return steps


def _value_from(raw: JsonValue) -> dict[str, tuple[str, list[_Step]]]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise DecisionConfigError("rule: value_from debe ser un objeto")
    out: dict[str, tuple[str, list[_Step]]] = {}
    for field, ref in raw.items():
        path = ref.get("path") if isinstance(ref, dict) else None
        if not isinstance(ref, dict) or not isinstance(path, str):
            raise DecisionConfigError("rule: cada value_from necesita path (texto) y at (lista de pasos)")
        out[field] = (path, _steps(ref.get("at")))
    return out


def _narrow(raw: JsonValue) -> _Narrow | None:
    if raw is None:
        return None
    message = "rule: when.narrow necesita text_from (texto o lista de textos) y fields (lista de textos)"
    if not isinstance(raw, dict):
        raise DecisionConfigError(message)
    source, fields = raw.get("text_from"), raw.get("fields")
    keys = [source] if isinstance(source, str) else source
    names_ok = isinstance(keys, list) and bool(keys) and all(isinstance(k, str) and k for k in keys)
    fields_ok = isinstance(fields, list) and bool(fields) and all(isinstance(f, str) and f for f in fields)
    if not names_ok or not fields_ok or not isinstance(keys, list) or not isinstance(fields, list):
        raise DecisionConfigError(message)
    return _Narrow(text_from=[k for k in keys if isinstance(k, str)],
                   fields=[f for f in fields if isinstance(f, str)])


def _cases(config: dict[str, JsonValue]) -> list[_Case]:
    raw = config.get("cases")
    if not isinstance(raw, list):
        raise DecisionConfigError("rule: config.cases debe ser una lista")
    cases: list[_Case] = []
    for case in raw:
        when = case.get("when") if isinstance(case, dict) else None
        value = case.get("value") if isinstance(case, dict) else None
        if (not isinstance(case, dict) or not isinstance(when, dict) or not isinstance(when.get("path"), str)
                or not isinstance(value, dict)):
            raise DecisionConfigError("rule: cada caso necesita when.path, una condición y value (objeto)")
        conditions = [name for name in ("equals", "count", "count_min") if name in when]
        if len(conditions) != 1:
            raise DecisionConfigError("rule: when lleva exactamente una de equals, count o count_min")
        path = when["path"]
        assert isinstance(path, str)
        cases.append(_Case(path=path, equals=when.get("equals"), count=_count(when.get("count"), "count"),
                           count_min=_count(when.get("count_min"), "count_min"), value=value,
                           value_from=_value_from(case.get("value_from")),
                           narrow=_narrow(when.get("narrow"))))
    return cases


def _same(a: JsonValue, b: JsonValue) -> bool:
    """Igualdad estricta: `True` no es `1`."""
    return isinstance(a, bool) == isinstance(b, bool) and a == b
