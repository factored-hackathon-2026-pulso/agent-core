"""Calibra `understand-turno` sobre las salidas crudas de JEV ya recolectadas y mide el artefacto en `test`.

    uv run python -m testing.calibration.calibrate_understand_turno --raw RAW.jsonl --out-dir DIR

No usa red: reproduce las respuestas de `collect_jev` con un proveedor grabado. Escribe `DIR/<run_id>.json`
(el artefacto, listo para `DirectoryCalibrationSource`) y `DIR/understand_turno_report.md`. El conjunto
`test` no interviene en `calibrate`: solo mide el artefacto ya hecho.

El artefacto lleva en `limitations` que se calibró con mensajes sintéticos: los umbrales son de
demostración."""

import argparse
import json
import sys
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

from agent_core.decision import DecisionProvider, ProviderError, RawPrediction
from agent_core.decision.calibration import CalibrationArtifact, DevExample, Target, calibrate
from agent_core.domain import (
    CalibrationRef,
    Command,
    DecisionModelDef,
    JsonValue,
    Locale,
    ProviderSpec,
    canonical_bytes,
    sha256_hex,
)
from testing.calibration.collect_jev import INTERRUPTS, provider_spec, runtime_schema
from testing.calibration.understand_turno import FLOWS, examples

PROVIDER = "jev"
FIELDS = ("command", "flow", "interrupt")
SYNTHETIC_NOTE = ("calibrado con mensajes sintéticos escritos para esto (testing/calibration), no con "
                  "tráfico real: los umbrales son de demostración y hay que recalibrar con turnos reales")
INTERRUPT_NOTE = ("interrupt no es informativo: su enum tiene un solo valor (fraude), JEV responde siempre "
                  "fraude con p_raw 1.0 y el recall sale 1.0 por construcción; la decisión real la toma "
                  "command=interrupt")


def _key(inputs: dict[str, JsonValue]) -> str:
    return sha256_hex(canonical_bytes(inputs))


class RecordedJev:
    """`DecisionProvider` que reproduce las respuestas grabadas, por contenido de la entrada."""

    name = PROVIDER

    def __init__(self, rows: dict[str, dict[str, object]]) -> None:
        self._rows = rows

    def predict(self, spec: ProviderSpec, inputs_model_view: dict[str, JsonValue],
                schema: dict[str, JsonValue], locale: Locale) -> RawPrediction:
        row = self._rows.get(_key(inputs_model_view))
        if row is None or "error" in row:
            raise ProviderError("sin respuesta grabada")
        value, p_raw, top_k = row["value"], row["p_raw"], row["top_k"]
        assert isinstance(value, dict) and isinstance(p_raw, dict) and isinstance(top_k, dict)
        return RawPrediction(
            value=value, p_raw={k: None if v is None else float(v) for k, v in p_raw.items()},
            top_k={k: [(label, float(p)) for label, p in v] for k, v in top_k.items()},
            model_version=str(row["model_version"]))


def load_rows(raw: Path, wanted: list[DevExample]) -> dict[str, dict[str, object]]:
    by_id = {json.loads(line)["id"]: json.loads(line) for line in raw.read_text(encoding="utf-8").splitlines()
             if line.strip()}
    missing = [e.id for e in wanted if e.id not in by_id]
    if missing:
        raise SystemExit(f"faltan {len(missing)} respuestas en {raw.name}: corre collect_jev de nuevo")
    return {_key(e.inputs): by_id[e.id] for e in wanted}


def model_def() -> DecisionModelDef:
    return DecisionModelDef(
        id="understand-turno", version="1.0.0", output_schema=runtime_schema(list(FLOWS), INTERRUPTS),
        calibrated_fields=list(FIELDS), providers=[provider_spec()],
        calibration=CalibrationRef(method="isotonic"))


def evaluate(artifact: CalibrationArtifact, provider: DecisionProvider, spec: ProviderSpec,
             schema: dict[str, JsonValue], split: list[DevExample]) -> dict[tuple[str, str], dict[str, int]]:
    """Por `(idioma, campo)`: cuántos hay, cuántos pasan el umbral, cuántos pasan y aciertan, y aciertos."""
    out: dict[tuple[str, str], dict[str, int]] = defaultdict(
        lambda: {"n": 0, "accepted": 0, "accepted_correct": 0, "correct": 0})
    for example in split:
        try:
            raw = provider.predict(spec, example.inputs, schema, example.lang)
        except ProviderError:
            raw = None
        for field in FIELDS:
            if field not in example.labels:
                continue
            cell = out[(example.lang, field)]
            cell["n"] += 1
            predicted = None if raw is None else raw.value.get(field)
            p = None if raw is None else raw.p_raw.get(field)
            if predicted is None or p is None:
                continue
            correct = predicted == example.labels[field]
            cell["correct"] += correct
            calibrator = artifact.calibrator(field, PROVIDER, example.lang)
            p_cal = calibrator.apply(p) if calibrator else p
            if p_cal >= artifact.threshold(field, str(predicted), PROVIDER, example.lang):
                cell["accepted"] += 1
                cell["accepted_correct"] += correct
    return out


