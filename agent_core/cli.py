"""CLI `agentcore`. Fase 1: `contracts [--check] [--out DIR]`."""

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from agent_core.contracts import check_contracts, write_contracts


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="agentcore")
    sub = parser.add_subparsers(dest="command", required=True)
    contracts = sub.add_parser("contracts", help="genera o verifica contracts/")
    contracts.add_argument("--check", action="store_true", help="falla si contracts/ está desactualizado")
    contracts.add_argument("--out", type=Path, default=Path("contracts"))
    args = parser.parse_args(argv)
    if args.command == "contracts":
        if args.check:
            diffs = check_contracts(args.out)
            for rel in diffs:
                print(f"contracts desactualizado: {rel} (corre `uv run agentcore contracts`)",
                      file=sys.stderr)
            return 1 if diffs else 0
        write_contracts(args.out)
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
