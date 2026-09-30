"""Proveedores de decisión "grabados" para el replay `fixture` (M11 §3.4): responden desde `decision_made`.

Las salidas no deterministas de M5 (JEV y clasificadores) se leen del registro y `DecisionService` real las
vuelve a calibrar. Como el fixture no lleva el artefacto de calibración, se sintetiza uno con los umbrales
mínimos que reproducen los `above_threshold` grabados."""

from collections import deque

from agent_core.audit import ReplayDesync
from agent_core.decision import RawPrediction
from agent_core.decision.calibration.artifact import CalibrationArtifact, Target
from agent_core.decision.types import value_label
from agent_core.domain import DecisionMade, EngineEvent, JsonValue, Locale, ProviderSpec


class RecordedProvider:
    """`DecisionProvider` que sirve, en orden, las salidas grabadas de un proveedor."""

    def __init__(self, name: str, events: list[DecisionMade]) -> None:
        self.name = name
        self._queue = deque(events)

    def predict(self, spec: ProviderSpec, inputs_model_view: dict[str, JsonValue],
                schema: dict[str, JsonValue], locale: Locale) -> RawPrediction:
        if not self._queue:
            raise ReplayDesync(None, None, {"provider": self.name})
        payload = self._queue.popleft().payload
        return RawPrediction(
            value=dict(payload.value), p_raw=dict(payload.p_raw),
            top_k={name: [(item.label, item.p) for item in items] for name, items in payload.top_k.items()},
            latency_ms=payload.latency_ms, tokens=payload.tokens, cost_usd=payload.cost_usd,
            model_version=payload.model_version)


def decisions_of(events: list[EngineEvent]) -> list[DecisionMade]:
    return [e for e in events if isinstance(e, DecisionMade)]


def providers_from(decisions: list[DecisionMade]) -> dict[str, RecordedProvider]:
    names = list(dict.fromkeys(d.payload.provider_used for d in decisions))
    return {name: RecordedProvider(name, [d for d in decisions if d.payload.provider_used == name])
            for name in names}


def synthesized_thresholds(decisions: list[DecisionMade], run_id: str) -> CalibrationArtifact:
    """Un umbral igual a `p_cal` por cada campo que superó el suyo; sin clave = nunca pasa (spec M5 §5)."""
    thresholds: dict[tuple[str, str, str, str], float] = {}
    for decision in decisions:
        payload = decision.payload
        for name, above in payload.above_threshold.items():
            p = payload.p_cal.get(name)
            if not above or p is None or name not in payload.value:
                continue
            key = (name, value_label(payload.value[name]), payload.provider_used, payload.locale)
            thresholds[key] = min(p, thresholds.get(key, p))
    return CalibrationArtifact(run_id=run_id, split_hash="b" * 64, method="isotonic", calibrators={},
                               thresholds=thresholds,
                               target={"command": Target(metric="precision", value=0.9)})