def per_command(artifact: CalibrationArtifact, provider: DecisionProvider, spec: ProviderSpec,
                schema: dict[str, JsonValue], split: list[DevExample]) -> dict[str, dict[str, int]]:
    """Solo español, campo `command`, por valor verdadero: n, aceptados y aceptados correctos."""
    out: dict[str, dict[str, int]] = defaultdict(lambda: {"n": 0, "accepted": 0, "accepted_correct": 0})
    for example in (e for e in split if e.lang == "es"):
        truth = example.labels["command"]
        out[truth]["n"] += 1
        try:
            raw = provider.predict(spec, example.inputs, schema, example.lang)
        except ProviderError:
            continue
        predicted, p = raw.value.get("command"), raw.p_raw.get("command")
        if predicted is None or p is None:
            continue
        calibrator = artifact.calibrator("command", PROVIDER, "es")
        p_cal = calibrator.apply(p) if calibrator else p
        if p_cal >= artifact.threshold("command", str(predicted), PROVIDER, "es"):
            out[truth]["accepted"] += 1
            out[truth]["accepted_correct"] += predicted == truth
    return out


def _pct(part: int, whole: int) -> str:
    return "—" if whole == 0 else f"{Decimal(part) * 100 / Decimal(whole):.1f} %"


def render(artifact: CalibrationArtifact, cells: dict[tuple[str, str], dict[str, int]],
           commands: dict[str, dict[str, int]], dev_counts: dict[str, int],
           test_counts: dict[str, int]) -> str:
    lines = [
        "# Calibración de `understand-turno` (sintética)", "",
        f"- Artefacto: `{artifact.run_id}` · método `{artifact.method}` · "
        f"`split_hash` `{artifact.split_hash[:12]}…`",
        f"- Proveedor `{PROVIDER}`, modelo fijado en la definición. Muestras dev: {dev_counts}. "
        f"Muestras test: {test_counts}.",
        "- Objetivos: " + ", ".join(f"`{k}` {t.metric} ≥ {t.value}" for k, t in artifact.target.items()), "",
        "## Límites", ""]
    lines += [f"- {text}" for text in artifact.limitations]
    lines += ["", "## Medición en el conjunto cerrado (`test`)", "",
              "Aceptado = pasa el umbral calibrado. Precisión = aciertos entre los aceptados. "
              "Cobertura = aceptados entre todos.", "",
              "| Idioma | Campo | n | Cobertura | Precisión | Exactitud sin umbral |",
              "|---|---|---|---|---|---|"]
    for (lang, field), c in sorted(cells.items()):
        lines.append(f"| {lang} | {field} | {c['n']} | {_pct(c['accepted'], c['n'])} | "
                     f"{_pct(c['accepted_correct'], c['accepted'])} | {_pct(c['correct'], c['n'])} |")
    lines += ["", "## `command` por valor verdadero (es, `test`)", "",
              "| Valor | n | Cobertura | Precisión |", "|---|---|---|---|"]
    for value, c in sorted(commands.items()):
        lines.append(f"| {value} | {c['n']} | {_pct(c['accepted'], c['n'])} | "
                     f"{_pct(c['accepted_correct'], c['accepted'])} |")
    lines += ["", "## Umbrales (`command`, es)", "", "| Valor | Umbral |", "|---|---|"]
    for (name, value, _provider, lang), threshold in sorted(artifact.thresholds.items()):
        if name == "command" and lang == "es":
            lines.append(f"| {value} | {threshold:.3f} |")
    omitted = sorted({c.value for c in Command} - {v for (n, v, _p, lg) in artifact.thresholds
                                                          if n == "command" and lg == "es"})
    listed = ", ".join(f"`{v}`" for v in omitted) or "ninguno"
    lines += ["", f"Sin umbral (quedan en 1.0: nunca pasan): {listed}", ""]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m testing.calibration.calibrate_understand_turno")
    parser.add_argument("--raw", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--target-command", type=Decimal, default=Decimal("0.95"))
    parser.add_argument("--target-flow", type=Decimal, default=Decimal("0.95"))
    parser.add_argument("--target-interrupt", type=Decimal, default=Decimal("0.90"))
    parser.add_argument("--min-support", type=int, default=10)
    parser.add_argument("--min-samples-pt", type=int, default=200)
    args = parser.parse_args(argv)

    dev, test = examples("dev"), examples("test")
    provider = RecordedJev(load_rows(args.raw, [*dev, *test]))
    definition, spec, schema = model_def(), provider_spec(), runtime_schema(list(FLOWS), INTERRUPTS)
    artifact = calibrate(
        definition, dev, {PROVIDER: provider},
        targets={"command": Target(metric="precision", value=float(args.target_command)),
                 "flow": Target(metric="precision", value=float(args.target_flow)),
                 "interrupt": Target(metric="recall", value=float(args.target_interrupt))},
        min_samples={"es": 1, "pt": args.min_samples_pt}, min_support=args.min_support)
    artifact = artifact.model_copy(
        update={"limitations": [SYNTHETIC_NOTE, INTERRUPT_NOTE, *artifact.limitations]})

    args.out_dir.mkdir(parents=True, exist_ok=True)
    (args.out_dir / f"{artifact.run_id}.json").write_text(artifact.to_json(), encoding="utf-8")
    cells = evaluate(artifact, provider, spec, schema, test)
    commands = per_command(artifact, provider, spec, schema, test)
    count = {split: {lang: sum(e.lang == lang for e in rows) for lang in ("es", "pt")}
             for split, rows in (("dev", dev), ("test", test))}
    (args.out_dir / "understand_turno_report.md").write_text(
        render(artifact, cells, commands, count["dev"], count["test"]), encoding="utf-8")
    print(f"artefacto {artifact.run_id} escrito en {args.out_dir}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
