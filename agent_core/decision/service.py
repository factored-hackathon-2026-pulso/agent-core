"""`DecisionService`: cadena de proveedores, calibración, umbrales y evento `decision_made` (spec §3.1)."""

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal

from agent_core.decision.calibration.artifact import CalibrationArtifact, CalibrationSource
from agent_core.decision.schema import check_schema_supported, validate_output
from agent_core.decision.types import (
    WILDCARD_LABEL,
    DecisionConfigError,
    DecisionOutput,
    DecisionProvider,
    EventScope,
    FullViewProvider,
    ProviderError,
    ProviderTimeout,
    RawPrediction,
    value_label,
)
from agent_core.domain import (
    DecisionMade,
    DecisionMadePayload,
    DecisionModelDef,
    EntityRef,
    JsonValue,
    LabelScore,
    Locale,
    ProviderSpec,
)
from agent_core.ports import Clock, IdKind, IdSource, RegistryPort
from agent_core.views import TOKEN_PATTERN, TokenVault

_NS_PER_MS = 1_000_000
_ATTEMPTS_PER_PROVIDER = 2  # la llamada inicial y 1 reintento por salida fuera de esquema


@dataclass
class _Usage:
    calls: int = 0
    tokens: int = 0
    cost_usd: Decimal = field(default_factory=lambda: Decimal("0"))


_TOKEN = re.compile(TOKEN_PATTERN)


def _has_unknown_token(node: JsonValue, vault: TokenVault) -> bool:
    """Busca tokens en todos los strings de `node` (valores y claves) y verifica cada uno con el vault."""
    if isinstance(node, str):
        return any(not vault.exists(match.group(0)) for match in _TOKEN.finditer(node))
    if isinstance(node, list):
        return any(_has_unknown_token(item, vault) for item in node)
    if isinstance(node, dict):
        return any(_has_unknown_token(key, vault) or _has_unknown_token(item, vault)
                   for key, item in node.items())
    return False


