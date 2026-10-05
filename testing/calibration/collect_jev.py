"""Recolecta las salidas crudas de JEV sobre el conjunto sintético de `understand-turno`.

    AGENTCORE_JEV_API_KEY=... uv run python -m testing.calibration.collect_jev --out raw.jsonl [--limit N]

Una línea JSON por ejemplo (`id`, `split`, `lang`, `value`, `p_raw`, `top_k`, `model_version`, `tokens`, o
`error` con la clase de la falla). Es reanudable: salta los ids que ya están en `--out`. Solo salen
mensajes sintéticos de `testing/calibration`; la clave la lee el transporte por entorno y nunca se imprime
ni se guarda.

Guardar la salida cruda permite recalibrar sin volver a llamar a JEV: `calibrate` corre después, sin red,
sobre un proveedor que reproduce estas respuestas."""

import argparse
import json
import os
import sys
from decimal import Decimal
from pathlib import Path

import yaml

from agent_core.adapters.system_clock import SystemClock
from agent_core.decision import HttpJevTransport, JevProvider, ProviderError, ProviderTimeout
from agent_core.domain import Command, JsonValue, ProviderSpec
from testing.calibration.understand_turno import FLOWS, examples

MODEL_DEF = (Path(__file__).resolve().parents[2]
             / "tests/fixtures/registry-e2e/decision_models/understand-turno@1.0.0.yaml")
INTERRUPTS = ["fraude"]
MAX_CONSECUTIVE_ERRORS = 3


def runtime_schema(flows: list[str], interrupts: list[str]) -> dict[str, JsonValue]:
    """El esquema cerrado que `UnderstandService` arma por release (command, flow, interrupt)."""
    def enum(values: list[str]) -> dict[str, JsonValue]:
        return {"type": "string", "enum": list(values)}

    return {"type": "object", "additionalProperties": False, "required": ["command"],
            "properties": {"command": enum([c.value for c in Command]), "flow": enum(flows),
                           "interrupt": enum(interrupts)}}


def provider_spec(model_def: Path = MODEL_DEF) -> ProviderSpec:
    """El `ProviderSpec` de JEV tal como lo declara el modelo de decisión: preguntas y modelo fijado."""
    model = yaml.safe_load(model_def.read_text(encoding="utf-8"))
    spec = next(p for p in model["providers"] if p["provider"] == "jev")
    return ProviderSpec.model_validate(spec)


def _done(path: Path) -> set[str]:
    if not path.exists():
        return set()
    return {json.loads(line)["id"] for line in path.read_text(encoding="utf-8").splitlines() if line.strip()}


def collect(out: Path, limit: int | None, model_def: Path = MODEL_DEF) -> int:
    key = os.environ.get("AGENTCORE_JEV_API_KEY", "")
    if not key:
        print("falta AGENTCORE_JEV_API_KEY en el entorno", file=sys.stderr)
        return 2
    provider = JevProvider(HttpJevTransport(lambda: key, SystemClock()))
    spec, schema = provider_spec(model_def), runtime_schema(list(FLOWS), INTERRUPTS)
    seen = _done(out)
    todo = [(split, e) for split in ("dev", "test") for e in examples(split) if e.id not in seen]
    todo = todo[:limit] if limit is not None else todo
    errors_in_a_row, written, tokens = 0, 0, 0
    with out.open("a", encoding="utf-8") as sink:
        for split, example in todo:
            row: dict[str, object] = {"id": example.id, "split": split, "lang": example.lang}
            try:
                raw = provider.predict(spec, example.inputs, schema, example.lang)
            except (ProviderError, ProviderTimeout) as exc:
                row["error"] = type(exc).__name__
                errors_in_a_row += 1
            else:
                errors_in_a_row = 0
                tokens += raw.tokens
                row.update(value=raw.value, p_raw={k: None if v is None else str(Decimal(repr(v)))
                                                   for k, v in raw.p_raw.items()},
                           top_k={k: [[label, str(Decimal(repr(p)))] for label, p in v]
                                  for k, v in raw.top_k.items()},
                           model_version=raw.model_version, tokens=raw.tokens)
            sink.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
            sink.flush()
            written += 1
            if errors_in_a_row >= MAX_CONSECUTIVE_ERRORS:
                print(f"parado tras {MAX_CONSECUTIVE_ERRORS} errores seguidos ({row['error']})",
                      file=sys.stderr)
                return 1
    print(f"escritas {written} filas ({len(seen)} ya estaban), {tokens} tokens", file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m testing.calibration.collect_jev")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--model-def", type=Path, default=MODEL_DEF,
                        help="definición del modelo de decisión (por defecto la del fixture registry-e2e)")
    args = parser.parse_args(argv)
    return collect(args.out, args.limit, args.model_def)


if __name__ == "__main__":
    raise SystemExit(main())
