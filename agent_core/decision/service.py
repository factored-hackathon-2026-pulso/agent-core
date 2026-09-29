"""`DecisionService`: cadena de proveedores, calibración, umbrales y evento `decision_made` (spec §3.1)."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal

from agent_core.decision.calibration.artifact import CalibrationSource
from agent_core.decision.schema import check_schema_supported, validate_output
from agent_core.decision.types import (
    DecisionConfigError,
    DecisionOutput,
    DecisionProvider,
    ProviderError,
    ProviderTimeout,
    RawPrediction,
)
from agent_core.domain import DecisionModelDef, EntityRef, JsonValue, Locale, ProviderSpec
from agent_core.ports import Clock, IdKind, IdSource, RegistryPort
from agent_core.views import TokenVault

_NS_PER_MS = 1_000_000
_ATTEMPTS_PER_PROVIDER = 2  # la llamada inicial y 1 reintento por salida fuera de esquema


@dataclass
class _Usage:
    calls: int = 0
    tokens: int = 0
    cost_usd: Decimal = field(default_factory=lambda: Decimal("0"))


class DecisionService:
    def __init__(self, registry: RegistryPort, providers: Mapping[str, DecisionProvider],
                 calibrations: CalibrationSource, clock: Clock, ids: IdSource) -> None:
        self._registry = registry
        self._providers = dict(providers)
        self._calibrations = calibrations
        self._clock = clock
        self._ids = ids

    def decide_output(self, model_ref: EntityRef, inputs_model_view: dict[str, JsonValue], locale: Locale,
                      token_vault: TokenVault) -> DecisionOutput:
        """Corre la cadena y devuelve la salida (sin armar el evento)."""
        definition = self._registry.get(model_ref, DecisionModelDef)
        return self._decide_with(definition, definition.output_schema, inputs_model_view, locale, token_vault)

    def _decide_with(self, definition: DecisionModelDef, schema: dict[str, JsonValue],
                     inputs_model_view: dict[str, JsonValue], locale: Locale,
                     token_vault: TokenVault) -> DecisionOutput:
        """Igual que `decide_output` con un esquema efectivo (Understand lo arma por release)."""
        started = self._clock.monotonic_ns()
        self._check_inputs(definition, inputs_model_view)
        check_schema_supported(schema)
        for spec in definition.providers:
            if spec.provider not in self._providers:
                raise DecisionConfigError(f"proveedor sin adaptador registrado: {spec.provider}")
        usage = _Usage()
        chosen: tuple[int, str, RawPrediction] | None = None
        for depth, spec in enumerate(definition.providers):
            provider = self._providers[spec.provider]
            raw = self._attempt(provider, spec, inputs_model_view, schema, locale, usage)
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
        p_cal = {name: p_raw[name] if definition.calibration.method == "none" else None for name in fields}
        return DecisionOutput(
            value=raw.value, p_cal=p_cal, p_raw=p_raw, top_k=top_k,
            above_threshold=dict.fromkeys(fields, False), provider_used=provider_used,
            model_version=raw.model_version, fallback_depth=depth, latency_ms=latency_ms,
            tokens=usage.tokens, cost_usd=usage.cost_usd, decision_id=decision_id, model_calls=usage.calls)

    @staticmethod
    def _check_inputs(definition: DecisionModelDef, inputs: dict[str, JsonValue]) -> None:
        if definition.input_view and not set(inputs) <= set(definition.input_view):
            raise DecisionConfigError("la entrada trae claves fuera de input_view")

    @staticmethod
    def _attempt(provider: DecisionProvider, spec: ProviderSpec, inputs: dict[str, JsonValue],
                 schema: dict[str, JsonValue], locale: Locale, usage: _Usage) -> RawPrediction | None:
        """Una salida válida del proveedor o `None` (timeout, error o fuera de esquema dos veces)."""
        for _ in range(_ATTEMPTS_PER_PROVIDER):
            usage.calls += 1
            try:
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