class DecisionService:
    def __init__(self, registry: RegistryPort, providers: Mapping[str, DecisionProvider],
                 calibrations: CalibrationSource, clock: Clock, ids: IdSource) -> None:
        self._registry = registry
        self._providers = dict(providers)
        self._calibrations = calibrations
        self._clock = clock
        self._ids = ids

    def decide_output(self, model_ref: EntityRef, inputs_model_view: dict[str, JsonValue], locale: Locale,
                      token_vault: TokenVault, *,
                      inputs_full: dict[str, JsonValue] | None = None) -> DecisionOutput:
        """Corre la cadena y devuelve la salida (sin armar el evento)."""
        definition = self._registry.get(model_ref, DecisionModelDef)
        return self._decide_with(definition, definition.output_schema, inputs_model_view, locale, token_vault,
                                 inputs_full)

    def decide(self, model_ref: EntityRef, inputs_model_view: dict[str, JsonValue], locale: Locale,
               token_vault: TokenVault, *, scope: EventScope,
               inputs_full: dict[str, JsonValue] | None = None) -> tuple[DecisionOutput, DecisionMade]:
        """Corre la cadena y arma `decision_made` (vista audit: valores con tokens, nunca `full`).

        `inputs_full` solo llega a un proveedor `rule` con `compare_on: full` (ADR 0027): a ningún otro."""
        definition = self._registry.get(model_ref, DecisionModelDef)
        output = self._decide_with(definition, definition.output_schema, inputs_model_view, locale,
                                   token_vault, inputs_full)
        return output, self._event(model_ref, output, locale, scope)

    def decide_with_schema(self, model_ref: EntityRef, schema: dict[str, JsonValue],
                           inputs_model_view: dict[str, JsonValue], locale: Locale, token_vault: TokenVault,
                           *, scope: EventScope) -> tuple[DecisionOutput, DecisionMade]:
        """Como `decide` con un esquema efectivo (Understand lo arma por release); no muta el registro."""
        definition = self._registry.get(model_ref, DecisionModelDef)
        output = self._decide_with(definition, schema, inputs_model_view, locale, token_vault)
        return output, self._event(model_ref, output, locale, scope)

    def _event(self, model_ref: EntityRef, output: DecisionOutput, locale: Locale,
               scope: EventScope) -> DecisionMade:
        payload = DecisionMadePayload(
            decision_id=output.decision_id, model=model_ref, provider_used=output.provider_used,
            model_version=output.model_version, fallback_depth=output.fallback_depth, value=output.value,
            p_cal=dict(output.p_cal), p_raw=dict(output.p_raw),
            top_k={name: [LabelScore(label=label, p=p) for label, p in pairs]
                   for name, pairs in output.top_k.items()},
            above_threshold=output.above_threshold, latency_ms=output.latency_ms, tokens=output.tokens,
            cost_usd=output.cost_usd, locale=locale)
        return DecisionMade(event_id=self._ids.new_id(IdKind.event), run_id=scope.run_id,
                            turn_id=scope.turn_id, session_id=scope.session_id, release=scope.release,
                            ts=self._clock.now(), payload=payload)

    def _decide_with(self, definition: DecisionModelDef, schema: dict[str, JsonValue],
                     inputs_model_view: dict[str, JsonValue], locale: Locale,
                     token_vault: TokenVault,
                     inputs_full: dict[str, JsonValue] | None = None) -> DecisionOutput:
        """Igual que `decide_output` con un esquema efectivo (Understand lo arma por release)."""
        started = self._clock.monotonic_ns()
        self._check_inputs(definition, inputs_model_view)
        if inputs_full is not None:
            self._check_inputs(definition, inputs_full)
        check_schema_supported(schema)
        for spec in definition.providers:
            if spec.provider not in self._providers:
                raise DecisionConfigError(f"proveedor sin adaptador registrado: {spec.provider}")
        usage = _Usage()
        chosen: tuple[int, str, RawPrediction] | None = None
        for depth, spec in enumerate(definition.providers):
            provider = self._providers[spec.provider]
            raw = self._attempt(provider, spec, inputs_model_view, schema, locale, usage, inputs_full)
            if raw is not None:
                chosen = (depth, spec.provider, raw)
                break
        latency_ms = (self._clock.monotonic_ns() - started) // _NS_PER_MS
        decision_id = self._ids.new_id(IdKind.decision)
        fields = list(definition.calibrated_fields)
        if chosen is None:
            return DecisionOutput(
                value={}, p_cal=dict.fromkeys(fields), p_raw=dict.fromkeys(fields), top_k={},
                above_threshold=dict.fromkeys(fields, False), provider_used="none", model_version="none",
                fallback_depth=len(definition.providers), latency_ms=latency_ms, tokens=usage.tokens,
                cost_usd=usage.cost_usd, decision_id=decision_id, model_calls=usage.calls)
        depth, provider_used, raw = chosen
        p_raw = {name: raw.p_raw.get(name) for name in fields}
        top_k = {name: list(raw.top_k[name]) for name in fields if name in raw.top_k}
        p_cal, above = self._calibrate(definition, provider_used, locale, raw.value, p_raw)
        if _has_unknown_token(raw.value, token_vault):
            above = dict.fromkeys(fields, False)  # salida inválida; `p_cal` se conserva para diagnóstico
        return DecisionOutput(
            value=raw.value, p_cal=p_cal, p_raw=p_raw, top_k=top_k,
            above_threshold=above, provider_used=provider_used,
            model_version=raw.model_version, fallback_depth=depth, latency_ms=latency_ms,
            tokens=usage.tokens, cost_usd=usage.cost_usd, decision_id=decision_id, model_calls=usage.calls)

    def _calibrate(self, definition: DecisionModelDef, provider: str, locale: Locale,
                   value: dict[str, JsonValue], p_raw: dict[str, float | None]
                   ) -> tuple[dict[str, float | None], dict[str, bool]]:
        """`p_cal` y `above_threshold` por campo calibrado (spec §3.1 pasos 3 y 4)."""
        calibration = definition.calibration
        cal_art = self._calibrations.get(calibration.run) if calibration.run else None
        thr_art = self._calibrations.get(definition.thresholds_from) if definition.thresholds_from else None
        p_cal: dict[str, float | None] = {}
        above: dict[str, bool] = {}
        for name in definition.calibrated_fields:
            raw_p = p_raw[name]
            calibrator = cal_art.calibrator(name, provider, locale) if cal_art else None
            if raw_p is None:
                p = None
            elif calibrator is not None:
                p = calibrator.apply(raw_p)
            else:
                p = raw_p if calibration.method == "none" else None
            p_cal[name] = p
            above[name] = self._passes(thr_art, name, value, provider, locale, p)
        return p_cal, above

    @staticmethod
    def _passes(artifact: CalibrationArtifact | None, name: str, value: dict[str, JsonValue], provider: str,
                locale: Locale, p_cal: float | None) -> bool:
        # Combinación ausente = umbral 1.0 y nunca pasa (spec §5: sin calibración, siempre low_confidence),
        # ni siquiera con p_cal == 1.0.
        if p_cal is None or artifact is None or name not in value:
            return False
        item = value[name]
        threshold = artifact.thresholds.get((name, value_label(item), provider, locale))
        if threshold is None:  # a specific label wins; otherwise the "*" wildcard (runtime choices)
            threshold = artifact.thresholds.get((name, WILDCARD_LABEL, provider, locale))
        if threshold is None:
            return False
        return p_cal >= threshold

    @staticmethod
    def _check_inputs(definition: DecisionModelDef, inputs: dict[str, JsonValue]) -> None:
        if definition.input_view and not set(inputs) <= set(definition.input_view):
            raise DecisionConfigError("la entrada trae claves fuera de input_view")

    @staticmethod
    def _attempt(provider: DecisionProvider, spec: ProviderSpec, inputs: dict[str, JsonValue],
                 schema: dict[str, JsonValue], locale: Locale, usage: _Usage,
                 inputs_full: dict[str, JsonValue] | None = None) -> RawPrediction | None:
        """Una salida válida del proveedor o `None` (timeout, error o fuera de esquema dos veces)."""
        # ADR 0027: la vista `full` solo va a `rule` con `compare_on: full`; jamás a JEV, classifier ni LLM.
        full_provider = (provider if inputs_full is not None and spec.compares_on_full_view
                         and isinstance(provider, FullViewProvider) else None)
        for _ in range(_ATTEMPTS_PER_PROVIDER):
            usage.calls += 1
            try:
                if full_provider is not None and inputs_full is not None:
                    raw = full_provider.predict_full(spec, inputs, inputs_full, schema, locale)
                else:
                    raw = provider.predict(spec, inputs, schema, locale)
            except ProviderTimeout:
                return None
            except ProviderError as exc:
                usage.tokens += exc.tokens
                usage.cost_usd += exc.cost_usd
                return None
            usage.tokens += raw.tokens
            usage.cost_usd += raw.cost_usd
            if not validate_output(raw.value, schema):
                return raw
        return None
