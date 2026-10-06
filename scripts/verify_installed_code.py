"""Falla si el código instalado (site-packages) no es byte a byte el del árbol fuente (A11).

Uso en el build de la imagen: `python scripts/verify_installed_code.py /app` (con el venv del proyecto en el
PATH, desde un directorio que no sea la raíz del código, para importar la copia instalada y no la fuente).
Una caché de `uv` obsoleta puede entregar un wheel de fuentes viejas aunque el commit sea nuevo."""

import hashlib
import importlib.util
import sys
from pathlib import Path

PACKAGES = ("agent_core", "agent_telemetry")


def _digest(root: Path) -> dict[str, str]:
    return {str(p.relative_to(root)).replace("\\", "/"): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*.py")) if "__pycache__" not in p.parts}


def mismatches(source_root: Path, installed_root: Path, package: str) -> list[str]:
    """Rutas (relativas al paquete) que faltan, sobran o difieren entre la fuente y lo instalado."""
    src, dst = _digest(source_root / package), _digest(installed_root)
    return sorted(k for k in src.keys() | dst.keys() if src.get(k) != dst.get(k))


def main(argv: list[str]) -> int:
    source_root = Path(argv[1]) if len(argv) > 1 else Path.cwd()
    bad = 0
    for package in PACKAGES:
        spec = importlib.util.find_spec(package)
        if spec is None or not spec.submodule_search_locations:
            print(f"{package}: no está instalado", file=sys.stderr)
            return 1
        installed = Path(next(iter(spec.submodule_search_locations)))
        if installed.resolve() == (source_root / package).resolve():
            print(f"{package}: se importa desde la fuente, no hay instalación que comprobar", file=sys.stderr)
            return 1
        diff = mismatches(source_root, installed, package)
        if diff:
            bad += 1
            print(f"{package}: el código instalado difiere de la fuente en {len(diff)} archivos "
                  f"(p. ej. {diff[0]})", file=sys.stderr)
    if not bad:
        print("código instalado = fuente")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
