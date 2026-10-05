"""Refresca la copia del contrato del tool-service (`tests/fixtures/tool-service-contract/`).

    uv run python scripts/sync_tool_contract.py [--check] [RUTA_A_TOOL_SERVICE]

Sin ruta busca un `tool-service` junto a agent-core. `--check` no escribe: falla si la copia está vieja.
El tool-service es dueño del contrato; las fixtures del registry E2E se alinean a él y el test
`tests/registry/test_tool_contract_drift.py` falla si se desvían."""

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / "tests" / "fixtures" / "tool-service-contract"


def _sibling() -> Path:
    for parent in ROOT.parents:
        if (parent / "tool-service" / "registry" / "tools").is_dir():
            return parent / "tool-service"
    return ROOT.parent / "tool-service"


def main(argv: list[str]) -> int:
    paths = [a for a in argv if not a.startswith("--")]
    source = Path(paths[0]) if paths else _sibling()
    tools, version = source / "registry" / "tools", source / "contracts" / "tool-provider-version.txt"
    if not tools.is_dir() or not version.is_file():
        print(f"no encuentro el contrato del tool-service en {source}", file=sys.stderr)
        return 2
    wanted = {p.name: p.read_bytes() for p in tools.glob("*.yaml")}
    found = {p.name: p.read_bytes() for p in (DEST / "tools").glob("*.yaml")}
    current = wanted == found and (DEST / version.name).read_bytes() == version.read_bytes()
    if "--check" in argv:
        print("al día" if current else "desactualizado: corre scripts/sync_tool_contract.py")
        return 0 if current else 1
    (DEST / "tools").mkdir(parents=True, exist_ok=True)
    for stale in set(found) - set(wanted):
        (DEST / "tools" / stale).unlink()
    for name in wanted:
        shutil.copyfile(tools / name, DEST / "tools" / name)
    shutil.copyfile(version, DEST / version.name)
    print(f"{len(wanted)} tools copiadas")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
