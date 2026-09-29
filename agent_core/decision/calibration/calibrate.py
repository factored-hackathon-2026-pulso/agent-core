"""`calibrate`: corre los proveedores sobre el split de desarrollo y produce el `CalibrationArtifact` (§3.4).

Función pura del contenido: sin reloj ni `IdSource`; el mismo split (en cualquier orden) y las mismas salidas
de proveedor dan el mismo artefacto, con `split_hash` y `run_id` derivados por hash."""

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal

from agent_core.decision.calibration.artifact import CalibrationArtifact, IsotonicMap, Target
from agent_core.decision.calibration.isotonic import fit_isotonic
from agent_core.decision.calibration.metrics import (
    Scored,
    coverage,
    ece,
    macro_f1,
    precision_at_threshold,
    recall_at_threshold,
)
from agent_core.decision.calibration.thresholds import Prediction, choose_threshold
from agent_core.decision.schema import check_schema_supported, validate_output
from agent_core.decision.types import (
    DecisionConfigError,
    DecisionProvider,
    ProviderError,
    ProviderTimeout,
    value_label,
)
from agent_core.domain import DecisionModelDef, JsonValue, Locale, ProviderSpec, canonical_bytes, sha256_hex

BASE_LANG = "es"


@dataclass(frozen=True)
class DevExample:
    """Ejemplo etiquetado del split (entrada en vista `model`). PT: `synthetic=True` (ADR 0012)."""
    id: str
    inputs: dict[str, JsonValue]
    labels: dict[str, str]
    lang: Locale
    synthetic: bool = False


@dataclass(frozen=True)
class _Row:
    id: str
    truth: str
    p_raw: float | None
    predicted: str | None


def calibrate(model_def: DecisionModelDef, dev_split: Sequence[DevExample],
              providers: Mapping[str, DecisionProvider], *, targets: Mapping[str, Target],
              min_samples: Mapping[str, int], min_support: int) -> CalibrationArtifact:
    method = model_def.calibration.method
    if method not in ("none", "isotonic"):
        raise DecisionConfigError(f"método de calibración no implementado: {method}")
    fields = list(model_def.calibrated_fields)
    for name in fields:
        if name not in targets:
            raise ValueError(f"falta target para el campo calibrado {name!r}")
    examples = sorted(dev_split, key=lambda e: e.id)
    if len({e.id for e in examples}) != len(examples):
        raise ValueError("ids repetidos en el split de desarrollo")
    if not any(e.lang == BASE_LANG for e in examples):
        raise ValueError(f"el idioma base {BASE_LANG!r} no tiene ejemplos")
    langs = sorted({e.lang for e in examples} | set(min_samples))
    for lang in langs:
        if lang not in min_samples:
            raise ValueError(f"min_samples no trae valor para el idioma {lang!r}")
    specs = list(dict.fromkeys(spec.provider for spec in model_def.providers))
    for provider in specs:
        if provider not in providers:
            raise DecisionConfigError(f"proveedor sin adaptador registrado: {provider}")
    check_schema_supported(model_def.output_schema)

    all_rows: dict[tuple[str, str, str], list[_Row]] = {}
    calibrators: dict[tuple[str, str, str], IsotonicMap] = {}
    thresholds: dict[tuple[str, str, str, str], float] = {}
    for spec in model_def.providers:
        if spec.provider not in specs:
            continue
        specs.remove(spec.provider)
        for lang in langs:
            batch = [e for e in examples if e.lang == lang]
            if not batch:
                continue
            rows = _run(providers[spec.provider], spec, model_def, batch, lang, fields)
            for name in fields:
                all_rows[(name, spec.provider, lang)] = rows[name]
                _fit_field(name, spec.provider, lang, rows[name], method, targets[name], min_support,
                           calibrators, thresholds)

    limitations: list[str] = []
    for lang in langs:
        if lang == BASE_LANG or sum(e.lang == lang for e in examples) >= min_samples[lang]:
            continue
        for key in [k for k in calibrators if k[2] == lang]:
            del calibrators[key]
        for tkey in [k for k in thresholds if k[3] == lang]:
            del thresholds[tkey]
        calibrators.update({(f, p, lang): m for (f, p, lg), m in calibrators.items() if lg == BASE_LANG})
        thresholds.update({(f, v, p, lang): t for (f, v, p, lg), t in thresholds.items() if lg == BASE_LANG})
        limitations.append(f"{lang}: calibración copiada de {BASE_LANG} (muestra < mínimo)")

    split_hash = sha256_hex(canonical_bytes([
        {"id": e.id, "inputs": e.inputs, "labels": e.labels, "lang": e.lang, "synthetic": e.synthetic}
        for e in examples]))
    run_id = "cal-" + sha256_hex(canonical_bytes({
        "model": f"{model_def.id}@{model_def.version}", "split_hash": split_hash, "method": method,
        "providers": sorted(spec.provider for spec in model_def.providers),
        "targets": {name: {"metric": targets[name].metric, "value": targets[name].value} for name in fields},
        "min_samples": dict(min_samples), "min_support": min_support,
    }))[:16]
    artifact = CalibrationArtifact(
        run_id=run_id, split_hash=split_hash, method=method, calibrators=calibrators, thresholds=thresholds,
        target={name: targets[name] for name in fields}, limitations=limitations)
    synthetic = {lang: any(e.synthetic for e in examples if e.lang == lang) for lang in langs}
    samples = {lang: sum(e.lang == lang for e in examples) for lang in langs}
    return artifact.model_copy(update={"metrics": _metrics(artifact, all_rows, synthetic, samples)})


