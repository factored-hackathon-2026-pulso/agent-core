"""Informe de métricas por idioma y proveedor (spec §3.4.5 y §8), en markdown o JSON determinista.

Calibración: sale de `CalibrationArtifact.metrics` (PT marcado sintético). Runtime: latencia p50/p95, costo
total y tasa de respaldo de una secuencia de `decision_made`. No incluye textos de entrada."""

from collections import defaultdict
from collections.abc import Sequence
from decimal import Decimal

from agent_core.decision.calibration.artifact import CalibrationArtifact
from agent_core.decision.calibration.metrics import percentile
from agent_core.domain import DecisionMade, JsonValue, canonical_bytes, loads

_COLUMNS = (("n", "n"), ("ece", "ECE"), ("macro_f1", "macro-F1"),
            ("precision_at_threshold", "precision@thr"), ("coverage", "coverage"),
            ("recall_at_threshold", "recall@thr"))


def load_events(text: str) -> list[DecisionMade]:
    """Eventos `decision_made` de un JSONL (solo con `loads`)."""
    return [DecisionMade.model_validate(loads(line)) for line in text.splitlines() if line.strip()]


def runtime_metrics(events: Sequence[DecisionMade]) -> dict[str, JsonValue]:
    """`{idioma: {proveedor: {...}}}` con latencias por rango más cercano y tasa de respaldo."""
    groups: dict[tuple[str, str], list[DecisionMade]] = defaultdict(list)
    for event in events:
        groups[(event.payload.locale, event.payload.provider_used)].append(event)
    result: dict[str, JsonValue] = {}
    for (lang, provider), items in sorted(groups.items()):
        latencies = [e.payload.latency_ms for e in items]
        cost = sum((e.payload.cost_usd for e in items), Decimal("0"))
        fallback = sum(e.payload.fallback_depth > 0 for e in items) / len(items)
        by_provider = result.setdefault(lang, {})
        assert isinstance(by_provider, dict)
        by_provider[provider] = {
            "decisions": len(items), "latency_p50_ms": percentile(latencies, 50),
            "latency_p95_ms": percentile(latencies, 95), "cost_usd": format(cost, "f"),
            "fallback_rate": format(Decimal(repr(fallback)).quantize(Decimal("0.000001")), "f"),
        }
    return result


def build_report(artifact: CalibrationArtifact, events: Sequence[DecisionMade] | None = None
                 ) -> dict[str, JsonValue]:
    report: dict[str, JsonValue] = {
        "run_id": artifact.run_id, "split_hash": artifact.split_hash, "method": artifact.method,
        "limitations": list(artifact.limitations),
        "calibration": artifact.metrics.get("languages", {}),
    }
    if events is not None:
        report["runtime"] = runtime_metrics(events)
    return report


def render_json(report: dict[str, JsonValue]) -> str:
    """JSON canónico (JCS): claves ordenadas, idéntico entre ejecuciones."""
    return canonical_bytes(report).decode("utf-8")


def _cell(value: JsonValue) -> str:
    return "-" if value is None else str(value)


def render_markdown(report: dict[str, JsonValue]) -> str:
    lines = [f"# Informe de calibración `{report['run_id']}`", "",
             f"- método: {report['method']}", f"- split_hash: `{report['split_hash']}`"]
    limitations = report.get("limitations")
    if isinstance(limitations, list):
        lines += [f"- limitación: {item}" for item in limitations]
    calibration = report.get("calibration")
    for lang, info in sorted(calibration.items()) if isinstance(calibration, dict) else []:
        if not isinstance(info, dict):
            continue
        suffix = " (synthetic)" if info.get("synthetic") else ""
        lines += ["", f"## {lang}{suffix}", "", f"Muestra: {info.get('samples')}"]
        providers = info.get("providers")
        for provider, fields in sorted(providers.items()) if isinstance(providers, dict) else []:
            if not isinstance(fields, dict):
                continue
            lines += ["", f"### {provider}", "",
                      "| campo | " + " | ".join(label for _, label in _COLUMNS) + " |",
                      "|---|" + "---|" * len(_COLUMNS)]
            for name, entry in sorted(fields.items()):
                if isinstance(entry, dict):
                    cells = " | ".join(_cell(entry.get(key)) for key, _ in _COLUMNS)
                    lines.append(f"| {name} | {cells} |")
    runtime = report.get("runtime")
    if isinstance(runtime, dict):
        lines += ["", "## Runtime", "", "| idioma | proveedor | decisiones | latencia p50 (ms) | "
                  "latencia p95 (ms) | costo USD | tasa de respaldo |", "|---|---|---|---|---|---|---|"]
        for lang, by_provider in sorted(runtime.items()):
            for provider, row in sorted(by_provider.items()) if isinstance(by_provider, dict) else []:
                if isinstance(row, dict):
                    lines.append(f"| {lang} | {provider} | {_cell(row['decisions'])} | "
                                 f"{_cell(row['latency_p50_ms'])} | {_cell(row['latency_p95_ms'])} | "
                                 f"{_cell(row['cost_usd'])} | {_cell(row['fallback_rate'])} |")
    return "\n".join(lines) + "\n"
