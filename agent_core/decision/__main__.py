"""Comando del informe de métricas (P8): `python -m agent_core.decision report --artifact <ruta.json>
[--events <ruta.jsonl>] [--format md|json] [--out <ruta>]`.

Código de salida 0; un artefacto o eventos ilegibles dan 2 con un mensaje que no incluye su contenido."""

import argparse
import sys
from pathlib import Path

from agent_core.decision.calibration.artifact import CalibrationArtifact
from agent_core.decision.calibration.report import build_report, load_events, render_json, render_markdown


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m agent_core.decision")
    sub = parser.add_subparsers(dest="command", required=True)
    report = sub.add_parser("report", help="informe de métricas por idioma y proveedor")
    report.add_argument("--artifact", type=Path, required=True)
    report.add_argument("--events", type=Path)
    report.add_argument("--format", choices=("md", "json"), default="md")
    report.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
    try:
        artifact = CalibrationArtifact.from_json(args.artifact.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        print("no se pudo leer el artefacto de calibración", file=sys.stderr)
        return 2
    events = None
    if args.events is not None:
        try:
            events = load_events(args.events.read_text(encoding="utf-8"))
        except (OSError, ValueError, UnicodeDecodeError):
            print("no se pudieron leer los eventos", file=sys.stderr)
            return 2
    data = build_report(artifact, events)
    text = render_json(data) + "\n" if args.format == "json" else render_markdown(data)
    if args.out is not None:
        args.out.write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