def _fmt(value: float | None) -> str | None:
    """Métricas como string con 6 decimales: JSON canónico estable (M0 no admite `float` en `JsonValue`)."""
    return None if value is None else format(Decimal(repr(value)).quantize(Decimal("0.000001")), "f")


def _metrics(artifact: CalibrationArtifact, all_rows: dict[tuple[str, str, str], list[_Row]],
             synthetic: dict[str, bool], samples: dict[str, int]) -> dict[str, JsonValue]:
    """ECE, macro-F1, precisión al umbral y cobertura (y recall al umbral si el objetivo es recall), por
    idioma y proveedor, evaluados con las calibraciones y umbrales finales del artefacto."""
    languages: dict[str, JsonValue] = {}
    for lang in sorted(synthetic):
        providers: dict[str, JsonValue] = {}
        for (name, provider, row_lang), rows in sorted(all_rows.items(), key=lambda item: item[0]):
            if row_lang != lang:
                continue
            calibrator = artifact.calibrator(name, provider, lang)
            scored: list[Scored] = []
            for r in rows:
                p_cal = None if r.p_raw is None else (calibrator.apply(r.p_raw) if calibrator else (
                    r.p_raw if artifact.method == "none" else None))
                threshold = (artifact.thresholds.get((name, r.predicted, provider, lang))
                             if r.predicted is not None else None)
                scored.append(Scored(r.truth, r.predicted, p_cal,
                                     p_cal is not None and threshold is not None and p_cal >= threshold))
            entry: dict[str, JsonValue] = {
                "n": len(rows),
                "ece": _fmt(ece([(s.p_cal, s.predicted == s.truth) for s in scored
                                 if s.p_cal is not None and s.predicted is not None])),
                "macro_f1": _fmt(macro_f1([s.truth for s in scored], [s.predicted for s in scored])),
                "precision_at_threshold": _fmt(precision_at_threshold(scored)),
                "coverage": _fmt(coverage(scored)),
            }
            if artifact.target[name].metric == "recall":
                entry["recall_at_threshold"] = _fmt(recall_at_threshold(scored))
            fields_for = providers.setdefault(provider, {})
            assert isinstance(fields_for, dict)
            fields_for[name] = entry
        languages[lang] = {"synthetic": synthetic[lang], "samples": samples[lang], "providers": providers}
    return {"languages": languages}


def _run(provider: DecisionProvider, spec: ProviderSpec, model_def: DecisionModelDef, batch: list[DevExample],
         lang: Locale, fields: list[str]) -> dict[str, list[_Row]]:
    """Corre el proveedor sobre `batch`; una falla o una salida fuera de esquema no tiene predicción."""
    rows: dict[str, list[_Row]] = {name: [] for name in fields}
    for example in batch:
        raw = None
        try:
            candidate = provider.predict(spec, example.inputs, model_def.output_schema, lang)
            if not validate_output(candidate.value, model_def.output_schema):
                raw = candidate
        except (ProviderTimeout, ProviderError):
            raw = None
        for name in fields:
            if name not in example.labels:
                continue
            predicted = None if raw is None or name not in raw.value else value_label(raw.value[name])
            p = None if raw is None else raw.p_raw.get(name)
            valid = p is not None and math.isfinite(p) and 0.0 <= p <= 1.0
            rows[name].append(_Row(example.id, example.labels[name], p if valid else None, predicted))
    return rows


def _fit_field(name: str, provider: str, lang: Locale, rows: list[_Row], method: str, target: Target,
               min_support: int, calibrators: dict[tuple[str, str, str], IsotonicMap],
               thresholds: dict[tuple[str, str, str, str], float]) -> None:
    calibrator: IsotonicMap | None = None
    if method == "isotonic":
        calibrator = fit_isotonic([(r.p_raw, float(r.predicted == r.truth), r.id)
                                   for r in rows if r.p_raw is not None and r.predicted is not None])
        if calibrator is None:
            return
        calibrators[(name, provider, lang)] = calibrator
    preds = []
    for r in rows:
        p_cal = None if r.p_raw is None else (calibrator.apply(r.p_raw) if calibrator else r.p_raw)
        preds.append(Prediction(id=r.id, truth=r.truth, predicted=r.predicted, p_cal=p_cal))
    values = sorted({p.truth for p in preds} | {p.predicted for p in preds if p.predicted is not None})
    for value in values:
        threshold = choose_threshold(preds, value, target, min_support)
        if threshold is not None:
            thresholds[(name, value, provider, lang)] = threshold
