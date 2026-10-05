"""Genera el estado local de `deploy/compose/state/` para una corrida de prueba de `agentcore serve`.

    uv run python scripts/serve_state.py [--state deploy/compose/state] [--data-pipeline RUTA/data]

SOLO DATOS SINTÉTICOS y claves de PRUEBA del repo (`testing.demo_identities`): sirve para ensayar el
contrato de `docs/serve-env.md`, nunca para producción. Escribe `identity-keys.json`, `staff-keys.json`,
`calibration/` (umbrales de demo con idioma `pt` añadido), `classifier/` (artefacto sintético, ver
`limitations`), `field-classification.json` (la última corrida publicada por data-pipeline más el overlay del
motor) y `field-grants.json` (vacío: nadie lee campos; se llena con lo que gobierno de datos conceda). Además
deja en `tokens.json` credenciales de 1 hora."""

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _with_portuguese(artifact_json: str) -> str:
    doc = json.loads(artifact_json)
    seen = {tuple(t["key"]) for t in doc["thresholds"]}
    for t in list(doc["thresholds"]):
        key = list(t["key"])
        if key[-1] == "es" and (*key[:-1], "pt") not in seen:
            doc["thresholds"].append({"key": [*key[:-1], "pt"], "value": t["value"]})
    doc["thresholds"].sort(key=lambda t: t["key"])
    return json.dumps(doc, ensure_ascii=False, sort_keys=True)


def _sibling_data() -> Path:
    for parent in ROOT.parents:
        if (parent / "data-pipeline" / "data" / "publish" / "latest.json").is_file():
            return parent / "data-pipeline" / "data"
    return ROOT.parent / "data-pipeline" / "data"


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, default=ROOT / "deploy" / "compose" / "state")
    parser.add_argument("--data-pipeline", type=Path, default=_sibling_data())
    args = parser.parse_args(argv)
    state: Path = args.state
    (state / "calibration").mkdir(parents=True, exist_ok=True)
    (state / "classifier").mkdir(parents=True, exist_ok=True)

    sys.path.insert(0, str(ROOT))
    from testing.engine_world import demo_calibration
    from testing.serve_classifier import synthetic_classifier_json

    command = [sys.executable, "-m", "testing.demo_identities", "--public-keys",
               str(state / "identity-keys.json"), "--staff-keys", str(state / "staff-keys.json")]
    out = subprocess.run(command, cwd=ROOT, check=True, capture_output=True, text=True)
    (state / "tokens.json").write_text(out.stdout, encoding="utf-8")

    transfer = (ROOT / "tests" / "fixtures" / "registry-e2e" / "calibrations" / "cal-transfer-demo.json")
    (state / "calibration" / "cal-demo.json").write_text(_with_portuguese(demo_calibration().to_json()),
                                                         encoding="utf-8")
    (state / "calibration" / "cal-transfer-demo.json").write_text(
        _with_portuguese(transfer.read_text(encoding="utf-8")), encoding="utf-8")
    (state / "classifier" / "sintetico.json").write_text(synthetic_classifier_json(), encoding="utf-8")

    pointer = json.loads((args.data_pipeline / "publish" / "latest.json").read_text(encoding="utf-8"))
    published = json.loads((args.data_pipeline / pointer["path"] / "field_classification.json")
                           .read_text(encoding="utf-8"))
    overlay = json.loads((ROOT / "scripts" / "e2e" / "field-overlay.json").read_text(encoding="utf-8"))
    (state / "field-classification.json").write_text(json.dumps({**published, **overlay}), encoding="utf-8")
    # Tasas FIJAS e inventadas (USD por unidad), solo para ensayar `convertir_moneda`; las reales: finanzas.
    rates = {"USD": "1", "MXN": "0.055", "COP": "0.00025", "ARS": "0.001", "BRL": "0.19", "PEN": "0.27",
             "CLP": "0.0011", "EUR": "1.08"}
    (state / "fx-rates.json").write_text(json.dumps(rates), encoding="utf-8")
    grants = state / "field-grants.json"
    if not grants.exists():
        grants.write_text("[]", encoding="utf-8")
    print(f"estado en {state}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
