"""Proveedor `rule` (spec §3.3): decisiones triviales con p ∈ {0, 1}. Propuesta P7, confirmada.

    config = {"cases": [{"when": {"path": "<clave de la entrada>", "equals": <json>}, "value": {...}}],
              "default": {...}}

El primer caso que coincide gana (`p_raw = 1.0` por campo); sin caso, `default` con `p_raw = 0.0`; sin
`default`, `ProviderError`. Sin JSON Logic (M2 lo tiene y no se puede importar)."""

from agent_core.decision.types import DecisionConfigError, ProviderError, RawPrediction
from agent_core.domain import JsonValue, Locale, ProviderSpec


class RuleProvider:
    name = "rule"

    def predict(self, spec: ProviderSpec, inputs_model_view: dict[str, JsonValue],
                schema: dict[str, JsonValue], locale: Locale) -> RawPrediction:
        cases = _cases(spec.config)
        default = spec.config.get("default")
        if default is not None and not isinstance(default, dict):
            raise DecisionConfigError("rule: config.default debe ser un objeto")
        for path, equals, value in cases:
            if path in inputs_model_view and _same(inputs_model_view[path], equals):
                return RawPrediction(value=value, p_raw=dict.fromkeys(value, 1.0), model_version="rule-1")
        if default is None:
            raise ProviderError("rule: ningún caso coincide y no hay default")
        return RawPrediction(value=default, p_raw=dict.fromkeys(default, 0.0), model_version="rule-1")


def _cases(config: dict[str, JsonValue]) -> list[tuple[str, JsonValue, dict[str, JsonValue]]]:
    raw = config.get("cases")
    if not isinstance(raw, list):
        raise DecisionConfigError("rule: config.cases debe ser una lista")
    cases: list[tuple[str, JsonValue, dict[str, JsonValue]]] = []
    for case in raw:
        when = case.get("when") if isinstance(case, dict) else None
        value = case.get("value") if isinstance(case, dict) else None
        if (not isinstance(when, dict) or not isinstance(when.get("path"), str) or "equals" not in when
                or not isinstance(value, dict)):
            raise DecisionConfigError("rule: cada caso necesita when.path, when.equals y value (objeto)")
        path = when["path"]
        assert isinstance(path, str)
        cases.append((path, when["equals"], value))
    return cases


def _same(a: JsonValue, b: JsonValue) -> bool:
    """Igualdad estricta: `True` no es `1`."""
    return isinstance(a, bool) == isinstance(b, bool) and a == b
